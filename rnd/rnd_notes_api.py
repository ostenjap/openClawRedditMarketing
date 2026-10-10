#!/usr/bin/env python3
"""R&D notes API for the "R&D" sidebar panel (ui.techno-wanderlust.com).

Runs on the host (systemd unit rnd-notes.service). Caddy maps  /rnd-api/*  ->  127.0.0.1:8804/*  (prefix stripped).

Storage: one Markdown file per note in NOTES_DIR (default /root/rnd/notes/<id>.md) with a small front matter
block (title, created, updated), so the notes are plain files that Claude or a human can read and grep later.
Deleting a note moves the file to NOTES_DIR/.trash/ (nothing is ever removed for good from here).

Auth: same as kanban-board: the user's Open WebUI session (Bearer token or `token` cookie) is checked against
Open WebUI (GET /api/v1/auths/) and must be an admin. Writes also need X-Requested-With: rnd + same-origin Origin.

Routes
  GET  /health
  GET  /notes                    list: id, title, snippet, created, updated (newest first)
  GET  /notes/<id>               one note with its full body
  POST /notes  {title, body}     create
  POST /notes/<id>  {title, body, base}   save; base = the `updated` value the editor started from.
                                 409 if the file changed since (another tab), so nothing is overwritten silently.
  POST /notes/<id>/delete        move to .trash/
"""
import fcntl
import hashlib
import json
import os
import re
import secrets
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("RND_HOST", "127.0.0.1")
PORT = int(os.environ.get("RND_PORT", "8804"))
NOTES_DIR = os.environ.get("RND_DIR", "/root/rnd/notes")
OWUI = "http://127.0.0.1:3000"
ORIGIN = "https://ui.techno-wanderlust.com"
AUTH_OFF = os.environ.get("RND_AUTH_OFF") == "1"  # local test instance only (never set in the unit)
MAX_BODY = 512 * 1024
MAX_TITLE = 200
ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")

_auth_cache = {}  # sha256(token) -> (expires_at, ok)


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def token_of(h):
    a = h.headers.get("Authorization", "")
    if a.startswith("Bearer ") and len(a) > 20:
        return a[7:].strip()
    m = re.search(r"(?:^|;\s*)token=([^;]+)", h.headers.get("Cookie", ""))
    return m.group(1) if m else ""


def is_admin(h):
    if AUTH_OFF:
        return True
    tok = token_of(h)
    if not tok:
        return False
    key = hashlib.sha256(tok.encode()).hexdigest()
    hit = _auth_cache.get(key)
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]
    ok = False
    try:
        req = urllib.request.Request(OWUI + "/api/v1/auths/", headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=5) as r:
            ok = json.load(r).get("role") == "admin"
    except Exception:
        ok = False
    if len(_auth_cache) > 200:
        _auth_cache.clear()
    _auth_cache[key] = (now + (60 if ok else 10), ok)
    return ok


# ---------------------------------------------------------------- storage

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def path_of(nid):
    return os.path.join(NOTES_DIR, nid + ".md")


def clean_title(t):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t[:MAX_TITLE]


def parse(text):
    meta, body = {}, text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                k, sep, v = line.partition(":")
                if sep:
                    v = v.strip()
                    try:
                        v = json.loads(v)  # values are written JSON-quoted
                    except ValueError:
                        pass
                    meta[k.strip()] = v
            body = text[end + 5:]
            if body.startswith("\n"):
                body = body[1:]
    return meta, body


def render(title, created, updated, body):
    head = "---\ntitle: %s\ncreated: %s\nupdated: %s\n---\n\n" % (
        json.dumps(title, ensure_ascii=False), created, updated)
    return head + body.replace("\r\n", "\n")


def read_note(nid):
    with open(path_of(nid), encoding="utf-8") as f:
        meta, body = parse(f.read())
    return {
        "id": nid,
        "title": str(meta.get("title") or ""),
        "created": str(meta.get("created") or ""),
        "updated": str(meta.get("updated") or ""),
        "body": body,
    }


def write_atomic(nid, text):
    p = path_of(nid)
    tmp = p + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


class Lock:
    """One writer at a time (threads and any second process)."""

    def __enter__(self):
        self.f = open(os.path.join(NOTES_DIR, ".lock"), "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def snippet(body):
    s = re.sub(r"[#>*_`~\-\[\]]+", " ", body)
    return re.sub(r"\s+", " ", s).strip()[:240]


def list_notes():
    out = []
    for name in os.listdir(NOTES_DIR):
        nid = name[:-3]
        if not name.endswith(".md") or not ID_RE.match(nid):
            continue
        try:
            n = read_note(nid)
        except (OSError, UnicodeDecodeError):
            continue
        out.append({"id": nid, "title": n["title"], "snippet": snippet(n["body"]),
                    "created": n["created"], "updated": n["updated"]})
    out.sort(key=lambda n: n["updated"], reverse=True)
    return {"ok": True, "notes": out}


def create(b):
    nid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    ts = now_iso()
    with Lock():
        write_atomic(nid, render(clean_title(b.get("title")), ts, ts, str(b.get("body") or "")))
    log("create", nid)
    return 200, {"ok": True, "note": read_note(nid)}


def save(nid, b):
    with Lock():
        if not os.path.exists(path_of(nid)):
            return 404, {"ok": False, "error": "Note not found (deleted in another tab?)"}
        cur = read_note(nid)
        base = b.get("base")
        if base and base != cur["updated"]:
            return 409, {"ok": False, "error": "This note was changed somewhere else.", "note": cur}
        title = clean_title(b["title"]) if "title" in b else cur["title"]
        body = str(b["body"]) if "body" in b else cur["body"]
        write_atomic(nid, render(title, cur["created"] or now_iso(), now_iso(), body))
    return 200, {"ok": True, "note": read_note(nid)}


def trash(nid):
    with Lock():
        if not os.path.exists(path_of(nid)):
            return 404, {"ok": False, "error": "Note not found"}
        tdir = os.path.join(NOTES_DIR, ".trash")
        os.makedirs(tdir, exist_ok=True)
        os.replace(path_of(nid), os.path.join(tdir, nid + ".md"))
    log("trash", nid)
    return 200, {"ok": True}


# ---------------------------------------------------------------- http

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "rnd-notes"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _guard(self, mutating):
        if not is_admin(self):
            self._send(401, {"ok": False, "error": "Sign in to Proxious as an admin"})
            return False
        if mutating and not AUTH_OFF:
            origin = self.headers.get("Origin")
            if (origin and origin != ORIGIN) or self.headers.get("X-Requested-With") != "rnd":
                self._send(403, {"ok": False, "error": "Blocked request"})
                return False
        return True

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Note too large (max 512 KB)")
        b = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(b, dict):
            raise ValueError("bad body")
        return b

    def _route(self):
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if not parts or parts[0] != "notes":
            return None, None
        nid = parts[1] if len(parts) > 1 else None
        if nid is not None and not ID_RE.match(nid):
            return "bad", None
        return nid, parts[2] if len(parts) > 2 else None

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            return self._send(200, {"ok": True})
        if not self._guard(False):
            return
        nid, sub = self._route()
        try:
            if nid is None and self.path.split("?")[0].rstrip("/") == "/notes":
                return self._send(200, list_notes())
            if nid and nid != "bad" and sub is None:
                if not os.path.exists(path_of(nid)):
                    return self._send(404, {"ok": False, "error": "Note not found"})
                return self._send(200, {"ok": True, "note": read_note(nid)})
            return self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:  # noqa: BLE001
            log("GET error", self.path, repr(e))
            return self._send(500, {"ok": False, "error": "Server error"})

    def do_POST(self):
        if not self._guard(True):
            return
        nid, sub = self._route()
        try:
            if nid == "bad":
                return self._send(404, {"ok": False, "error": "not found"})
            if nid is None and self.path.split("?")[0].rstrip("/") == "/notes":
                return self._send(*create(self._json()))
            if nid and sub is None:
                return self._send(*save(nid, self._json()))
            if nid and sub == "delete":
                return self._send(*trash(nid))
            return self._send(404, {"ok": False, "error": "not found"})
        except ValueError as e:
            return self._send(400, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            log("POST error", self.path, repr(e))
            return self._send(500, {"ok": False, "error": "Server error"})


if __name__ == "__main__":
    os.makedirs(NOTES_DIR, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True
    log("rnd-notes api on", HOST, PORT, "dir", NOTES_DIR, "auth_off=%s" % AUTH_OFF)
    srv.serve_forever()

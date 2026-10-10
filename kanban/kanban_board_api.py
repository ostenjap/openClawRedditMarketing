#!/usr/local/lib/hermes-agent/venv/bin/python
"""Kanban board API for the "Kanban" sidebar panel (ui.techno-wanderlust.com).

Runs on the host (systemd unit kanban-board.service) because the Open WebUI container
cannot see kanban.db. Caddy maps  /kanban-api/*  ->  127.0.0.1:8802/*  (prefix stripped).

Auth: every request must carry the user's Open WebUI session (Authorization: Bearer <jwt> or the
`token` cookie). It is validated by asking Open WebUI itself (GET /api/v1/auths/) and must be an
admin. State-changing requests also need the X-Requested-With header (CSRF) and a same-origin Origin.

Board changes go through hermes_cli.kanban_db's own transition functions, so events, runs and
the dispatcher stay consistent. Running cards are never moved or stopped from here.

Routes
  GET  /health
  GET  /board                 columns + workers
  GET  /card/<id>             body, comments, runs, events
  POST /move     {id, to}     to: parked | ready | blocked | done | archive
  POST /create   {title, body, assignee, park}
  POST /comment  {id, body}
  GET  /archive               archived cards (newest first, max 200)
  POST /restore  {id}         archived -> Done (if it was finished) or Parked
  POST /chat     {message, session}   streams Hermes' reply (SSE passthrough)
  POST /automation {kind, id, action} Mission panel pause/play. kind hermes -> `hermes cron pause|resume`;
                                      kind system -> comments the crontab line out (mission_collect.PAUSE_MARK).
                                      Re-runs mission_collect.py so the panel sees the change at once.
  GET  /notifications             Notifications panel: `ceo alerts --json` (budget + CEO alerts, read/dismiss state)
  POST /notifications {action, key | all}   action: read | dismiss (added 2026-10-10)
"""
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/usr/local/lib/hermes-agent")
from hermes_cli import kanban_db as kb  # noqa: E402

HOST = os.environ.get("KANBAN_HOST", "127.0.0.1")
PORT = int(os.environ.get("KANBAN_PORT", "8802"))
OWUI = "http://127.0.0.1:3000"
HERMES = "http://127.0.0.1:8642"
ORIGIN = "https://ui.techno-wanderlust.com"
CHAT_ID = "744825544"  # user's Telegram DM (completion pings)
CREW = ("scout", "scribe", "ops", "analyst")
DEFAULT_MAX_RUNTIME = 1200
DEFAULT_MAX_RETRIES = 1
AUTH_OFF = os.environ.get("KANBAN_AUTH_OFF") == "1"  # test instance only (never set in the unit)
MAX_BODY = 64 * 1024

COLUMN_OF = {
    "triage": "parked", "scheduled": "parked",
    "todo": "ready", "ready": "ready",
    "running": "running",
    "blocked": "blocked",
    "done": "done",
}
DONE_LIMIT = 30

_auth_cache = {}  # sha256(token) -> (expires_at, ok)


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def hermes_key():
    try:
        for line in open("/root/.hermes/.env"):
            if line.startswith("API_SERVER_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


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


def to_dict(o):
    d = dict(o.__dict__) if hasattr(o, "__dict__") else dict(o)
    return d


def task_card(conn, t, detail=False):
    summary = None
    if t.status in ("done", "blocked", "running", "scheduled"):
        try:
            summary = kb.latest_summary(conn, t.id)
        except Exception:
            summary = None
    if not summary and t.result:
        summary = str(t.result)
    c = {
        "id": t.id, "title": t.title, "assignee": t.assignee, "status": t.status,
        "column": COLUMN_OF.get(t.status, t.status),
        "created_at": t.created_at, "started_at": t.started_at, "completed_at": t.completed_at,
        "failures": t.consecutive_failures or 0, "max_runtime": t.max_runtime_seconds,
        "summary": (summary or "")[:400],
        "last_error": (t.last_failure_error or "")[:300],
    }
    if detail:
        c["body"] = t.body or ""
        c["result"] = str(t.result or "")[:4000]
        c["max_retries"] = t.max_retries
        c["created_by"] = t.created_by
    return c


def board():
    conn = kb.connect()
    try:
        tasks = kb.list_tasks(conn, include_archived=False)
        cols = {k: [] for k in ("parked", "ready", "running", "blocked", "done")}
        for t in tasks:
            col = COLUMN_OF.get(t.status)
            if col:
                cols[col].append(t)
        cols["done"].sort(key=lambda t: t.completed_at or 0, reverse=True)
        cols["done"] = cols["done"][:DONE_LIMIT]
        for k in ("parked", "ready", "running", "blocked"):
            cols[k].sort(key=lambda t: (-(t.priority or 0), t.created_at or 0))
        out = {k: [task_card(conn, t) for t in v] for k, v in cols.items()}
        busy = {c["assignee"]: c for c in out["running"]}
        workers = [{"name": w, "busy": w in busy, "task": busy[w]["id"] if w in busy else None,
                    "title": busy[w]["title"] if w in busy else None} for w in CREW[:3]]
        n_arch = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='archived'").fetchone()[0]
        return {"ok": True, "now": int(time.time()), "columns": out, "workers": workers,
                "cap": 3, "total_done": len([t for t in tasks if t.status == "done"]), "archived": n_arch}
    finally:
        conn.close()


def card_detail(task_id):
    conn = kb.connect()
    try:
        t = kb.get_task(conn, task_id)
        if not t:
            return None
        c = task_card(conn, t, detail=True)
        c["comments"] = [{"author": x.author, "body": x.body[:2000], "at": x.created_at}
                         for x in kb.list_comments(conn, task_id)][-30:]
        c["runs"] = [{"profile": r.profile, "outcome": r.outcome or r.status, "started": r.started_at,
                      "ended": r.ended_at, "summary": (r.summary or "")[:600], "error": (r.error or "")[:400]}
                     for r in kb.list_runs(conn, task_id)][-10:]
        c["events"] = [{"kind": e.kind, "at": e.created_at} for e in kb.list_events(conn, task_id)][-25:]
        return c
    finally:
        conn.close()


def archive_list():
    conn = kb.connect()
    try:
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status='archived' "
            "ORDER BY COALESCE((SELECT MAX(created_at) FROM task_events e WHERE e.task_id=tasks.id AND e.kind='archived'),"
            " completed_at, created_at) DESC LIMIT 200").fetchall()
        out = []
        for (tid,) in rows:
            t = kb.get_task(conn, tid)
            if not t:
                continue
            c = task_card(conn, t)
            ev = conn.execute("SELECT MAX(created_at) FROM task_events WHERE task_id=? AND kind='archived'", (tid,)).fetchone()[0]
            c["archived_at"] = ev or t.completed_at or t.created_at
            c["finished"] = bool(t.completed_at)
            out.append(c)
        return {"ok": True, "cards": out}
    finally:
        conn.close()


def restore(task_id):
    """archived -> done (it had finished) or parked. The library has no unarchive, so this is a
    guarded status flip plus an audit event; it never touches active cards."""
    conn = kb.connect()
    try:
        t = kb.get_task(conn, task_id)
        if not t:
            return 404, "Card not found", None
        if t.status != "archived":
            return 409, "Card isn't archived", t.status
        new = "done" if t.completed_at else "scheduled"
        with kb.write_txn(conn):
            cur = conn.execute("UPDATE tasks SET status=? WHERE id=? AND status='archived'", (new, task_id))
            if cur.rowcount != 1:
                return 409, "Couldn't restore it", t.status
            kb._append_event(conn, task_id, "restored", {"to": new})
        return 200, "Restored to Done" if new == "done" else "Restored to Parked", new
    finally:
        conn.close()


def move(task_id, to):
    """Returns (http_status, message, new_status)."""
    conn = kb.connect()
    try:
        t = kb.get_task(conn, task_id)
        if not t:
            return 404, "Card not found", None
        st = t.status
        if st == "archived":
            return 409, "Card is archived", st
        if st == "running":
            return 409, "A worker is running this card. Wait for it to finish; it can't be moved.", st
        if to == "archive":
            return (200, "Archived", "archived") if kb.archive_task(conn, task_id) else (409, "Couldn't archive", st)
        if to == "parked":
            if st in ("scheduled", "triage"):
                return 200, "Already parked", st
            if st in ("todo", "ready", "blocked"):
                ok = kb.schedule_task(conn, task_id, reason="Parked from the Kanban panel")
                return (200, "Parked", "scheduled") if ok else (409, "Couldn't park it", st)
            return 409, "Finished cards can't be parked", st
        if to == "ready":
            if st in ("ready", "todo"):
                return 200, "Already queued", st
            if st in ("scheduled", "blocked"):
                ok = kb.unblock_task(conn, task_id)
                return (200, "Queued — a worker picks it up within ~30 s", "ready") if ok else (409, "Couldn't queue it", st)
            return 409, "Finished cards can't be re-queued from here", st
        if to == "blocked":
            if st == "blocked":
                return 200, "Already on hold", st
            if st == "ready":
                ok = kb.block_task(conn, task_id, reason="Put on hold from the Kanban panel")
                return (200, "On hold", "blocked") if ok else (409, "Couldn't hold it", st)
            return 409, "Only queued cards can be put on hold", st
        if to == "done":
            if st == "done":
                return 200, "Already done", st
            if st in ("blocked", "scheduled"):
                if not kb.unblock_task(conn, task_id):
                    return 409, "Couldn't update the card", st
                st = "ready"
            if st == "ready":
                ok = kb.complete_task(conn, task_id, result="Marked done by hand from the Kanban panel")
                return (200, "Marked done", "done") if ok else (409, "Couldn't mark it done (a worker may have just claimed it)", st)
            return 409, "This card is waiting on other cards first", st
        return 400, "Unknown column", st
    finally:
        conn.close()


def create(title, body, assignee, park):
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or len(title) > 200:
        return 400, "Title is required (max 200 characters)", None
    if len(body) > 8000:
        return 400, "Details are too long (max 8000 characters)", None
    if assignee not in CREW:
        return 400, "Pick a worker", None
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title=title, body=body or None, assignee=assignee, created_by="user",
                             max_runtime_seconds=DEFAULT_MAX_RUNTIME, max_retries=DEFAULT_MAX_RETRIES)
        try:
            kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id=CHAT_ID, notifier_profile="default")
        except Exception as e:
            log("notify sub failed", tid, e)
        if park:
            kb.schedule_task(conn, tid, reason="Created parked from the Kanban panel")
        return 200, "Created", tid
    finally:
        conn.close()


def add_comment(task_id, body):
    body = (body or "").strip()
    if not body or len(body) > 4000:
        return 400, "Comment is empty or too long"
    conn = kb.connect()
    try:
        if not kb.get_task(conn, task_id):
            return 404, "Card not found"
        kb.add_comment(conn, task_id, "user", body)
        return 200, "Added"
    finally:
        conn.close()


# ------------------------------------------------------------------ Mission automations pause/play
COLLECTOR = "/root/openwebui-fork/mission_collect.py"
JOBS_FILE = "/root/.hermes/cron/jobs.json"
CRON_LOCK = "/root/openwebui-fork/.automation-toggle.lock"
CRONTAB_BAK = "/root/openwebui-fork/crontab.bak-mission-toggle"  # crontab as it was before the last toggle


def _collector():
    spec = importlib.util.spec_from_file_location("mission_collect", COLLECTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # defines things only; main() is behind __name__ guard
    return mod


def _toggle_hermes(job_id, pause):
    if not re.fullmatch(r"[0-9a-f]{6,32}", job_id):
        return 400, "Unknown job"
    try:
        data = json.load(open(JOBS_FILE))
        jobs = data if isinstance(data, list) else data.get("jobs", [])
    except (OSError, ValueError):
        return 500, "Could not read Hermes jobs"
    if not any(j.get("id") == job_id for j in jobs):
        return 404, "Job not found"
    r = subprocess.run(["/usr/local/bin/hermes", "cron", "pause" if pause else "resume", job_id],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return 500, "hermes cron failed: " + (r.stderr or r.stdout).strip()[-200:]
    return 200, "Paused" if pause else "Resumed"


def _toggle_system(cid, pause):
    mc = _collector()
    match = next((m for c, _n, _s, _h, _l, m in mc.SYSTEM_CRONS if c == cid), None)
    if not match:
        return 404, "Job not found"
    mark = mc.PAUSE_MARK
    with open(CRON_LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        if cur.returncode != 0:
            return 500, "Could not read crontab"
        lines, hits = cur.stdout.splitlines(), 0
        for i, line in enumerate(lines):
            if match not in line:
                continue
            if pause and not line.lstrip().startswith("#"):
                lines[i] = mark + line
                hits += 1
            elif not pause and line.startswith(mark):
                lines[i] = line[len(mark):]
                hits += 1
        if hits != 1:
            return 409, "Already " + ("paused" if pause else "running") if hits == 0 else "Ambiguous crontab line"
        with open(CRONTAB_BAK, "w") as f:
            f.write(cur.stdout)
        w = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=10)
        if w.returncode != 0:
            return 500, "Could not write crontab: " + w.stderr.strip()[-200:]
    return 200, "Paused" if pause else "Resumed"


def toggle_automation(kind, aid, action):
    if action not in ("pause", "resume"):
        return 400, "Bad action"
    pause = action == "pause"
    if kind == "hermes":
        code, msg = _toggle_hermes(aid, pause)
    elif kind == "system":
        code, msg = _toggle_system(aid, pause)
    else:
        return 400, "Bad kind"
    if code == 200:  # refresh the Mission snapshot now instead of waiting for the */5 cron
        subprocess.run(["/usr/bin/python3", COLLECTOR], capture_output=True, timeout=60)
    return code, msg


CEO = "/usr/local/bin/ceo"
ALERT_KEY = re.compile(r"[a-z]+:[A-Za-z0-9:_.\-]{1,120}")


def notifications():
    r = subprocess.run([CEO, "alerts", "--json"], capture_output=True, text=True, timeout=45)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-200:] or "ceo alerts failed")
    return json.loads(r.stdout)


def notification_action(action, key, all_):
    if action not in ("read", "dismiss"):
        return 400, "Bad action"
    args = [CEO, "alerts", action]
    if all_:
        args.append("--all")
    elif ALERT_KEY.fullmatch(key or ""):
        args.append(key)
    else:
        return 400, "Bad alert key"
    r = subprocess.run(args + ["--json"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return 404 if "no alert" in r.stdout else 500, (r.stdout or r.stderr).strip()[-200:]
    return 200, "OK"


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "kanban-board"

    def log_message(self, fmt, *a):  # quiet; one line per mutating call is logged explicitly
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
            if (origin and origin != ORIGIN) or self.headers.get("X-Requested-With") != "kanban":
                self._send(403, {"ok": False, "error": "Blocked request"})
                return False
        return True

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            return self._send(200, {"ok": True})
        if not self._guard(False):
            return
        try:
            if path == "/board":
                return self._send(200, board())
            if path == "/archive":
                return self._send(200, archive_list())
            if path == "/notifications":
                return self._send(200, notifications())
            m = re.fullmatch(r"/card/(t_[0-9a-f]{4,16})", path)
            if m:
                d = card_detail(m.group(1))
                return self._send(200, {"ok": True, "card": d}) if d else self._send(404, {"ok": False, "error": "Card not found"})
            self._send(404, {"ok": False, "error": "Not found"})
        except Exception as e:
            log("GET error", path, repr(e))
            self._send(500, {"ok": False, "error": "Board unavailable: " + str(e)[:120]})

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._guard(True):
            return
        try:
            b = self._json()
            if path == "/move":
                code, msg, st = move(str(b.get("id", "")), str(b.get("to", "")))
                log("move", b.get("id"), "->", b.get("to"), code)
                return self._send(code, {"ok": code == 200, "message": msg, "status": st})
            if path == "/create":
                code, msg, tid = create(b.get("title"), b.get("body"), b.get("assignee"), bool(b.get("park")))
                log("create", tid, b.get("assignee"), code)
                return self._send(code, {"ok": code == 200, "message": msg, "id": tid})
            if path == "/restore":
                code, msg, st = restore(str(b.get("id", "")))
                log("restore", b.get("id"), code)
                return self._send(code, {"ok": code == 200, "message": msg, "status": st})
            if path == "/comment":
                code, msg = add_comment(str(b.get("id", "")), b.get("body"))
                return self._send(code, {"ok": code == 200, "message": msg})
            if path == "/automation":
                code, msg = toggle_automation(str(b.get("kind", "")), str(b.get("id", "")), str(b.get("action", "")))
                log("automation", b.get("kind"), b.get("id"), b.get("action"), code)
                return self._send(code, {"ok": code == 200, "message": msg})
            if path == "/notifications":
                code, msg = notification_action(str(b.get("action", "")), str(b.get("key", "")), b.get("all") is True)
                log("notifications", b.get("action"), b.get("key") or ("all" if b.get("all") else ""), code)
                return self._send(code, {"ok": code == 200, "message": msg})
            if path == "/chat":
                return self._chat(b)
            self._send(404, {"ok": False, "error": "Not found"})
        except ValueError as e:
            self._send(400, {"ok": False, "error": str(e)})
        except Exception as e:
            log("POST error", path, repr(e))
            self._send(500, {"ok": False, "error": "Failed: " + str(e)[:120]})

    def _chat(self, b):
        msg = str(b.get("message", "")).strip()
        sess = re.sub(r"[^a-zA-Z0-9_-]", "", str(b.get("session", "")))[:48] or "default"
        if not msg or len(msg) > 8000:
            return self._send(400, {"ok": False, "error": "Message is empty or too long"})
        key = hermes_key()
        if not key:
            return self._send(500, {"ok": False, "error": "Hermes API key not found"})
        payload = json.dumps({"model": "hermes-agent", "stream": True,
                              "messages": [{"role": "user", "content": msg}]}).encode()
        req = urllib.request.Request(
            HERMES + "/v1/chat/completions", data=payload, method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                     "X-Hermes-Session-Id": "kanban-panel-" + sess})
        try:
            up = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            return self._send(502, {"ok": False, "error": "Hermes answered %s" % e.code})
        except Exception as e:
            return self._send(502, {"ok": False, "error": "Hermes unreachable: " + str(e)[:100]})
        log("chat", sess, len(msg), "chars")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while True:
                chunk = up.readline()
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            up.close()


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True
    log("kanban-board api on", HOST, PORT, "auth_off=%s" % AUTH_OFF)
    srv.serve_forever()

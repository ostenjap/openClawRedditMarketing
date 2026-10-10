#!/usr/bin/env python3
"""Login gate for the Hermes dashboard (hermes.178-105-191-249.sslip.io).

Caddy forward_auth -> GET /_gate/check. Valid session cookie -> 204, else 302 to /_gate/login.
Login = Open WebUI credentials, verified against 127.0.0.1:3000/api/v1/auths/signin, admin role only.
The OWUI token is discarded; we issue our own HMAC-signed, HttpOnly, Secure, SameSite=Strict cookie.
"""
import hashlib, hmac, html, json, os, secrets, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OWUI = "http://127.0.0.1:3000"
PORT = 8803
COOKIE = "hermes_gate"
TTL = 12 * 3600
SECRET_FILE = "/root/hermes-dashboard-gate/secret"
PUBLIC_HOST = os.environ.get("GATE_HOST", "hermes.178-105-191-249.sslip.io")

def _secret():
    try:
        return open(SECRET_FILE, "rb").read()
    except FileNotFoundError:
        os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
        s = secrets.token_bytes(32)
        fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, s); os.close(fd)
        return s
SECRET = _secret()
fails = {}  # ip -> [count, window_start]

def valid(cookie):
    try:
        exp, _, mac = cookie.split(".")
        if int(exp) < time.time():
            return False
        # who is not recoverable from the cookie; mac covers exp only plus fixed marker
        return hmac.compare_digest(mac, hmac.new(SECRET, f"{exp}|admin".encode(), hashlib.sha256).hexdigest())
    except Exception:
        return False

def make_cookie():
    exp = int(time.time()) + TTL
    return f"{exp}.x.{hmac.new(SECRET, f'{exp}|admin'.encode(), hashlib.sha256).hexdigest()}"

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Proxious dashboard</title><link rel=icon type=image/png href=https://ui.techno-wanderlust.com/static/favicon-96x96.png><style>
body{{font:15px system-ui;background:#0e0e10;color:#eee;display:grid;place-items:center;min-height:100vh;margin:0}}
form{{background:#1a1a1f;padding:28px;border-radius:12px;width:min(340px,86vw);display:grid;gap:12px}}
h1{{font-size:17px;margin:0 0 4px}}input,button{{font:inherit;padding:10px;border-radius:8px;border:1px solid #333;background:#101014;color:#eee}}
button{{background:#3b82f6;border:0;cursor:pointer}}.e{{color:#f87171;font-size:13px}}small{{color:#888}}</style>
<form method=post action=/_gate/login><img src=https://ui.techno-wanderlust.com/static/proxious-mark-dark.svg alt="" width=64 height=64 style="justify-self:center"><h1>Proxious dashboard</h1><small>Sign in with your Proxious admin account</small>
{err}<input name=email type=email placeholder=Email required autofocus><input name=password type=password placeholder=Password required>
<button>Sign in</button></form>"""

class H(BaseHTTPRequestHandler):
    server_version = "gate"
    def log_message(self, *a): pass
    def _cookie(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE: return v
        return ""
    def _send(self, code, body=b"", headers=()):
        self.send_response(code)
        for k, v in headers: self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)
    def _login_page(self, err="", code=200):
        e = f'<div class=e>{html.escape(err)}</div>' if err else ""
        self._send(code, PAGE.format(err=e).encode(), [("Content-Type", "text/html; charset=utf-8")])
    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/_gate/check":
            if valid(self._cookie()):
                # Caddy rewrites Origin for the backend, so enforce same-site on mutating calls here.
                m = self.headers.get("X-Forwarded-Method", "GET")
                o = self.headers.get("Origin", "")
                if m not in ("GET", "HEAD", "OPTIONS") and o and urllib.parse.urlparse(o).netloc != PUBLIC_HOST:
                    return self._send(403, b"bad origin")
                return self._send(204)
            return self._send(302, b"", [("Location", "/_gate/login")])
        if p == "/_gate/login": return self._login_page()
        if p == "/_gate/logout":
            return self._send(302, b"", [("Location", "/_gate/login"), ("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict")])
        self._send(404)
    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/_gate/login": return self._send(404)
        ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        c = fails.get(ip, [0, time.time()])
        if time.time() - c[1] > 900: c = [0, time.time()]
        if c[0] >= 5: return self._login_page("Too many attempts, try again in 15 minutes.", 429)
        n = min(int(self.headers.get("Content-Length", 0)), 4096)
        f = urllib.parse.parse_qs(self.rfile.read(n).decode())
        email, pw = f.get("email", [""])[0], f.get("password", [""])[0]
        ok = False
        try:
            req = urllib.request.Request(OWUI + "/api/v1/auths/signin", data=json.dumps({"email": email, "password": pw}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                ok = json.load(r).get("role") == "admin"
        except Exception:
            ok = False
        if not ok:
            fails[ip] = [c[0] + 1, c[1]]
            return self._login_page("Invalid credentials or not an admin.", 401)
        fails.pop(ip, None)
        self._send(302, b"", [("Location", "/"), ("Set-Cookie", f"{COOKIE}={make_cookie()}; Path=/; Max-Age={TTL}; Secure; HttpOnly; SameSite=Strict")])

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

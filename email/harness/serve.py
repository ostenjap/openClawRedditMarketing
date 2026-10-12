"""Local harness: serves the repo's email/ folder and proxies /mail-api/* to a mail_send_api.py in test mode
(MAIL_AUTH_OFF=1, MAIL_DRY_RUN=1: nothing is delivered, sends are only logged, the CRM is not touched).
  serve.py 8768          starts a local backend (no Jimmy: the llm-gateway only exists on the box)
  serve.py 8768 remote   uses a test instance on the box through  ssh -N -L 8896:127.0.0.1:8896 root@178.105.191.249
                         (start it there with MAIL_HOME=/tmp/mailtest MAIL_PORT=8896 MAIL_AUTH_OFF=1 MAIL_DRY_RUN=1)"""
import atexit, http.server, os, subprocess, sys, tempfile, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP_PORT = 8895
UP = "http://127.0.0.1:%d" % UP_PORT
tmp = tempfile.mkdtemp(prefix="mailharness-")
env = dict(os.environ, MAIL_AUTH_OFF="1", MAIL_DRY_RUN="1", MAIL_PORT=str(UP_PORT),
           MAIL_CONFIG=os.path.join(tmp, "smtp.env"), MAIL_SENT_LOG=os.path.join(tmp, "sent.jsonl"))
if "remote" in sys.argv[2:]:
    UP = "http://127.0.0.1:8896"
else:
    api = subprocess.Popen([sys.executable, os.path.join(ROOT, "mail_send_api.py")], env=env)
    atexit.register(api.terminate)
    time.sleep(0.5)


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def _proxy(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n) if n else None
        req = urllib.request.Request(UP + self.path[len("/mail-api"):], data=data, method=self.command,
                                     headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
        try:
            r = urllib.request.urlopen(req, timeout=120)
            code, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/mail-api/"):
            return self._proxy()
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/mail-api/"):
            return self._proxy()
        self.send_error(405)


port = int(sys.argv[1]) if len(sys.argv) > 1 else 8768
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()

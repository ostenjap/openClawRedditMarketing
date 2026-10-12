"""Local harness for the LinkedIn Drafter panel: the REAL router (linkedin_drafter_router.py) in its fake-LLM mode
(DRAFTER_FAKE_LLM=1, no gateway, no cost) behind a stub auth, plus the real crons-tab.js + drafter-panel.js.
  python drafter/harness/serve.py 8769      -> http://127.0.0.1:8769/harness/harness.html  (?dark for dark mode)
GET /debug/prompts shows the last LLM prompts the pipeline built, to check what the writer was told."""
import os, sys, tempfile, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DRAFTER_FAKE_LLM", "1")
os.environ.setdefault("DRAFTER_DATA_DIR", tempfile.mkdtemp(prefix="drafterharness-"))

class _User:  # what get_verified_user returns inside Open WebUI
    id = "harness-user"

auth = types.ModuleType("open_webui.utils.auth")
auth.get_verified_user = lambda: _User()
for name in ("open_webui", "open_webui.utils"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["open_webui.utils.auth"] = auth
sys.path.insert(0, ROOT)

import linkedin_drafter_router as R  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
import uvicorn  # noqa: E402

PROMPTS = []
_orig = R.gw_complete
def _spy(system, user, **kw):
    PROMPTS.append({"tag": kw.get("tag"), "system": system[-900:], "user": user[:8000]})
    del PROMPTS[:-40]
    res = _orig(system, user, **kw)
    if str(kw.get('tag', '')).startswith('write') and len(res['text']) < 200:  # fake angle 4 is deliberately too short
        res = dict(res, text=res['text'] + ' ' + 'Padding so the harness run can finish. ' * 8)
    return res
R.gw_complete = _spy

app = FastAPI()
app.include_router(R.router, prefix="/api/v1/linkedin-drafter")
app.get("/debug/prompts")(lambda: PROMPTS)
app.mount("/", StaticFiles(directory=ROOT), name="static")

if __name__ == "__main__":
    print("data dir:", os.environ["DRAFTER_DATA_DIR"])
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8769, log_level="warning")

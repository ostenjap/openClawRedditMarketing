"""Patch Open WebUI's env.py: default WEBUI_NAME 'Proxious' and no ' (Open WebUI)' suffix.
Allowed by the Open WebUI LICENSE section 4(i) while the deployment has <= 50 end users per rolling 30 days.
Usage: python3 patch_env_name.py <env.py>   (idempotent)"""
import sys

p = sys.argv[1]
s = open(p).read()
old = ("WEBUI_NAME = os.getenv('WEBUI_NAME', 'Open WebUI')\n"
       "if WEBUI_NAME != 'Open WebUI':\n"
       "    WEBUI_NAME += ' (Open WebUI)'")
new = ("# Proxious rebrand (2026-10-10): allowed by LICENSE section 4(i) while this deployment has <= 50 end users\n"
       "# per rolling 30 days. Revert to the upstream lines (env.py.orig-20261010) if that ever stops being true.\n"
       "WEBUI_NAME = os.getenv('WEBUI_NAME', 'Proxious')")
if "WEBUI_NAME = os.getenv('WEBUI_NAME', 'Proxious')" in s:
    print("already patched")
else:
    assert s.count(old) == 1, "anchor not found"
    open(p, "w").write(s.replace(old, new))
    print("patched")

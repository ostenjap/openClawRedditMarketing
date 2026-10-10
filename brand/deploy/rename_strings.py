"""Proxious rename of user-visible strings in the ui.techno-wanderlust.com add-ons (2026-10-10).
Only display text changes: ids, routes, service names, the `hermes` CLI and the model id stay as they are.
Exact replacements with expected counts; backs up each changed file as <file>.bak-rename-20261010. Idempotent."""
import shutil
import sys

CT = "/root/openwebui-fork/crons-tab/"
RULES = {
    CT + "crons-tab.js": [
        ("'Scheduled Hermes agent jobs will show up here.'", "'Scheduled Proxious jobs will show up here.'", 1),
        ("'Open WebUI'", "'Proxious'", 1),
    ],
    CT + "kanban-panel.js": [
        ("Sign in to Open WebUI as an admin to use the board.", "Sign in to Proxious as an admin to use the board.", 1),
        ("This is the same Hermes agent as on Telegram", "This is the same Proxious agent as on Telegram", 1),
        ("'Lost connection to Hermes.'", "'Lost connection to Proxious.'", 1),
        ("'Ask Hermes'", "'Ask Proxious'", 2),
        ("'Chat with Hermes'", "'Chat with Proxious'", 1),
        ("e('b', null, 'Hermes')", "e('b', null, 'Proxious')", 1),
        ("'Message Hermes…'", "'Message Proxious…'", 1),
        ("'Message Hermes'", "'Message Proxious'", 1),
    ],
    CT + "mission-panel.js": [("'Recent errors (Hermes)'", "'Recent errors (Proxious)'", 1)],
    CT + "drafter-panel.js": [("drafted from what Hermes knows about you", "drafted from what Proxious knows about you", 1)],
    CT + "notifications-panel.js": [("'Sign in to Open WebUI as an admin'", "'Sign in to Proxious as an admin'", 1)],
    "/root/kanban-board/kanban_board_api.py": [("Sign in to Open WebUI as an admin", "Sign in to Proxious as an admin", 1)],
}

failed = False
for path, rules in RULES.items():
    s = open(path, encoding="utf-8").read()
    out, changed = s, 0
    for old, new, n in rules:
        have = out.count(old)
        if have == 0 and new in out:
            continue  # already renamed
        if have != n:
            print(f"✘ {path}: expected {n}x {old!r}, found {have}")
            failed = True
            continue
        out = out.replace(old, new)
        changed += have
    if changed and not failed:
        shutil.copy2(path, path + ".bak-rename-20261010")
        open(path, "w", encoding="utf-8").write(out)
    print(f"{'✔' if not failed else '·'} {path}: {changed} replacement(s)")
sys.exit(1 if failed else 0)

"""One-off patch: add the CEO to /root/openwebui-fork/mission_collect.py (automations + pseudo crew card). Idempotent.
Usage: python3 patch_mission_collect.py <ceo-operate job id> <ceo-meeting job id>"""
import sys

p = "/root/openwebui-fork/mission_collect.py"
s = open(p).read()
if "def ceo():" in s:
    raise SystemExit("already patched")
op_id, meet_id = sys.argv[1], sys.argv[2]


def rep(old, new):
    global s
    assert s.count(old) == 1, old[:60]
    s = s.replace(old, new)


rep('''    ("samatha-remind", "Samatha analytics reminder", "Mon 07:00", 168, "/root/samatha/remind.log", "samatha remind"),
]''', '''    ("samatha-remind", "Samatha analytics reminder", "Mon 07:00", 168, "/root/samatha/remind.log", "samatha remind"),
    ("ceo-brief", "CEO morning brief", "weekdays 08:30", 24, "/root/ceo/cron.log", "ceo brief"),
    ("ceo-checkin", "CEO evening check-in", "weekdays 19:00", 24, "/root/ceo/cron.log", "ceo checkin"),
    ("ceo-scorecard", "CEO Friday scorecard", "Fri 17:00", 168, "/root/ceo/cron.log", "ceo scorecard"),
    ("ceo-standups", "Crew standups for the agents meeting", "Mon 07:00", 168, "/root/ceo/cron.log", "ceo standups"),
    ("ceo-dispatch", "CEO dispatch (proposals, undo, standing orders)", "hourly", 2, "/root/ceo/cron.log", "ceo dispatch"),
]''')
rep('''    "samatha-remind": ("samatha remind", "Telegram via @Samatha bot"),
}''', '''    "samatha-remind": ("samatha remind", "Telegram via @Samatha bot"),
    "ceo-brief": ("ceo brief (no AI)", "Telegram via Plank"),
    "ceo-checkin": ("ceo checkin (no AI)", "Telegram via Plank"),
    "ceo-scorecard": ("ceo scorecard (no AI)", "Telegram via Plank"),
    "ceo-standups": ("ceo standups -> scout/scribe/ops kanban cards", "/root/ceo/inbox/standup-*.md"),
    "ceo-dispatch": ("ceo dispatch (no AI; runs standing orders as crew cards)", "Telegram via Plank (only when something happened)"),
}''')
rep('''PURPOSE = {
''', '''PURPOSE = {
    "ceo-brief": "Plank-as-CEO's morning brief: funnel vs targets, budget per agent, your 3 moves for the day, what the "
                 "CEO did, and decisions waiting for your 👍/👎. Mondays add the week plan and the meeting minutes.",
    "ceo-checkin": "Evening nudge from the CEO about moves still open. Silent when everything is done.",
    "ceo-scorecard": "Friday scorecard: funnel vs targets, CEO and founder grades, agreement rate, one proposed change, "
                     "PIP / board-review flags.",
    "ceo-standups": "Monday 07:00: scout, scribe and ops each file a short standup for the 08:00 agents meeting "
                    "(from 2026-10-19).",
    "ceo-dispatch": "Hourly: lets big CEO decisions go ahead after the 12 h objection window, undoes decisions you "
                    "rejected, and runs the agents' standing orders while they have allowance.",
    "%s": "The CEO's autonomous operate run (weekdays 08:00 + 13:00 Berlin): reads the state, acts with the ceo CLI "
          "(cards, standing orders, budget, plan) and reports in 5 lines. Skipped outside those hours at no cost.",
    "%s": "Weekly agents meeting on Claude Opus 5.5 (Mondays 08:00 Berlin, from 2026-10-19): reviews every agent, "
          "sets the week's plan, allowances and assignments, posts the minutes. Silent at other times at no cost.",
''' % (op_id, meet_id))
rep('''def crew():''', '''def ceo():
    """Plank-as-CEO as a pseudo crew card from `ceo status --json`."""
    import subprocess
    try:
        st = json.loads(subprocess.run(["/usr/local/bin/ceo", "status", "--json"], capture_output=True, text=True,
                                       timeout=30).stdout)
    except Exception as e:
        return {"name": "ceo", "role": "CEO (Plank)", "state": "idle", "task": None,
                "last": {"title": "status unavailable: " + str(e)[:60], "outcome": "", "at": None},
                "queued": 0, "blocked": 0, "done_24h": 0}
    f, t = st.get("funnel") or {}, st.get("targets") or {}
    key = st.get("key_remaining")
    line = "%s · contacted %s/%s · calls %s/%s · $%.2f of $%g%s" % (
        "FIRST SALE" if st.get("north_star_hit") else "hunting first sale", f.get("contacted", 0), t.get("contacted", "-"),
        f.get("call", 0), t.get("calls_booked", "-"), st.get("spent") or 0, st.get("budget") or 0,
        (" · key $%.2f left" % float(key)) if key is not None else "")
    waiting = (st.get("unrated") or 0) + (st.get("proposals") or 0)
    return {"name": "ceo", "role": "CEO (Plank)" + (" · PIP" if st.get("pip") else ""),
            "state": "blocked" if st.get("key_low") else "idle", "task": None,
            "last": {"title": line, "outcome": ("%d decision(s) await your take" % waiting) if waiting else "",
                     "at": None},
            "queued": st.get("open_moves") or 0, "blocked": st.get("contested") or 0, "done_24h": 0}


def crew():''')
rep('''        workers.append(samatha())''', '''        workers.append(samatha())
        workers.insert(0, ceo())''')
open(p, "w").write(s)
print("patched")

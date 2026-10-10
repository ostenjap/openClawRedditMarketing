"""One-off patch: add Samatha to /root/openwebui-fork/mission_collect.py (crons + pseudo crew card). Idempotent."""
p = "/root/openwebui-fork/mission_collect.py"
s = open(p).read()
if "def samatha():" in s:
    raise SystemExit("already patched")


def rep(old, new):
    global s
    assert s.count(old) == 1, old[:60]
    s = s.replace(old, new)


rep('''     "/root/hackamaps-scrapper/scraper.py"),
]''', '''     "/root/hackamaps-scrapper/scraper.py"),
    ("samatha-alerts", "Samatha KPI alerts", "daily 16:00", 24, "/root/samatha/alerts.log", "samatha alerts --notify"),
    ("samatha-remind", "Samatha analytics reminder", "Mon 07:00", 168, "/root/samatha/remind.log", "samatha remind"),
]''')
rep('''    "hackamaps-scraper": ("/root/hackamaps-scrapper/scraper.py", "Supabase (hackathons_staging)"),
}''', '''    "hackamaps-scraper": ("/root/hackamaps-scrapper/scraper.py", "Supabase (hackathons_staging)"),
    "samatha-alerts": ("samatha alerts (KPI engine, no AI)", "Telegram via @Samatha bot"),
    "samatha-remind": ("samatha remind", "Telegram via @Samatha bot"),
}''')
rep('''PURPOSE = {
''', '''PURPOSE = {
    "samatha-alerts": "Samatha (LinkedIn bot) checks KPIs daily: posting cadence behind target, engagement rate under "
                      "target two weeks running, or a post at more than 2x your median. Messages only when one fires.",
    "samatha-remind": "Monday nudge from Samatha to send last week's LinkedIn analytics export so the KPI scorecard updates.",
''')
rep('''def crew():''', '''def samatha():
    """Samatha (LinkedIn bot) as a pseudo crew card: gateway state + KPI line from `samatha status --json`."""
    import subprocess
    try:
        up = subprocess.run(["systemctl", "is-active", "hermes-gateway-samatha"], capture_output=True, text=True,
                            timeout=5).stdout.strip() == "active"
        st = json.loads(subprocess.run(["/usr/local/bin/samatha", "status", "--json"], capture_output=True, text=True,
                                       timeout=20).stdout)
    except Exception as e:
        return {"name": "samatha", "role": "LinkedIn manager", "state": "idle", "task": None,
                "last": {"title": "status unavailable: " + str(e)[:60], "outcome": "", "at": None},
                "queued": 0, "blocked": 0, "done_24h": 0}
    er = st.get("er_last_week")
    kpi = "leads %s · posts %s/%s · ER %s · $%.2f of $%.2f today" % (
        st.get("leads_week"), st.get("posts_week"), st.get("posts_target") or "-",
        ("%.1f%%" % (er * 100)) if er is not None else "-", st.get("cost_today") or 0, st.get("daily_cap") or 0)
    pend = st.get("pending_approvals") or 0
    return {"name": "samatha", "role": "LinkedIn manager · " + ("bot online" if up else "bot offline"),
            "state": "busy" if st.get("running") else "idle",
            "task": {"id": "", "title": "drafting a post", "since": None} if st.get("running") else None,
            "last": {"title": kpi, "outcome": ("%d awaiting your pick" % pend) if pend else "", "at": None},
            "queued": pend, "blocked": 0, "done_24h": 0}


def crew():''')
rep('''        recent = []''', '''        workers.append(samatha())
        recent = []''')
open(p, "w").write(s)
print("patched")

#!/bin/bash
# Offline end-to-end test of the ceo engine: fake clock, fake CRMs, fake per-agent spend, no cards, no Telegram.
# Usage: bash fake_e2e.sh /path/to/ceo.py   (run on the box: needs the hermes venv for yaml + zoneinfo)
set -u
CEO_PY=${1:-/root/ceo/ceo.py}
PY=/usr/local/lib/hermes-agent/venv/bin/python
T=$(mktemp -d /tmp/ceotest.XXXX)
mkdir -p "$T/hermes/profiles/scout" "$T/hermes/profiles/samatha" "$T/home"
# Fixed test config, independent of the founder's live numbers in ceo.yaml.
cat > "$T/home/ceo.yaml" <<'YAML'
week_targets: {contacted: 25, replies: 4, calls_booked: 2, demos: 1, offers_sent: 1}
budget_usd_week: 20
allowances: {ceo: 5, scout: 3, scribe: 1.5, ops: 0.5, samatha: 6, web: 2}
big_decision: {usd_over: 2, realloc_pct_over: 25, objection_hours: 12}
founder_moves_per_day: 3
segments: [berlin-sme]
review: {miss_weeks_for_pip: 2, miss_weeks_for_board_review: 3}
meeting: {start: 2026-10-19, hour: 8, standup_hour: 7, standup_usd: 0.05}
operate_hours: [8, 13]
YAML
export CEO_HOME=$T/home CEO_HERMES_ROOT=$T/hermes CEO_GW_LOG=$T/calls.jsonl CEO_BUNNY_DB=$T/bunny.db \
       CEO_SME_JSON=$T/sme.json CEO_ENV=/nonexistent CEO_FAKE_EXEC=1 CEO_NO_NOTIFY=1
MON=$(date -u -d "2026-10-12 06:30" +%s)   # Monday 08:30 Berlin
export CEO_NOW=$MON
C="$PY $CEO_PY"
FAIL=0
ok(){ echo "  ✔ $1"; }
bad(){ echo "  ✘ $1"; FAIL=$((FAIL+1)); }
expect(){ # expect "<label>" "<regex>" "<text>"
  if echo "$3" | grep -Eq -- "$2"; then ok "$1"; else bad "$1 (wanted /$2/)"; echo "$3" | sed 's/^/      | /' | head -30; fi; }
expect_not(){ if echo "$3" | grep -Eq -- "$2"; then bad "$1 (unwanted /$2/)"; echo "$3" | sed 's/^/      | /' | head -30; else ok "$1"; fi; }
code(){ "$@" >/dev/null 2>&1; echo $?; }

$PY - "$T" "$MON" <<'EOF'
import sqlite3, json, sys
T, mon = sys.argv[1], int(sys.argv[2])
c = sqlite3.connect(f"{T}/bunny.db")
c.execute("create table leads(id integer primary key, company text, category text, city text, tier text, score int, status text, warm_signal text, emails text, updated text)")
rows = [(1, "Und Gretel", "Clean beauty", "Berlin", "A", 96, "contacted", "UGC page", "a@x.de", "2026-10-01T10:00:00Z"),
        (2, "Yoshi Tea", "Tea e-commerce", "Berlin", "A", 84, "new", "Influencer job", "b@x.de", "2026-10-01T10:00:00Z"),
        (3, "Growthy Agentur", "Performance-marketing agency", "Berlin", "A", 95, "new", "casting call", "c@x.de", "2026-10-01T10:00:00Z"),
        (4, "US Brand", "DTC", "US", "A", 99, "new", "x", "d@x.com", "2026-10-01T10:00:00Z"),
        (5, "Kiez Socks", "Fashion DTC", "Berlin", "B", 90, "new", "", "", "2026-10-01T10:00:00Z")]
c.executemany("insert into leads values(?,?,?,?,?,?,?,?,?,?)", rows); c.commit()
json.dump([{"id": str(i), "company": f"SME {i}", "city": "Berlin", "email": f"s{i}@x.de", "need": "Creator für Paid Ads" if i % 2 else "Werkstudent",
            "category": "DTC", "status": "new", "updated": "2026-09-22T07:50:04Z"} for i in range(1, 9)], open(f"{T}/sme.json", "w"))
for path, cost in ((f"{T}/hermes/state.db", 0.8), (f"{T}/hermes/profiles/scout/state.db", 2.9), (f"{T}/hermes/profiles/samatha/state.db", 0.1)):
    s = sqlite3.connect(path)
    s.execute("create table sessions(id text, started_at real, estimated_cost_usd real, actual_cost_usd real)")
    s.execute("insert into sessions values('a', ?, ?, 0)", (mon - 3600, cost))
    s.execute("insert into sessions values('old', ?, 9.0, 0)", (mon - 9 * 86400,))  # last week: must not count
    s.commit()
from datetime import datetime, timezone
iso = datetime.fromtimestamp(mon - 1800, timezone.utc).isoformat(timespec="seconds")
with open(f"{T}/calls.jsonl", "w") as f:
    f.write(json.dumps({"ts": iso, "tag": "sam:draft", "usage": {"cost": 0.5}}) + "\n")
    f.write(json.dumps({"ts": iso, "tag": "drafter", "usage": {"cost": 0.2}}) + "\n")
EOF

echo "== budget per agent (real-spend sources)"
O=$($C budget); expect "ceo spend from default state.db" "ceo 0.80/5.00" "$O"
expect "scout from profile state.db, last week excluded" "scout 2.90/3.00" "$O"
expect "samatha = profile + gateway sam: calls" "samatha 0.60/6.00" "$O"
expect "web = other gateway calls" "web 0.20/2.00" "$O"

echo "== Monday brief"
O=$($C brief); expect "week plan section on Monday" "Week plan" "$O"
expect "decide-offer move" "Decide the pilot offer" "$O"
expect "outreach move with Berlin leads" "Email these [0-9]+ leads" "$O"
expect "best Berlin brand first" "a\) Yoshi Tea" "$O"
expect_not "US lead excluded by segment" "US Brand" "$O"
expect_not "already-contacted CRM lead excluded" "Und Gretel" "$O"
expect "no-email lead flagged" "Kiez Socks.*no email" "$O"
expect_not "no email drafts in the brief" "Subject:|Hi |Dear " "$O"
O=$($C brief); expect "brief only once a day" "already sent today" "$O"

echo "== founder closes moves"
O=$($C done 2); expect "outreach done logs contacted" "Logged [0-9]+ × contacted" "$O"
O=$($C skip 1 "need to think"); expect "skip recorded" "Skipped: need to think" "$O"
O=$($C log reply "Yoshi"); expect "reply logged by company name" "Logged reply: Yoshi Tea" "$O"
O=$($C log reply "Yoshi"); expect "duplicate event refused" "already logged" "$O"

echo "== budget plan rules"
[ "$($C budget plan ceo=15 scout=10 --why x >/dev/null; echo $?)" = 2 ] && ok "over-total allocation refused (exit 2)" || bad "over-total allowed"
O=$($C budget plan scout=4 web=2 --why "scout needs room"); expect "small realloc acts now" "^D[0-9]+ ✅ allowances" "$O"
O=$($C budget plan samatha=0 scout=10 --why "all-in on research"); expect "big realloc (>25%) is a proposal" "⏳ proposal" "$O"
DB=$(echo "$O" | grep -oE '^D[0-9]+' | head -1); $C feedback $DB down "keep Samatha funded" >/dev/null
$C budget plan scout=3 web=3 --why "back to default" >/dev/null

echo "== email ban + allowances"
O=$($C card scribe "Write cold email to Yoshi Tea" "draft the email" --why x); expect "email work refused" "founder's domain" "$O"
O=$($C order add scribe "email sequence for leads" --every daily --why x); expect "email standing order refused" "founder's domain" "$O"
O=$($C card scout "Find buying signals for Yoshi" "goal" --why "reply came in" --est 0.3); expect "scout over allowance refused" "allowance used up" "$O"
O=$($C card ops "Disk check" "" --why "weekly hygiene" --est 0.1); expect "ops card executes" "card t_fake" "$O"

echo "== standing orders + objection window"
O=$($C order add scout "Re-rank Berlin leads by warm signal" --every daily --budget 0.05 --why "rank before outreach")
expect "new order is a proposal" "⏳ proposal: standing order #1" "$O"
D_ORDER=$(echo "$O" | grep -oE '^D[0-9]+' | head -1)
O=$($C order add scout "Scrape more US leads" --every daily --budget 0.05 --why "volume"); D2=$(echo "$O" | grep -oE '^D[0-9]+' | head -1)
O=$($C feedback $D2 down "stay on Berlin"); expect "👎 on a proposal cancels it" "cancelled" "$O"
O=$($C dispatch); expect_not "proposal waits for its window" "went ahead" "$O"
export CEO_NOW=$((MON + 13*3600))   # 21:30 Berlin: window passed, but orders only run 9-20h
O=$($C dispatch); expect "proposal goes ahead after the window" "$D_ORDER went ahead" "$O"
expect_not "cancelled order never runs" "order #2" "$O"
export CEO_NOW=$((MON + 24*3600 + 3*3600))   # Tue 11:30
O=$($C dispatch); expect "active daily order runs (fake card)" "order #1 → card t_fake" "$O"
O=$($C dispatch); expect_not "order runs once per day" "order #1" "$O"
O=$($C orders); expect "order list shows run" "#1 \[active\] scout daily .*runs 1" "$O"

echo "== feedback loop: 👎 → argue once → 👎 → undone"
$C plan set "Week 1: 25 emails to Berlin brands" --why "start" >/dev/null
O=$($C plan set "Week 1: pivot to US" --why "bigger market"); DP=$(echo "$O" | grep -oE '^D[0-9]+' | head -1)
O=$($C feedback $DP down "no US, Berlin first"); expect "👎 on reversible decision → contested" "undo it on my next run" "$O"
O=$($C context); expect "context lists contested" "CONTESTED.*" "$O"; expect "context shows preference" "no US, Berlin first" "$O"
$C argue $DP "US leads have 3x the warm signals" >/dev/null
O=$($C argue $DP "again"); expect "argue only once" "not contested|already argued" "$O"
O=$($C feedback $DP down); expect "second 👎 after argue undoes" "undone for good|undone" "$O"
O=$($C plan); expect "plan restored" "25 emails to Berlin" "$O"
O=$($C plan set "Week 1: 30 emails" --why "more volume"); DQ=$(echo "$O" | grep -oE '^D[0-9]+' | head -1)
$C feedback $DQ down "25 is enough" >/dev/null
export CEO_NOW=$((CEO_NOW + 25*3600))
O=$($C dispatch); expect "stale contested auto-undone after 24h" "$DQ undone" "$O"
O=$($C decisions --contested); expect "nothing left contested" "No decisions" "$O"

echo "== next-day brief builds on events"
O=$($C brief); expect "reply → book-call move" "Book a call with Yoshi Tea" "$O"
expect "what-I-did section" "What I did since the last brief" "$O"
O=$($C offer set "€149 for a 2-week pilot"); expect "offer saved" "Offer saved" "$O"

echo "== Friday scorecard + PIP"
export CEO_NOW=$((MON + 4*86400 + 9*3600))   # Fri 17:30 Berlin
$PY - "$T" <<'EOF'
import sqlite3, sys
c = sqlite3.connect(f"{sys.argv[1]}/home/ceo.db")
c.execute("insert into weeks(week,data,missed,created_at) values('2026-10-05','{}',1,0)"); c.commit()
EOF
O=$($C scorecard); expect "scorecard funnel" "Funnel: contacted" "$O"; expect "grades" "CEO grade: [A-F]" "$O"
expect "agreement rate" "Founder agreement with my decisions: [0-9]+%" "$O"
expect "one proposed change" "One change for next week" "$O"
expect "2nd missed week → PIP" "PIP starts Monday" "$O"
O=$($C decisions --json); echo "$O" | grep -q '"by": "system"' && ok "PIP allowance cut recorded as system decision" || bad "no system PIP decision"
export CEO_NOW=$((MON + 7*86400 + 3600))
O=$($C budget); expect "next week allowances halved" "ceo [0-9.]+/2.50" "$O"
O=$($C context); expect "context mode PIP" "MODE: PIP" "$O"

echo "== Monday agents meeting (Opus job gate)"
at(){ date -u -d "$1" +%s; }
export CEO_NOW=$(at "2026-10-12 06:00")   # Mon 12 Oct 08:00 Berlin: before meeting.start
O=$($C gate meeting); expect "silent before the start date" '\{"wakeAgent": false\}' "$O"
export CEO_NOW=$(at "2026-10-19 05:00")   # Mon 19 Oct 07:00 Berlin
O=$($C standups --at 7); expect "standup cards for all 3 workers" "ops: card t_fake.*" "$O"; expect "scout standup" "scout: card t_fake" "$O"
O=$($C standups --at 7); expect "standups once per week" "already requested" "$O"
O=$($C gate meeting); expect "no wake at 07:00" '\{"wakeAgent": false\}' "$O"
mkdir -p "$T/home/inbox"; printf 'Finished: ranked 40 leads\nBlocked: none\n' > "$T/home/inbox/standup-scout-2026-10-19.md"
export CEO_NOW=$(at "2026-10-19 06:00")   # 08:00 Berlin (CEST)
O=$($C gate meeting); expect "meeting wakes at 08:00 Berlin" '\{"wakeAgent": true\}' "$O"
expect "pack has each agent" "## SCOUT.*" "$O"; expect "pack includes filed standup" "ranked 40 leads" "$O"
expect "pack flags missing standup" "STANDUP: none filed" "$O"; expect "pack has Samatha status" "## SAMATHA" "$O"
O=$($C gate operate); expect "meeting replaces Monday 08:00 operate" '\{"wakeAgent": false\}' "$O"
O=$($C minutes save "scout: keep ranking. scribe: pilot one-pager. ops: idle. Samatha: 3 posts. D9 for your take."); expect "minutes saved" "Minutes saved" "$O"
O=$($C gate meeting); expect "one meeting per week" '\{"wakeAgent": false\}' "$O"
O=$($C minutes save "draft the cold email for Yoshi"); expect "minutes can't carry email work" "founder's domain" "$O"
export CEO_NOW=$(at "2026-10-19 06:30")
O=$($C brief); expect "Monday brief shows the minutes" "Agents meeting minutes" "$O"; expect "minutes text in brief" "pilot one-pager" "$O"
export CEO_NOW=$(at "2026-10-20 06:00")   # Tue 08:00 Berlin
O=$($C gate operate); expect "Tuesday 08:00 operate wakes" '\{"wakeAgent": true\}' "$O"; expect "operate gets the snapshot" "MODE:" "$O"
O=$($C gate operate); expect "one operate run per slot" '\{"wakeAgent": false\}' "$O"
export CEO_NOW=$(at "2026-10-27 07:00")   # Tue 27 Oct 08:00 Berlin (CET, after the clock change)
O=$($C gate operate); expect "DST: 07:00 UTC is 08:00 Berlin in winter" '\{"wakeAgent": true\}' "$O"

echo "== status json + first sale"
O=$($C log sale "Yoshi"); expect "first sale celebrated" "FIRST SALE" "$O"
O=$($C status --json); expect "status json north star" '"north_star_hit": true' "$O"
O=$($C brief --force --dry-run); expect "brief shows north star hit" "North star HIT" "$O"

echo "== notifications (budget alerts first)"
export CEO_NOW=$((MON + 2*86400))   # Wed 14 Oct, same week as the spend fixtures (scout spent 2.90)
$C budget plan scout=2 --why "test: put scout over" >/dev/null
O=$($C alerts --json); expect "agent over allowance alert" "scout is over its allowance" "$O"
expect "no key data → no key alerts" '"key_remaining": null' "$O"
export CEO_FAKE_KEY='{"remaining": 2.5, "limit": 30, "reset": "monthly", "weekly": 6.5}'
O=$($C alerts --json); expect "key below floor is critical" '"level": "crit", "title": "OpenRouter key almost empty' "$O"
expect "runway alert" "runs out around" "$O"; expect "unread count" '"unread": [3-9]' "$O"
expect "budget summary for the panel" '"key_low": true' "$O"
O=$($C alerts read "budget:agent:2026-10-12:scout" --json); expect "mark one read" '"changed": 1' "$O"
O=$($C alerts --json); echo "$O" | $PY -c "import json,sys;d=json.load(sys.stdin);a=[x for x in d['active'] if x['key']=='budget:agent:2026-10-12:scout'][0];sys.exit(0 if a['read_at'] else 1)" && ok "read state sticks" || bad "read state lost"
O=$($C alerts dismiss "budget:runway:2026-10" --json); expect "dismiss" '"changed": 1' "$O"
O=$($C alerts --json); echo "$O" | $PY -c "import json,sys;d=json.load(sys.stdin);sys.exit(0 if not [x for x in d['active'] if x['key'].startswith('budget:runway')] and [x for x in d['earlier'] if x['key'].startswith('budget:runway')] else 1)" && ok "dismissed moves to Earlier" || bad "dismiss not reflected"
O=$($C dispatch --notify); expect "crit alert pushed to Telegram once" "OpenRouter key almost empty" "$O"
O=$($C dispatch --notify); expect_not "never pushed twice" "OpenRouter key almost empty" "$O"
export CEO_FAKE_KEY='{"remaining": 20, "limit": 30, "reset": "monthly", "weekly": 1}'
O=$($C alerts --json); echo "$O" | $PY -c "import json,sys;d=json.load(sys.stdin);sys.exit(0 if [x for x in d['earlier'] if x['key'].endswith(':floor') and x['resolved_at']] else 1)" && ok "cleared condition resolves" || bad "floor alert not resolved"
O=$($C alerts read --all --json); expect "mark all read" '"ok": true' "$O"
O=$($C alerts --json); expect "badge count zero after read-all" '"unread": 0' "$O"
unset CEO_FAKE_KEY

echo
[ $FAIL = 0 ] && echo "ALL PASS" || echo "$FAIL FAILED"
echo "(test data left in $T)"
exit $FAIL

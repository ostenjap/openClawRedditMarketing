#!/usr/local/lib/hermes-agent/venv/bin/python
"""ceo — operating ledger behind Plank-as-CEO (Hermes default profile). Goal: the first paying Bunny Search customer.

The agent (Plank) brings judgment; this CLI holds the state and enforces the rules so autonomy stays safe:
budget allowances per agent, decision records with founder feedback + undo, objection windows for big decisions,
standing orders that let other agents work on their own, founder moves, funnel events and the weekly scorecard.
No LLM calls in here: everything is deterministic, cheap and testable. Lead sources are read-only.

Founder-facing (cron, --notify sends to Telegram via Plank's bot):
  brief [--at HH] [--force]        daily moves (Monday adds week plan, allowances, standing orders)
  checkin [--at HH]                open moves + one-line nudge (silent when all done)
  scorecard [--at HH] [--force]    Friday: funnel vs targets, spend vs plan, agreement rate, grades, PIP flag
Founder replies (Plank maps chat to these):
  done N [--leads K1,K2] | skip N "reason" | log KIND LEAD [--note] | offer set "<text>"
  feedback D7 up|down|meh ["comment"] | pref add "<text>"
CEO levers (each one writes a decision record D<n>):
  budget plan ceo=4 scout=3 ... --why | card WORKER "title" ["body"] --why [--est USD]
  order add AGENT "task" --every SPEC --budget USD [--until X] --why | order pause|resume|stop ID --why
  plan set "<text>" --why | segments set a,b --why | decide "<summary>" --why [--big] [--irreversible]
  undo D7 | argue D7 "<new info>" | move add "<text>"
Read-only: status [--json] | budget [--agent A] [--verify] | decisions [--open|--unrated|--contested] | orders | prefs |
  moves | plan | context (operating snapshot for the ceo-operate cron)
System: dispatch (hourly: proposals whose window passed, stale contested undo, due standing orders)
"""
import argparse
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

HOME = Path(os.environ.get("CEO_HOME", "/root/ceo"))
DB_PATH = HOME / "ceo.db"
CFG_PATH = Path(os.environ.get("CEO_CONFIG", str(HOME / "ceo.yaml")))
HERMES = Path(os.environ.get("CEO_HERMES_ROOT", "/root/.hermes"))
ENV_FILE = Path(os.environ.get("CEO_ENV", "/root/.hermes/.env"))
GW_LOG = Path(os.environ.get("CEO_GW_LOG", "/var/log/llm-gateway/calls.jsonl"))
BUNNY_DB = Path(os.environ.get("CEO_BUNNY_DB", "/root/bunny-leads/crm/bunny.db"))
SME_JSON = Path(os.environ.get("CEO_SME_JSON", "/root/berlin-sme-campaign/data/leads.json"))
RANK_FILE = Path(os.environ.get("CEO_RANK_FILE", str(HOME / "inbox" / "lead_rank.json")))
KANBAN_DB = Path(os.environ.get("CEO_KANBAN_DB", "/root/.hermes/kanban.db"))
INBOX = HOME / "inbox"
CREW_CARD = os.environ.get("CEO_CREW_CARD", "/root/.hermes/scripts/crew-card")
SAMATHA = os.environ.get("CEO_SAMATHA", "/usr/local/bin/samatha")
TZ = ZoneInfo(os.environ.get("CEO_TZ", "Europe/Berlin"))
FAKE_EXEC = os.environ.get("CEO_FAKE_EXEC") == "1"

AGENTS = ["ceo", "scout", "scribe", "ops", "samatha"]
WORKERS = {"scout", "scribe", "ops"}
FUNNEL = ["contacted", "reply", "call", "demo", "offer", "sale"]
TARGET_KEYS = {"contacted": "contacted", "replies": "reply", "calls_booked": "call", "demos": "demo", "offers_sent": "offer"}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Email is the founder's domain: the CEO and its agents never draft, write or send email.
EMAIL_WORK = re.compile(r"cold[- ]?e-?mail|e-?mail\W+(draft|copy|sequence|template|campaign|text|body|subject)|"
                        r"(draft|write|send|compose|reply|answer|follow[- ]?up)\W+(\w+\W+){0,3}e-?mails?\b|newsletter|"
                        r"\binbox\b|mailchimp|smtp", re.I)
AGENCY = re.compile(r"agen(cy|tur)|ugc platform|marketplace", re.I)
NEED_FIT = re.compile(r"paid ads|ugc|tiktok|reels|creator|influencer", re.I)
# ICP (VOICE.md): small DTC / e-commerce brands that source creators themselves.
BRAND_FIT = re.compile(r"dtc|d2c|e-?commerce|online[- ]?shop|brand|marke|cosmetic|beauty|fashion|food|drink|grooming|"
                       r"skincare|apparel|shop", re.I)
NO_SIGNAL = re.compile(r"^\s*(no|kein)\b", re.I)
DACH = re.compile(r"berlin|munich|münchen|hamburg|cologne|köln|frankfurt|stuttgart|düsseldorf|leipzig|vienna|wien|"
                  r"zurich|zürich|\((de|at|ch)\)", re.I)
CRM_STATUS = {"contacted": "contacted", "replied": "reply", "reply": "reply", "call": "call", "meeting": "call",
              "demo": "demo", "offer": "offer", "proposal": "offer", "won": "sale", "customer": "sale", "paid": "sale"}
MOVE_EVENT = {"outreach": "contacted", "book_call": "call", "send_offer": "offer"}

DEFAULT_CFG = {
    "north_star": "first_payment",
    "week_targets": {"contacted": 25, "replies": 4, "calls_booked": 2, "demos": 1, "offers_sent": 1},
    "budget_usd_week": 20.0,
    "allowances": {"ceo": 4.0, "scout": 3.0, "scribe": 1.5, "ops": 0.5, "samatha": 6.0, "web": 3.0},
    "big_decision": {"usd_over": 2.0, "realloc_pct_over": 25, "objection_hours": 12},
    "founder_moves_per_day": 3,
    "segments": ["berlin-sme"],
    "review": {"miss_weeks_for_pip": 2, "miss_weeks_for_board_review": 3},
    "meeting": {"start": "2026-10-19", "hour": 8, "standup_hour": 7, "standup_usd": 0.05},
    "operate_hours": [8, 13],
}


class Refused(Exception):
    """A rule said no. Printed for the agent, exit code 2."""


# --------------------------------------------------------------------------- config / time / storage
def cfg():
    c = json.loads(json.dumps(DEFAULT_CFG))
    try:
        user = yaml.safe_load(CFG_PATH.read_text()) or {}
    except OSError:
        user = {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    c["budget_usd_week"] = float(c["budget_usd_week"])
    return c


def now():
    return int(os.environ.get("CEO_NOW") or time.time())


def local(ts=None):
    return datetime.fromtimestamp(now() if ts is None else ts, TZ)


def today():
    return local().date().isoformat()


def week_start_ts(ts=None):
    d = local(ts).date()
    d = d - timedelta(days=d.weekday())
    return int(datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp())


def week_key(ts=None):
    return local(week_start_ts(ts)).date().isoformat()


def weekdays_left():
    """Working days left this week including today (min 1)."""
    return max(1, 5 - local().weekday()) if local().weekday() < 5 else 1


def hhmm(ts):
    return local(ts).strftime("%a %H:%M")


SCHEMA = """
CREATE TABLE IF NOT EXISTS moves(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, n INTEGER, kind TEXT NOT NULL,
  text TEXT NOT NULL, leads TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'open', reason TEXT,
  created_at INTEGER NOT NULL, closed_at INTEGER);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL,
  lead TEXT NOT NULL, label TEXT, note TEXT, source TEXT NOT NULL DEFAULT 'founder');
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL,
  summary TEXT NOT NULL, why TEXT NOT NULL DEFAULT '', cost REAL NOT NULL DEFAULT 0, reversible INTEGER NOT NULL DEFAULT 1,
  big INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, due_at INTEGER, payload TEXT, undo TEXT, result TEXT,
  feedback INTEGER, feedback_note TEXT, feedback_at INTEGER, argued INTEGER NOT NULL DEFAULT 0, by TEXT NOT NULL DEFAULT 'ceo');
CREATE TABLE IF NOT EXISTS prefs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, text TEXT NOT NULL, source TEXT);
CREATE TABLE IF NOT EXISTS allowances(week TEXT NOT NULL, agent TEXT NOT NULL, usd REAL NOT NULL, PRIMARY KEY(week, agent));
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, task TEXT NOT NULL, body TEXT,
  every TEXT NOT NULL, budget REAL NOT NULL, until TEXT, status TEXT NOT NULL, last_run INTEGER, runs INTEGER NOT NULL DEFAULT 0,
  last_result TEXT, decision_id INTEGER, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, week TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS weeks(week TEXT PRIMARY KEY, data TEXT NOT NULL, missed INTEGER NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS meetings(week TEXT PRIMARY KEY, ts INTEGER NOT NULL, minutes TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(key TEXT PRIMARY KEY, source TEXT NOT NULL, level TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, resolved_at INTEGER, read_at INTEGER,
  dismissed_at INTEGER, pushed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


@contextmanager
def db():
    HOME.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def kv_get(con, key, default=None):
    r = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(con, key, value):
    con.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))


# --------------------------------------------------------------------------- telegram
def env_get(key):
    if os.environ.get(key):
        return os.environ[key]
    try:
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def notify(text):
    """Send via Plank's bot (same chat as the CEO conversation). Telegram limit 4096 chars."""
    tok = env_get("TELEGRAM_BOT_TOKEN")
    chat = env_get("TELEGRAM_HOME_CHANNEL") or (env_get("TELEGRAM_ALLOWED_USERS") or "").split(",")[0].strip()
    if not tok or not chat or os.environ.get("CEO_NO_NOTIFY") == "1":
        print(text)
        return False
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > 3800:
            chunks.append(cur)
            cur = block
        else:
            cur = (cur + "\n\n" + block) if cur else block
    if cur:
        chunks.append(cur)
    ok = True
    for ch in chunks:
        data = urllib.parse.urlencode({"chat_id": chat, "text": ch, "disable_web_page_preview": "true"}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=20).read()
        except Exception as e:
            ok = False
            print(f"telegram send failed: {type(e).__name__}", file=sys.stderr)
    print(text)
    return ok


def out(text, a):
    if not text:
        return
    if getattr(a, "notify", False):
        notify(text)
    else:
        print(text)


def at_gate(a):
    """Cron fires at both possible UTC hours (no CRON_TZ on this box); run only when Berlin local hour matches."""
    return a.at is None or local().hour == int(a.at)


# --------------------------------------------------------------------------- spend + allowances
def spend_by_agent(since, until=None):
    """Real LLM spend per agent from each Hermes profile's sessions + the llm-gateway log (tag prefix)."""
    until = until or now() + 1
    out_ = {a: 0.0 for a in AGENTS}
    out_["web"] = 0.0
    for agent in AGENTS:
        p = HERMES / "state.db" if agent == "ceo" else HERMES / "profiles" / agent / "state.db"
        if not p.exists():
            continue
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
            v = c.execute("SELECT COALESCE(SUM(CASE WHEN COALESCE(actual_cost_usd,0)>0 THEN actual_cost_usd "
                          "ELSE COALESCE(estimated_cost_usd,0) END),0) FROM sessions WHERE started_at>=? AND started_at<?",
                          (since, until)).fetchone()[0]
            c.close()
            out_[agent] += float(v or 0)
        except sqlite3.Error:
            pass
    try:
        with GW_LOG.open() as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts = datetime.fromisoformat(r["ts"]).timestamp()
                except (ValueError, KeyError):
                    continue
                if not (since <= ts < until):
                    continue
                cost = float(((r.get("usage") or {}).get("cost")) or 0)
                tag = r.get("tag") or ""
                who = "samatha" if tag.startswith("sam:") else "ceo" if tag.startswith("ceo:") else "web"
                out_[who] += cost
    except OSError:
        pass
    return {k: round(v, 4) for k, v in out_.items()}


def allowances(con, week=None):
    week = week or week_key()
    rows = {r["agent"]: r["usd"] for r in con.execute("SELECT agent,usd FROM allowances WHERE week=?", (week,))}
    if rows:
        return rows
    return {k: float(v) for k, v in cfg()["allowances"].items()}


def set_alloc(con, week, alloc):
    con.execute("DELETE FROM allowances WHERE week=?", (week,))
    for k, v in alloc.items():
        con.execute("INSERT INTO allowances(week,agent,usd) VALUES(?,?,?)", (week, k, float(v)))


def key_info(con):
    """OpenRouter key usage/limit (hard ceiling for everything incl. Plank). Cached 10 min in kv. None if unknown."""
    if os.environ.get("CEO_FAKE_KEY"):  # tests only
        return dict(json.loads(os.environ["CEO_FAKE_KEY"]), at=now())
    cached = kv_get(con, "key_info")
    if cached and now() - cached.get("at", 0) < 600:
        return cached
    key = env_get("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r).get("data", {})
    except Exception:
        return cached
    info = {"at": now(), "limit": d.get("limit"), "remaining": d.get("limit_remaining"), "reset": d.get("limit_reset"),
            "weekly": d.get("usage_weekly"), "daily": d.get("usage_daily"), "monthly": d.get("usage_monthly")}
    kv_set(con, "key_info", info)
    return info


def budget_state(con):
    c = cfg()
    spent = spend_by_agent(week_start_ts())
    alloc = allowances(con)
    total_spent = round(sum(spent.values()), 4)
    elapsed_days = (now() - week_start_ts()) / 86400
    proj = round(total_spent / elapsed_days * 7, 2) if elapsed_days >= 1 else None
    k = key_info(con)
    key_low = bool(k and k.get("remaining") is not None and float(k["remaining"]) < float(c.get("key_floor_usd", 3)))
    return {"week": week_key(), "total": c["budget_usd_week"], "spent": spent, "alloc": alloc,
            "total_spent": total_spent, "projected_week": proj, "key": k, "key_low": key_low,
            "exhausted": total_spent >= c["budget_usd_week"] or key_low}


def agent_room(con, agent, need=0.0):
    """Raise Refused unless `agent` still has `need` dollars in its allowance and the week total has room."""
    b = budget_state(con)
    if b["key_low"]:
        raise Refused(f"OpenRouter key almost at its limit (${float(b['key']['remaining']):.2f} left). Report only, "
                      "so Plank keeps working.")
    if b["exhausted"]:
        raise Refused(f"weekly LLM budget used up (${b['total_spent']:.2f} of ${b['total']:.2f}). Report only.")
    left = b["alloc"].get(agent, 0) - b["spent"].get(agent, 0)
    if left < need or left <= 0:
        raise Refused(f"{agent} allowance used up (${b['spent'].get(agent, 0):.2f} of ${b['alloc'].get(agent, 0):.2f}). "
                      "Move money with `ceo budget plan` or wait for Monday.")
    if b["total"] - b["total_spent"] < need:
        raise Refused(f"only ${b['total'] - b['total_spent']:.2f} left this week, this needs ~${need:.2f}")
    return left


def no_email(*texts):
    for t in texts:
        if t and EMAIL_WORK.search(t):
            raise Refused("email is the founder's domain: the CEO and its agents never draft, write or send email. "
                          "Name the leads and the reason; the founder writes the email.")


# --------------------------------------------------------------------------- leads (read-only)
def load_leads():
    """Both CRMs, read-only. Key = B<id> (Bunny CRM) or S<id> (Berlin SME json)."""
    leads = {}
    try:
        c = sqlite3.connect(f"file:{BUNNY_DB}?mode=ro", uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT id,company,category,city,tier,score,status,warm_signal,emails,updated FROM leads"):
            leads[f"B{r['id']}"] = {"key": f"B{r['id']}", "company": r["company"], "category": r["category"] or "",
                                    "city": r["city"] or "", "tier": r["tier"] or "", "score": int(r["score"] or 0),
                                    "status": (r["status"] or "new").lower(), "signal": r["warm_signal"] or "",
                                    "email": bool((r["emails"] or "").strip()), "updated": r["updated"], "src": "bunny"}
        c.close()
    except sqlite3.Error:
        pass
    try:
        for r in json.loads(SME_JSON.read_text()):
            leads[f"S{r.get('id')}"] = {"key": f"S{r.get('id')}", "company": r.get("company") or "?",
                                        "category": r.get("category") or "", "city": r.get("city") or "Berlin",
                                        "tier": "", "score": 0, "status": (r.get("status") or "new").lower(),
                                        "signal": r.get("need") or "", "email": bool((r.get("email") or "").strip()),
                                        "updated": r.get("updated"), "src": "berlin-sme"}
    except (OSError, ValueError, TypeError):
        pass
    return leads


def in_segment(lead, segments):
    for s in segments:
        if s == "all":
            return True
        if s == "berlin-sme" and ("berlin" in lead["city"].lower() or lead["src"] == "berlin-sme"):
            return True
        if s == "dach" and (DACH.search(lead["city"]) or lead["src"] == "berlin-sme"):
            return True
        if s == "us" and lead["city"].upper() in ("US", "USA"):
            return True
        if s == "eu" and lead["city"].upper() not in ("US", "USA", ""):
            return True
    return False


def lead_score(lead, rank):
    """Scout's ranking wins when present; otherwise CRM score + simple fit rules (VOICE.md: brands, not agencies)."""
    why = []
    if lead["key"] in rank:
        return rank[lead["key"]]["score"], rank[lead["key"]].get("why") or "scout ranking"
    s = lead["score"] if lead["src"] == "bunny" else 50
    if lead["src"] == "berlin-sme":
        if NEED_FIT.search(lead["signal"]):
            s += 15
            why.append("creator need")
        if BRAND_FIT.search(lead["category"]):
            s += 10
            why.append("brand")
        else:
            s -= 25
            why.append("not a DTC brand")
    if lead["src"] == "bunny" and lead["signal"] and not NO_SIGNAL.search(lead["signal"]):
        s += 10
        why.append("warm signal")
    if AGENCY.search(lead["category"]):
        s -= 30
        why.append("agency (lower fit)")
    if not lead["email"]:
        s -= 15
        why.append("no email")
    return s, ", ".join(why)


def load_rank():
    try:
        d = json.loads(RANK_FILE.read_text())
        items = d.get("ranked", d) if isinstance(d, dict) else d
        return {str(x["key"]): {"score": float(x.get("score", 0)), "why": x.get("why", "")} for x in items if "key" in x}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def touched_keys(con):
    return {r[0] for r in con.execute("SELECT DISTINCT lead FROM events")}


def in_open_moves(con):
    keys = set()
    for r in con.execute("SELECT leads FROM moves WHERE status='open'"):
        keys.update(json.loads(r[0]))
    return keys


def candidates(con, n):
    seg = segments(con)
    rank = load_rank()
    skip = touched_keys(con) | in_open_moves(con)
    pool = [l for l in load_leads().values() if l["status"] == "new" and l["key"] not in skip and in_segment(l, seg)]
    scored = sorted(((lead_score(l, rank), l) for l in pool), key=lambda x: -x[0][0])
    return [(l, w) for (s, w), l in scored[:n]], len(pool)


def parse_ts(v):
    try:
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def sync_crm(con):
    """Founder status changes in either CRM count as funnel events (no double entry). Only changes after CEO start."""
    started = kv_get(con, "started_at")
    if started is None:
        kv_set(con, "started_at", now())
        return 0
    n = 0
    for l in load_leads().values():
        kind = CRM_STATUS.get(l["status"])
        ts = parse_ts(l["updated"])
        if not kind or not ts or ts < started:
            continue
        if con.execute("SELECT 1 FROM events WHERE lead=? AND kind=?", (l["key"], kind)).fetchone():
            continue
        con.execute("INSERT INTO events(ts,kind,lead,label,note,source) VALUES(?,?,?,?,?,?)",
                    (ts, kind, l["key"], l["company"], "from CRM status", "crm"))
        n += 1
    return n


def segments(con):
    return kv_get(con, "segments") or cfg().get("segments") or ["berlin-sme"]


# --------------------------------------------------------------------------- funnel
def funnel(con, since=None, until=None):
    since = since if since is not None else week_start_ts()
    until = until or now() + 1
    return {k: con.execute("SELECT COUNT(DISTINCT lead) FROM events WHERE kind=? AND ts>=? AND ts<?",
                           (k, since, until)).fetchone()[0] for k in FUNNEL}


def first_sale(con):
    return con.execute("SELECT ts,label,lead FROM events WHERE kind='sale' ORDER BY ts LIMIT 1").fetchone()


def funnel_line(f):
    t = cfg()["week_targets"]
    return " · ".join(f"{name.replace('_', ' ')} {f[ev]}/{t.get(name, '-')}" for name, ev in TARGET_KEYS.items())


# --------------------------------------------------------------------------- decisions
def new_decision(con, kind, summary, why, payload=None, big=False, cost=0.0, reversible=True, by="ceo"):
    c = cfg()["big_decision"]
    big = bool(big or cost > float(c["usd_over"]))
    status = "proposed" if big else "active"
    due = now() + int(float(c["objection_hours"]) * 3600) if big else None
    cur = con.execute("INSERT INTO decisions(ts,kind,summary,why,cost,reversible,big,status,due_at,payload,by) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (now(), kind, summary, why or "", cost, 1 if reversible else 0, 1 if big else 0, status, due,
                       json.dumps(payload or {}), by))
    did = cur.lastrowid
    if not big:
        execute(con, did)
    return con.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()


def execute(con, did):
    d = con.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
    p = json.loads(d["payload"] or "{}")
    k = d["kind"]
    undo, result = None, None
    if k == "alloc":
        undo = {"week": p["week"], "alloc": allowances(con, p["week"])}
        set_alloc(con, p["week"], p["alloc"])
    elif k == "order_add":
        con.execute("UPDATE orders SET status='active' WHERE id=?", (p["order_id"],))
        undo = {"order_id": p["order_id"], "status": "stopped"}
    elif k == "order_set":
        prev = con.execute("SELECT status FROM orders WHERE id=?", (p["order_id"],)).fetchone()[0]
        con.execute("UPDATE orders SET status=? WHERE id=?", (p["status"], p["order_id"]))
        undo = {"order_id": p["order_id"], "status": prev}
    elif k == "plan":
        prev = current_plan(con)
        con.execute("INSERT INTO plans(ts,week,text) VALUES(?,?,?)", (now(), week_key(), p["text"]))
        undo = {"text": prev or "(no plan)"}
    elif k == "segments":
        undo = {"segments": segments(con)}
        kv_set(con, "segments", p["segments"])
    elif k == "card":
        result = run_card(p["worker"], p["title"], p.get("body") or "")
    con.execute("UPDATE decisions SET status='active', undo=?, result=? WHERE id=?",
                (json.dumps(undo) if undo else None, result, did))


def revert(con, d):
    u = json.loads(d["undo"] or "null")
    if not u:
        return False
    k = d["kind"]
    if k == "alloc":
        set_alloc(con, u["week"], u["alloc"])
    elif k in ("order_add", "order_set"):
        con.execute("UPDATE orders SET status=? WHERE id=?", (u["status"], u["order_id"]))
    elif k == "plan":
        con.execute("INSERT INTO plans(ts,week,text) VALUES(?,?,?)", (now(), week_key(), u["text"]))
    elif k == "segments":
        kv_set(con, "segments", u["segments"])
    else:
        return False
    con.execute("UPDATE decisions SET status='undone' WHERE id=?", (d["id"],))
    return True


def dline(d):
    if d["status"] == "proposed":
        return (f"D{d['id']} ⏳ proposal: {d['summary']} · why: {d['why']}\n"
                f"   Goes ahead {hhmm(d['due_at'])} unless you reply 'D{d['id']} 👎 <why>'.")
    extra = f" · {d['result']}" if d["result"] else ""
    if d["status"] in ("undone", "cancelled"):
        return f"D{d['id']} ↩️ {d['status']}: {d['summary']}"
    return (f"D{d['id']} ✅ {d['summary']}{extra} · why: {d['why']}\n"
            f"   Your take? 'D{d['id']} 👍' or 'D{d['id']} 👎 <why>'")


def get_decision(con, ref):
    m = re.match(r"^[Dd]?(\d+)$", str(ref).strip())
    if not m:
        raise Refused(f"not a decision id: {ref}")
    d = con.execute("SELECT * FROM decisions WHERE id=?", (int(m.group(1)),)).fetchone()
    if not d:
        raise Refused(f"no decision D{m.group(1)}")
    return d


def current_plan(con):
    r = con.execute("SELECT text FROM plans ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else None


# --------------------------------------------------------------------------- execution (crew + samatha)
def run_card(worker, title, body):
    if FAKE_EXEC:
        return "card t_fake%04d" % (abs(hash((worker, title))) % 10000)
    try:
        p = subprocess.run([CREW_CARD, worker, title, body, "--workspace", "dir:/root/crew-output/ceo",
                            "--created-by", "ceo"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"card failed: {type(e).__name__}"
    m = re.search(r"\bt_[0-9a-f]{6,}\b", p.stdout + p.stderr)
    if p.returncode != 0:
        return "card failed: " + (p.stderr or p.stdout).strip()[:120]
    return "card " + (m.group(0) if m else "created")


def run_samatha(task):
    if FAKE_EXEC:
        return "samatha idea queued (fake)"
    try:
        p = subprocess.run([SAMATHA, "idea", task], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"samatha failed: {type(e).__name__}"
    return ("samatha: " + (p.stdout or p.stderr).strip().splitlines()[0][:120]) if (p.stdout or p.stderr) else "samatha ran"


# --------------------------------------------------------------------------- moves
def open_moves(con):
    return con.execute("SELECT * FROM moves WHERE status='open' ORDER BY COALESCE(n,99), id").fetchall()


def renumber(con):
    for i, m in enumerate(open_moves(con), 1):
        con.execute("UPDATE moves SET n=? WHERE id=?", (i, m["id"]))


def add_move(con, kind, text, leads=()):
    con.execute("INSERT INTO moves(day,kind,text,leads,created_at) VALUES(?,?,?,?,?)",
                (today(), kind, text, json.dumps(list(leads)), now()))


def generate_moves(con):
    """Fill today's open slots, highest funnel impact first: replies → calls → offer → outreach."""
    c = cfg()
    per_day = int(c["founder_moves_per_day"])
    slots = per_day - len(open_moves(con))
    if slots <= 0:
        return
    leads = load_leads()
    label = lambda k: leads.get(k, {}).get("company") or k  # noqa: E731
    ev = {}
    for r in con.execute("SELECT kind,lead,ts FROM events ORDER BY ts"):
        ev.setdefault(r["lead"], {})[r["kind"]] = r["ts"]
    busy = in_open_moves(con)
    todo = []
    for lead, e in ev.items():
        if lead in busy or "sale" in e:
            continue
        if "reply" in e and "call" not in e:
            todo.append(("book_call", f"Book a call with {label(lead)}: they replied {hhmm(e['reply'])}. "
                                      "Offer two concrete time slots.", [lead]))
        elif ("call" in e or "demo" in e) and "offer" not in e:
            todo.append(("send_offer", f"Send {label(lead)} the pilot offer (price + scope + start date) while the call "
                                       "is fresh.", [lead]))
    offer = kv_get(con, "offer")
    if not offer and not con.execute("SELECT 1 FROM moves WHERE kind='decide' AND status='open'").fetchone():
        todo.append(("decide", "Decide the pilot offer: price, what's included, how long. Reply e.g. 'offer: €149 for a "
                               "2-week pilot, 30 vetted creators in your niche + city'. I need this before the first call.", []))
    target = int(c["week_targets"].get("contacted", 25))
    remaining = max(0, target - funnel(con)["contacted"])
    if remaining:
        k = min(6, max(3, math.ceil(remaining / weekdays_left())))
        picks, pool = candidates(con, k)
        if picks:
            lines = [f"   {chr(97 + i)}) {l['company']} ({(l['category'] or '?')[:40]}, {l['city'] or '?'}) · "
                     f"{(l['signal'] or 'no signal')[:70]}{' · no email, try LinkedIn/IG' if not l['email'] else ''}"
                     f"{' · ' + w if w else ''} · {l['key']}" for i, (l, w) in enumerate(picks)]
            todo.append(("outreach", f"Email these {len(picks)} leads, in your own words (I don't touch email):\n" +
                         "\n".join(lines) + f"\n   ({pool} uncontacted in segment {','.join(segments(con))})",
                         [l["key"] for l, _ in picks]))
    for kind, text, ls in todo[:slots]:
        add_move(con, kind, text, ls)


def moves_text(con):
    ms = open_moves(con)
    if not ms:
        return "No open moves. 🎯"
    return "\n".join(f"{m['n']}. {m['text']}" + (f"  (open since {m['day']})" if m["day"] != today() else "") for m in ms)


# --------------------------------------------------------------------------- texts
def budget_lines(con, b=None):
    b = b or budget_state(con)
    parts = [f"{a} {b['spent'].get(a, 0):.2f}/{b['alloc'].get(a, 0):.2f}" for a in AGENTS + ["web"]]
    pace = f"on pace for ${b['projected_week']:.2f}" if b["projected_week"] is not None else "too early to project"
    k = b.get("key")
    key = (f"\n   OpenRouter key: ${float(k['remaining']):.2f} of ${k['limit']} left ({k['reset']})"
           f"{' ⚠️ LOW: report-only' if b['key_low'] else ''}") if k and k.get("remaining") is not None else ""
    return (f"Budget: ${b['total_spent']:.2f} of ${b['total']:g} this week ({pace})\n"
            f"   " + " · ".join(parts) + key)


def unrated(con, days=7):
    return con.execute("SELECT * FROM decisions WHERE status='active' AND feedback IS NULL AND by='ceo' "
                       "AND ts>=? ORDER BY id", (now() - days * 86400,)).fetchall()


def proposals(con):
    return con.execute("SELECT * FROM decisions WHERE status='proposed' ORDER BY due_at").fetchall()


def north_star_line(con):
    s = first_sale(con)
    if s:
        return f"🎉 North star HIT: first payment from {s['label'] or s['lead']} on {local(s['ts']).date()}."
    started = kv_get(con, "started_at") or now()
    return f"North star: first paying customer. Not yet (day {int((now() - started) / 86400) + 1} of the hunt)."


def brief_text(con, monday=False):
    f = funnel(con)
    since = kv_get(con, "last_brief_at") or now() - 86400
    did = con.execute("SELECT * FROM decisions WHERE ts>=? AND by='ceo' AND status IN ('active','undone') ORDER BY id",
                      (since,)).fetchall()
    L = [f"☀️ CEO brief · {local().strftime('%a %d %b')}", north_star_line(con), "This week: " + funnel_line(f),
         budget_lines(con)]
    if kv_get(con, "pip_week") == week_key():
        L.append("⚠️ I'm on a PIP this week (missed targets 2 weeks running): half budget, one segment.")
    mins = minutes_for(con) if monday else None
    if mins:
        L += ["", "🗓 Agents meeting minutes (full: 'ceo minutes show')"] + mins.splitlines()[:15]
    if monday:
        L += ["", "📋 Week plan: " + (current_plan(con) or "(none yet, I'll set it in this morning's operate run)"),
              "Segments: " + ", ".join(segments(con)) + " · Offer: " + (kv_get(con, "offer") or "not decided")]
        orders = con.execute("SELECT * FROM orders WHERE status='active' ORDER BY id").fetchall()
        if orders:
            L.append("Standing orders: " + "; ".join(f"#{o['id']} {o['agent']} {o['every']} ${o['budget']:.2f}: "
                                                     f"{o['task'][:50]}" for o in orders))
    L += ["", "🎯 Your moves today", moves_text(con), "Reply 'done 1', 'done 1 --leads B12,S4' or 'skip 2 <reason>'."]
    if did:
        L += ["", "🛠 What I did since the last brief"] + [dline(d) for d in did[-6:]]
    rest = [d for d in unrated(con) if d["id"] not in {x["id"] for x in did}]
    if rest:
        L += ["", "Still waiting for your take: " + ", ".join(f"D{d['id']} ({d['summary'][:40]})" for d in rest[:6])]
    pr = proposals(con)
    if pr:
        L += ["", "⏳ Going ahead unless you object"] + [dline(d) for d in pr]
    starved = [a for a in AGENTS if (kv_get(con, f"starved:{a}") or "") == today()]
    if starved:
        L.append(f"💸 Out of allowance today: {', '.join(starved)} (standing orders skipped).")
    return "\n".join(L)


def grade(ratio):
    return "A" if ratio >= .9 else "B" if ratio >= .7 else "C" if ratio >= .5 else "D" if ratio >= .3 else "F"


def week_report(con, since, until):
    c = cfg()
    f = funnel(con, since, until)
    t = c["week_targets"]
    ratios = {name: min(1.0, f[ev] / float(t[name])) for name, ev in TARGET_KEYS.items() if t.get(name)}
    missed = int(f["contacted"] < int(t.get("contacted", 0)) or f["call"] < int(t.get("calls_booked", 0)))
    m = {r["status"]: r["n"] for r in con.execute(
        "SELECT status, COUNT(*) n FROM moves WHERE created_at>=? AND created_at<? GROUP BY status", (since, until))}
    moves_total = sum(m.values())
    rated = con.execute("SELECT feedback FROM decisions WHERE ts>=? AND ts<? AND feedback IS NOT NULL AND by='ceo'",
                        (since, until)).fetchall()
    ups = sum(1 for r in rated if r[0] > 0)
    total_dec = con.execute("SELECT COUNT(*) FROM decisions WHERE ts>=? AND ts<? AND by='ceo'", (since, until)).fetchone()[0]
    return {"funnel": f, "ratios": ratios, "missed": missed,
            "ceo_grade": grade(sum(ratios.values()) / len(ratios)) if ratios else "-",
            "moves": m, "founder_done_pct": round(100 * m.get("done", 0) / moves_total) if moves_total else None,
            "founder_grade": grade(m.get("done", 0) / moves_total) if moves_total else "-",
            "agreement_pct": round(100 * ups / len(rated)) if rated else None, "rated": len(rated), "decisions": total_dec,
            "spent": spend_by_agent(since, until), "alloc": allowances(con, local(since).date().isoformat())}


def miss_streak(con, this_missed):
    streak = 1 if this_missed else 0
    if not this_missed:
        return 0
    for r in con.execute("SELECT missed FROM weeks WHERE week<? ORDER BY week DESC", (week_key(),)):
        if not r[0]:
            break
        streak += 1
    return streak


def biggest_gap(rep):
    order = [("contacted", "Outreach volume is the bottleneck. Proposal: block 45 min every morning for emails before "
                           "anything else, 5 a day."),
             ("replies", "Replies are the bottleneck. Proposal: a sharper first line that names their live creator "
                         "need, and scout re-ranks leads by warm signal."),
             ("calls_booked", "Replies don't turn into calls. Proposal: put two concrete time slots in every reply."),
             ("demos", "Calls don't turn into demos. Proposal: show a live search for their niche + city in the call."),
             ("offers_sent", "No offers going out. Proposal: send the pilot offer the same day as every demo.")]
    for name, text in order:
        if name in rep["ratios"] and rep["ratios"][name] < 1:
            return text
    return "All targets hit. Proposal: raise the contacted target by 20% next week."


# --------------------------------------------------------------------------- commands: founder-facing
def cmd_brief(a):
    if not at_gate(a):
        return
    with db() as con:
        if kv_get(con, "brief_day") == today() and not a.force:
            print("brief already sent today (use --force)")
            return
        sync_crm(con)
        generate_moves(con)
        renumber(con)
        text = brief_text(con, monday=a.week or local().weekday() == 0)
        if a.dry_run:
            con.rollback()
        else:
            kv_set(con, "brief_day", today())
            kv_set(con, "last_brief_at", now())
    if a.dry_run:
        print(text)
    else:
        out(text, a)


def cmd_checkin(a):
    if not at_gate(a):
        return
    with db() as con:
        sync_crm(con)
        ms = open_moves(con)
        if not ms:
            return
        skipped = con.execute("SELECT COUNT(*) FROM moves WHERE status='skipped' AND closed_at>=?",
                              (week_start_ts(),)).fetchone()[0]
        f = funnel(con)
        target = cfg()["week_targets"].get("contacted", 25)
        L = [f"🌙 Check-in: {len(ms)} move(s) still open.", moves_text(con)]
        if skipped >= 3:
            L.append(f"That's {skipped} skips this week while contacted is at {f['contacted']}/{target}. Skipping is "
                     "fine, but say what replaces it.")
        L.append("Close one before you log off ('done N'), or skip it with a reason ('skip N <why>').")
    out("\n".join(L), a)


def cmd_scorecard(a):
    if not at_gate(a):
        return
    c = cfg()
    with db() as con:
        if kv_get(con, "scorecard_week") == week_key() and not a.force and not a.dry_run:
            print("scorecard already sent this week (use --force)")
            return
        sync_crm(con)
        rep = week_report(con, week_start_ts(), now() + 1)
        streak = miss_streak(con, rep["missed"])
        L = [f"📊 Scorecard · week of {week_key()}", north_star_line(con),
             "Funnel: " + funnel_line(rep["funnel"]) + (f" · sales {rep['funnel']['sale']}" if rep["funnel"]["sale"] else ""),
             f"CEO grade: {rep['ceo_grade']} (targets hit) · Founder grade: {rep['founder_grade']} "
             f"(moves done {rep['moves'].get('done', 0)}/{sum(rep['moves'].values())}, skipped {rep['moves'].get('skipped', 0)})",
             f"Founder agreement with my decisions: "
             + (f"{rep['agreement_pct']}% of {rep['rated']} rated" if rep["rated"] else "nothing rated yet")
             + f" ({rep['decisions']} decisions)",
             budget_lines(con)]
        if rep["founder_grade"] in ("D", "F") and rep["ceo_grade"] in ("D", "F"):
            L.append("Plainly: founder execution is the bottleneck this week. My plan only works if the moves get done.")
        L += ["", "🔧 One change for next week: " + biggest_gap(rep)]
        rv = c["review"]
        if streak >= int(rv["miss_weeks_for_board_review"]):
            path = HOME / "reviews" / f"{week_key()}.md"
            if not a.dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text(f"# Board review, week of {week_key()}\n\nMissed targets {streak} weeks running.\n\n"
                                    "## Why I failed\n\n## What I'd stop\n\n## What the next CEO should do\n")
                kv_set(con, "board_review", str(path))
            L.append(f"🚨 Board review due: {streak} missed weeks. I'll write the memo ({path}). You decide: keep me, "
                     "or fire me (swap the CEO addendum in SOUL.md).")
        elif streak >= int(rv["miss_weeks_for_pip"]):
            nxt = week_key(week_start_ts() + 8 * 86400)
            if not a.dry_run and kv_get(con, "pip_week") != nxt:
                half = {k: round(v / 2, 2) for k, v in allowances(con).items()}
                d = new_decision(con, "alloc", f"PIP: halve all allowances for week {nxt}",
                                 f"targets missed {streak} weeks running", {"week": nxt, "alloc": half}, by="system")
                kv_set(con, "pip_week", nxt)
                L.append(f"⚠️ PIP starts Monday (D{d['id']}): half budget, one segment, and I name what I stop.")
            else:
                L.append("⚠️ PIP next week: half budget, one segment.")
        elif rep["missed"]:
            L.append("Missed this week. One more miss puts me on a PIP.")
        if not a.dry_run:
            con.execute("INSERT INTO weeks(week,data,missed,created_at) VALUES(?,?,?,?) ON CONFLICT(week) DO UPDATE SET "
                        "data=excluded.data, missed=excluded.missed, created_at=excluded.created_at",
                        (week_key(), json.dumps(rep), rep["missed"], now()))
            kv_set(con, "scorecard_week", week_key())
    out("\n".join(L), a)


def find_move(con, n):
    m = con.execute("SELECT * FROM moves WHERE status='open' AND n=?", (n,)).fetchone()
    if not m:
        raise Refused(f"no open move #{n}. Open moves:\n{moves_text(con)}")
    return m


def cmd_done(a):
    with db() as con:
        m = find_move(con, a.n)
        leads = json.loads(m["leads"])
        if a.leads:
            want = [x.strip() for x in a.leads.split(",") if x.strip()]
            leads = [x for x in leads if x in want] or want
        ev = MOVE_EVENT.get(m["kind"])
        names = load_leads()
        logged = 0
        if ev:
            for k in leads:
                if not con.execute("SELECT 1 FROM events WHERE lead=? AND kind=?", (k, ev)).fetchone():
                    con.execute("INSERT INTO events(ts,kind,lead,label,note,source) VALUES(?,?,?,?,?,?)",
                                (now(), ev, k, names.get(k, {}).get("company"), f"move {m['id']}", "move"))
                    logged += 1
        con.execute("UPDATE moves SET status='done', closed_at=? WHERE id=?", (now(), m["id"]))
        renumber(con)
        f = funnel(con)
        msg = f"✅ Move done.{f' Logged {logged} × {ev}.' if logged else ''} This week: " + funnel_line(f)
        if m["kind"] == "decide" and not kv_get(con, "offer"):
            msg += "\nTell me the offer text so I can use it: 'offer: <price, scope, length>'."
    print(msg)


def cmd_skip(a):
    with db() as con:
        m = find_move(con, a.n)
        con.execute("UPDATE moves SET status='skipped', reason=?, closed_at=? WHERE id=?", (a.reason, now(), m["id"]))
        renumber(con)
        n = con.execute("SELECT COUNT(*) FROM moves WHERE status='skipped' AND closed_at>=?",
                        (week_start_ts(),)).fetchone()[0]
    print(f"Skipped: {a.reason}. ({n} skip(s) this week.)")


def resolve_lead(ref):
    leads = load_leads()
    if ref in leads:
        return ref, leads[ref]["company"]
    hits = [l for l in leads.values() if ref.lower() in l["company"].lower()]
    if len(hits) == 1:
        return hits[0]["key"], hits[0]["company"]
    if len(hits) > 1:
        raise Refused("several leads match: " + ", ".join(f"{h['key']} {h['company']}" for h in hits[:8]))
    return "X:" + ref.strip().lower()[:60], ref.strip()


def cmd_log(a):
    kind = {"replied": "reply", "booked": "call", "meeting": "call", "proposal": "offer", "won": "sale", "paid": "sale"
            }.get(a.kind, a.kind)
    if kind not in FUNNEL:
        raise Refused(f"kind must be one of {FUNNEL}")
    key, label = resolve_lead(a.lead)
    with db() as con:
        if con.execute("SELECT 1 FROM events WHERE lead=? AND kind=?", (key, kind)).fetchone():
            print(f"already logged: {kind} for {label}")
            return
        con.execute("INSERT INTO events(ts,kind,lead,label,note,source) VALUES(?,?,?,?,?,?)",
                    (now(), kind, key, label, a.note, "founder"))
        if kind == "sale":
            print(f"🎉🎉 FIRST SALE logged: {label}. North star hit." if con.execute(
                "SELECT COUNT(*) FROM events WHERE kind='sale'").fetchone()[0] == 1 else f"💶 Sale logged: {label}")
            return
        print(f"Logged {kind}: {label} ({key}). This week: " + funnel_line(funnel(con)))


def cmd_offer(a):
    with db() as con:
        if a.action == "show":
            print(kv_get(con, "offer") or "No offer decided yet.")
            return
        kv_set(con, "offer", a.text)
        for m in con.execute("SELECT id FROM moves WHERE kind='decide' AND status='open'"):
            con.execute("UPDATE moves SET status='done', closed_at=? WHERE id=?", (now(), m[0]))
        renumber(con)
    print(f"Offer saved: {a.text}")


def cmd_feedback(a):
    v = {"up": 1, "+1": 1, "👍": 1, "yes": 1, "good": 1, "down": -1, "-1": -1, "👎": -1, "no": -1, "bad": -1,
         "meh": 0, "0": 0, "comment": 0}.get(a.verdict.strip().lower())
    if v is None:
        raise Refused("verdict must be up/down/meh (or 👍/👎)")
    with db() as con:
        d = get_decision(con, a.ref)
        con.execute("UPDATE decisions SET feedback=?, feedback_note=?, feedback_at=? WHERE id=?",
                    (v, a.comment, now(), d["id"]))
        if a.comment and v <= 0:
            con.execute("INSERT INTO prefs(ts,text,source) VALUES(?,?,?)", (now(), a.comment, f"D{d['id']}"))
        msg = f"Noted for D{d['id']}: {'👍' if v > 0 else '👎' if v < 0 else '😐'}"
        if v < 0:
            if d["status"] == "proposed":
                con.execute("UPDATE decisions SET status='cancelled' WHERE id=?", (d["id"],))
                if d["kind"] == "order_add":
                    con.execute("UPDATE orders SET status='cancelled' WHERE id=?",
                                (json.loads(d["payload"])["order_id"],))
                msg += ". Proposal cancelled, it won't happen."
            elif d["status"] == "active" and d["reversible"] and d["undo"]:
                if d["argued"]:
                    revert(con, d)
                    msg += ". You said no twice, so it's undone."
                else:
                    con.execute("UPDATE decisions SET status='contested' WHERE id=?", (d["id"],))
                    msg += ". I'll undo it on my next run, or come back once with new info."
            elif d["status"] == "active":
                msg += ". Can't be undone (already ran), so I'll learn from it."
        if a.comment:
            msg += f" Saved as a preference: \"{a.comment}\"" if v <= 0 else ""
    print(msg)


# --------------------------------------------------------------------------- commands: CEO levers
def cmd_budget(a):
    with db() as con:
        if a.action == "plan":
            c = cfg()
            alloc = dict(allowances(con))
            for kv_ in a.pairs:
                k, _, v = kv_.partition("=")
                if k not in AGENTS + ["web"] or not v:
                    raise Refused(f"bad pair {kv_!r}; use agent=usd with agent in {AGENTS + ['web']}")
                alloc[k] = round(float(v), 2)
            total = round(sum(alloc.values()), 2)
            if total > c["budget_usd_week"] + 1e-9:
                raise Refused(f"allocation ${total:.2f} is over the founder's weekly total ${c['budget_usd_week']:.2f}")
            if not a.why:
                raise Refused("--why is required")
            old = allowances(con)
            moved = sum(abs(alloc.get(k, 0) - old.get(k, 0)) for k in set(alloc) | set(old)) / 2
            pct = 100 * moved / c["budget_usd_week"]
            big = pct > float(c["big_decision"]["realloc_pct_over"])
            week = a.week or week_key()
            d = new_decision(con, "alloc", f"allowances {week}: " + " ".join(f"{k}={v:g}" for k, v in alloc.items())
                             + f" (moves {pct:.0f}% of budget)", a.why, {"week": week, "alloc": alloc}, big=big)
            print(dline(d))
            return
        b = budget_state(con)
        if a.json:
            print(json.dumps(b))
        else:
            L = [budget_lines(con, b)]
            past = []
            for i in range(1, 4):
                s = week_start_ts() - i * 7 * 86400
                past.append(f"{local(s).date()}: ${sum(spend_by_agent(s, s + 7 * 86400).values()):.2f}")
            L.append("Runway (last 3 weeks): " + " · ".join(past))
            if a.verify:
                L.append(openrouter_usage())
                L.append("Note: OpenRouter counts everything on the key (cron bots, other tools); the per-agent split above "
                         "covers Hermes profiles + llm-gateway only.")
            print("\n".join(L))
        if b["exhausted"]:
            sys.exit(2)
        if a.agent:
            left = b["alloc"].get(a.agent, 0) - b["spent"].get(a.agent, 0)
            print(f"{a.agent}: ${left:.2f} left")
            if left <= 0:
                sys.exit(2)


def openrouter_usage():
    key = env_get("OPENROUTER_API_KEY")
    if not key:
        return "OpenRouter check: no key found"
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r).get("data", {})
        return (f"OpenRouter (whole key, all agents + other tools): this week ${float(d.get('usage_weekly') or 0):.2f}, "
                f"today ${float(d.get('usage_daily') or 0):.2f}, limit {d.get('limit')} (reset {d.get('limit_reset')})")
    except Exception as e:
        return f"OpenRouter check failed: {type(e).__name__}"


def cmd_card(a):
    if a.worker not in WORKERS:
        raise Refused(f"worker must be one of {sorted(WORKERS)}")
    if not a.why:
        raise Refused("--why is required")
    no_email(a.title, a.body)
    with db() as con:
        agent_room(con, a.worker, a.est)
        d = new_decision(con, "card", f"{a.worker}: {a.title}", a.why,
                         {"worker": a.worker, "title": a.title, "body": a.body or ""}, cost=a.est, reversible=False)
    print(dline(d))


def valid_every(s):
    s = s.lower()
    return s in ("once", "daily", "weekdays", "weekly") or all(x in DAYS for x in s.split(","))


def cmd_order(a):
    with db() as con:
        if a.action == "add":
            if a.agent not in WORKERS | {"samatha"}:
                raise Refused(f"agent must be one of {sorted(WORKERS | {'samatha'})}")
            if not valid_every(a.every):
                raise Refused("--every must be once, daily, weekdays, weekly or days like mon,thu")
            if not a.why:
                raise Refused("--why is required")
            no_email(a.task, a.body)
            cur = con.execute("INSERT INTO orders(agent,task,body,every,budget,until,status,created_at) "
                              "VALUES(?,?,?,?,?,?,?,?)", (a.agent, a.task, a.body, a.every.lower(), a.budget, a.until,
                                                          "proposed", now()))
            oid = cur.lastrowid
            d = new_decision(con, "order_add", f"standing order #{oid}: {a.agent} {a.every} ${a.budget:.2f}/run: {a.task}",
                             a.why, {"order_id": oid}, big=True, cost=0)
            con.execute("UPDATE orders SET decision_id=? WHERE id=?", (d["id"], oid))
            print(dline(d))
            return
        o = con.execute("SELECT * FROM orders WHERE id=?", (a.id,)).fetchone()
        if not o:
            raise Refused(f"no order #{a.id}")
        if not a.why:
            raise Refused("--why is required")
        status = {"pause": "paused", "resume": "active", "stop": "stopped"}[a.action]
        d = new_decision(con, "order_set", f"{a.action} standing order #{o['id']} ({o['agent']}: {o['task'][:50]})",
                         a.why, {"order_id": o["id"], "status": status})
        print(dline(d))


def cmd_orders(a):
    with db() as con:
        rows = con.execute("SELECT * FROM orders WHERE status NOT IN ('cancelled') ORDER BY id").fetchall()
    if not rows:
        print("No standing orders.")
    for o in rows:
        print(f"#{o['id']} [{o['status']}] {o['agent']} {o['every']} ${o['budget']:.2f}/run · runs {o['runs']}"
              f"{' · last ' + hhmm(o['last_run']) if o['last_run'] else ''}{' · until ' + o['until'] if o['until'] else ''}"
              f"\n   {o['task']}{' · ' + o['last_result'] if o['last_result'] else ''}")


def cmd_plan(a):
    with db() as con:
        if a.action == "set":
            if not a.why:
                raise Refused("--why is required")
            no_email(a.text)
            print(dline(new_decision(con, "plan", "week plan: " + a.text[:160], a.why, {"text": a.text})))
        else:
            print(current_plan(con) or "No plan yet.")


def cmd_segments(a):
    with db() as con:
        if a.action == "show":
            print(", ".join(segments(con)))
            return
        segs = [s.strip().lower() for s in a.segs.split(",") if s.strip()]
        bad = [s for s in segs if s not in ("berlin-sme", "dach", "eu", "us", "all")]
        if bad or not segs:
            raise Refused("segments: berlin-sme, dach, eu, us, all")
        if not a.why:
            raise Refused("--why is required")
        print(dline(new_decision(con, "segments", "target segments → " + ",".join(segs), a.why, {"segments": segs},
                                 big=True)))


def cmd_decide(a):
    if not a.why:
        raise Refused("--why is required")
    no_email(a.summary)
    with db() as con:
        d = new_decision(con, "note", a.summary, a.why, big=a.big, cost=a.cost, reversible=not a.irreversible)
    print(dline(d))


def cmd_undo(a):
    with db() as con:
        d = get_decision(con, a.ref)
        if d["status"] == "proposed":
            con.execute("UPDATE decisions SET status='cancelled' WHERE id=?", (d["id"],))
            print(f"D{d['id']} cancelled before it ran.")
        elif revert(con, d):
            print(f"D{d['id']} undone.")
        else:
            con.execute("UPDATE decisions SET status='undone' WHERE id=?", (d["id"],))
            print(f"D{d['id']} marked undone. It has no automatic undo ({d['kind']}): reverse it by hand if needed.")


def cmd_argue(a):
    with db() as con:
        d = get_decision(con, a.ref)
        if d["status"] != "contested":
            raise Refused(f"D{d['id']} is not contested")
        if d["argued"]:
            raise Refused(f"already argued D{d['id']} once; undo it")
        con.execute("UPDATE decisions SET argued=1, status='active', feedback=NULL, ts=? WHERE id=?", (now(), d["id"]))
    notify(f"🤔 D{d['id']} pushback: {d['summary']}\nNew info: {a.text}\n"
           f"Still no? Reply 'D{d['id']} 👎' and it's undone for good. 'D{d['id']} 👍' keeps it.")


def cmd_move(a):
    with db() as con:
        per_day = int(cfg()["founder_moves_per_day"])
        if len(open_moves(con)) >= per_day * 2:
            raise Refused(f"founder already has {len(open_moves(con))} open moves; close or replace one first")
        add_move(con, "custom", a.text)
        renumber(con)
        print(moves_text(con))


def cmd_moves(a):
    with db() as con:
        print(moves_text(con))


def cmd_prefs(a):
    with db() as con:
        if a.action == "add":
            con.execute("INSERT INTO prefs(ts,text,source) VALUES(?,?,?)", (now(), a.text, "founder"))
            print(f"Preference saved: {a.text}")
            return
        rows = con.execute("SELECT * FROM prefs ORDER BY id DESC LIMIT 30").fetchall()
    print("\n".join(f"- {r['text']} ({r['source']}, {local(r['ts']).date()})" for r in rows) or "No preferences yet.")


def cmd_decisions(a):
    with db() as con:
        q, args = "SELECT * FROM decisions", ()
        if a.open:
            q += " WHERE status IN ('proposed','contested')"
        elif a.unrated:
            rows = unrated(con, 14)
            q = None
        elif a.contested:
            q += " WHERE status='contested'"
        if q:
            rows = con.execute(q + " ORDER BY id DESC LIMIT ?", args + (a.limit,)).fetchall()
    if a.json:
        print(json.dumps([dict(r) for r in rows]))
        return
    for d in rows:
        fb = {1: "👍", -1: "👎", 0: "😐"}.get(d["feedback"], "·")
        print(f"D{d['id']} [{d['status']}] {fb} {local(d['ts']).strftime('%d.%m %H:%M')} {d['kind']}: {d['summary']}"
              f"{' · ' + d['result'] if d['result'] else ''}\n   why: {d['why']}"
              f"{' · founder: ' + d['feedback_note'] if d['feedback_note'] else ''}")
    if not rows:
        print("No decisions.")


# --------------------------------------------------------------------------- system: dispatch + context + status
def order_due(o, loc):
    if o["until"]:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", o["until"]) and loc.date().isoformat() > o["until"]:
            return "expired"
    every = o["every"]
    last_day = local(o["last_run"]).date() if o["last_run"] else None
    if every == "once":
        return o["runs"] == 0
    if not (9 <= loc.hour < 20) or last_day == loc.date():
        return False
    if every == "daily":
        return True
    if every == "weekdays":
        return loc.weekday() < 5
    if every == "weekly":
        return last_day is None or (loc.date() - last_day).days >= 7
    return DAYS[loc.weekday()] in every.split(",")


def kpi_met(con, until):
    m = re.match(r"^(\w+)>=(\d+)$", until or "")
    if not m:
        return False
    return funnel(con).get(TARGET_KEYS.get(m.group(1), m.group(1)), 0) >= int(m.group(2))


def cmd_dispatch(a):
    news = []
    with db() as con:
        sync_crm(con)
        for d in con.execute("SELECT * FROM decisions WHERE status='proposed' AND due_at<=?", (now(),)).fetchall():
            execute(con, d["id"])
            d2 = con.execute("SELECT * FROM decisions WHERE id=?", (d["id"],)).fetchone()
            news.append(f"D{d['id']} went ahead (no objection): {d['summary']}"
                        f"{' · ' + d2['result'] if d2['result'] else ''}")
        for d in con.execute("SELECT * FROM decisions WHERE status='contested' AND feedback_at<=?",
                             (now() - 24 * 3600,)).fetchall():
            if revert(con, d):
                news.append(f"D{d['id']} undone (you said 👎 and I had no new argument): {d['summary']}")
        loc = local()
        for o in con.execute("SELECT * FROM orders WHERE status='active' ORDER BY id").fetchall():
            due = order_due(o, loc)
            if due == "expired":
                con.execute("UPDATE orders SET status='stopped', last_result='expired' WHERE id=?", (o["id"],))
                continue
            if not due or kpi_met(con, o["until"]):
                continue
            try:
                agent_room(con, o["agent"], o["budget"])
            except Refused as e:
                if kv_get(con, f"starved:{o['agent']}") != today():
                    kv_set(con, f"starved:{o['agent']}", today())
                    news.append(f"💸 Skipped standing order #{o['id']} ({o['agent']}): {e}")
                continue
            if o["agent"] == "samatha":
                res = run_samatha(o["task"])
            else:
                body = (o["body"] or "") + f"\n\n(Standing order #{o['id']} from the CEO, budget ~${o['budget']:.2f}.)"
                res = run_card(o["agent"], o["task"], body.strip())
            con.execute("UPDATE orders SET last_run=?, runs=runs+1, last_result=? WHERE id=?", (now(), res, o["id"]))
            if o["every"] == "once":
                con.execute("UPDATE orders SET status='done' WHERE id=?", (o["id"],))
            print(f"order #{o['id']} → {res}")
        con.execute("UPDATE moves SET status='expired', closed_at=? WHERE status='open' AND created_at<?",
                    (now(), now() - 7 * 86400))
        renumber(con)
        refresh_alerts(con)
        if a.notify:
            push_crit(con)
    if news:
        out("🛠 CEO dispatch\n" + "\n".join(news), a)


def kanban_ro():
    return sqlite3.connect(f"file:{KANBAN_DB}?mode=ro", uri=True, timeout=5)


def kanban_open():
    """Open crew cards per worker:status (kanban.db, read-only)."""
    try:
        c = kanban_ro()
        rows = c.execute("SELECT assignee, status, COUNT(*) FROM tasks WHERE status NOT IN ('done','archived') "
                         "GROUP BY 1,2").fetchall()
        c.close()
        return {f"{a or '?'}:{s}": n for a, s, n in rows}
    except sqlite3.Error:
        return {"error": "kanban.db unreadable"}


def kanban_week(since, until):
    """Each worker's cards touched in [since, until) with the first line of the latest run summary."""
    res = {w: [] for w in sorted(WORKERS)}
    try:
        c = kanban_ro()
        for tid, who, title, status in c.execute(
                "SELECT id, assignee, title, status FROM tasks WHERE assignee IN ('scout','scribe','ops') AND "
                "COALESCE(completed_at, started_at, created_at) >= ? AND COALESCE(completed_at, started_at, created_at) < ? "
                "ORDER BY created_at", (since, until)).fetchall():
            s = c.execute("SELECT summary FROM task_runs WHERE task_id=? AND summary IS NOT NULL ORDER BY id DESC LIMIT 1",
                          (tid,)).fetchone()
            first = next((ln.strip() for ln in (s[0] if s else "").splitlines() if ln.strip()), "")
            res[who].append({"id": tid, "title": (title or "")[:90], "status": status, "summary": first[:160]})
        c.close()
    except sqlite3.Error:
        pass
    return res


# --------------------------------------------------------------------------- weekly agents meeting
def meeting_on(c=None):
    c = c or cfg()
    return today() >= str(c["meeting"]["start"])


def minutes_for(con, week=None):
    r = con.execute("SELECT minutes FROM meetings WHERE week=?", (week or week_key(),)).fetchone()
    return r[0] if r else None


def standup_path(agent, week=None):
    return INBOX / f"standup-{agent}-{week or week_key()}.md"


def samatha_status():
    if FAKE_EXEC:
        return "(fake) leads_week: 0 · posts_week: 1/3"
    try:
        p = subprocess.run([SAMATHA, "status"], capture_output=True, text=True, timeout=30)
        return (p.stdout or p.stderr).strip()[:800] or "(no output)"
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(samatha status failed: {type(e).__name__})"


def cmd_standups(a):
    """Monday before the meeting: each worker writes a short standup (a cheap crew card)."""
    if not at_gate(a):
        return
    c = cfg()
    if not meeting_on(c) or local().weekday() != 0:
        return
    news = []
    with db() as con:
        if kv_get(con, "standups_week") == week_key():
            print("standups already requested this week")
            return
        alloc = allowances(con)
        for w in sorted(WORKERS):
            try:
                agent_room(con, w, float(c["meeting"]["standup_usd"]))
            except Refused as e:
                news.append(f"{w}: no standup ({e})")
                continue
            body = (f"The CEO chairs the weekly agents meeting at {c['meeting']['hour']:02d}:00 Berlin today. Write your standup "
                    f"(max 6 lines) to {standup_path(w)}:\n1) what you finished last week\n2) what you found that matters "
                    f"for the first paying Bunny Search customer\n3) what blocked you\n4) what you'd do this week with "
                    f"${alloc.get(w, 0):.2f} and why\n5) one request for the CEO.\nUse `hermes kanban list --assignee {w} "
                    "--json` for your history. Facts only, keep it short, finish in under 3 minutes.")
            news.append(f"{w}: {run_card(w, f'Standup for the agents meeting ({week_key()})', body)}")
        kv_set(con, "standups_week", week_key())
    print("\n".join(news))


def meeting_pack(con):
    c = cfg()
    ws = week_start_ts()
    last = ws - 7 * 86400
    lk = week_key(last)
    row = con.execute("SELECT data FROM weeks WHERE week=?", (lk,)).fetchone()
    rep = json.loads(row[0]) if row else week_report(con, last, ws)
    spent, alloc = spend_by_agent(last, ws), allowances(con, lk)
    cards = kanban_week(last, now() + 1)
    L = [f"AGENTS MEETING · week of {week_key()} · you chair it as CEO", north_star_line(con),
         f"LAST WEEK ({lk}) funnel: " + funnel_line(rep["funnel"]) +
         f" · CEO grade {rep.get('ceo_grade')} · founder grade {rep.get('founder_grade')} · "
         f"agreement {rep.get('agreement_pct')}% of {rep.get('rated')} rated",
         f"Spend last week: ${sum(spent.values()):.2f} of ${c['budget_usd_week']:.0f}"]
    for agent in AGENTS:
        L += ["", f"## {agent.upper()} · spent ${spent.get(agent, 0):.2f} of ${alloc.get(agent, 0):.2f}"]
        for o in con.execute("SELECT * FROM orders WHERE agent=? AND status IN ('active','paused')", (agent,)):
            L.append(f"  order #{o['id']} [{o['status']}] {o['every']} ${o['budget']:.2f}: {o['task'][:70]} "
                     f"(runs {o['runs']}{', last: ' + o['last_result'][:60] if o['last_result'] else ''})")
        for t in cards.get(agent, []):
            L.append(f"  card {t['id']} [{t['status']}] {t['title']}{' → ' + t['summary'] if t['summary'] else ''}")
        if agent in WORKERS:
            try:
                L.append("  STANDUP:\n    " + standup_path(agent).read_text()[:1200].strip().replace("\n", "\n    "))
            except OSError:
                L.append("  STANDUP: none filed")
        if agent == "samatha":
            L.append("  STATUS:\n    " + samatha_status().replace("\n", "\n    "))
        if agent == "ceo":
            ds = con.execute("SELECT * FROM decisions WHERE ts>=? AND ts<? AND by='ceo' ORDER BY id", (last, ws)).fetchall()
            L += [f"  D{d['id']} [{d['status']}] {({1: '👍', -1: '👎', 0: '😐'}).get(d['feedback'], '·')} {d['summary'][:80]}"
                  f"{' · founder: ' + d['feedback_note'] if d['feedback_note'] else ''}" for d in ds] or ["  no decisions"]
    L += ["", f"## web (Open WebUI via llm-gateway) · spent ${spent.get('web', 0):.2f} of ${alloc.get('web', 0):.2f}",
          "", "## FOUNDER"]
    m = rep.get("moves") or {}
    L.append(f"  moves done {m.get('done', 0)} · skipped {m.get('skipped', 0)} · expired {m.get('expired', 0)}")
    L += [f"  skipped: {s['text'][:50]} ({s['reason']})" for s in con.execute(
        "SELECT text,reason FROM moves WHERE status='skipped' AND closed_at>=? AND closed_at<?", (last, ws))]
    L += ["", "FOUNDER PREFERENCES:"] + ([f"  - {r['text']}" for r in con.execute(
        "SELECT text FROM prefs ORDER BY id DESC LIMIT 20")] or ["  none"])
    L += ["", "NOW: plan: " + (current_plan(con) or "none") + " · segments: " + ", ".join(segments(con)) +
          " · offer: " + (kv_get(con, "offer") or "NOT DECIDED"),
          f"Targets {json.dumps(c['week_targets'])} · weekly total ${c['budget_usd_week']:.0f} · "
          f"this week's allowances so far {json.dumps(allowances(con))}"]
    return "\n".join(L)


def cmd_meeting_pack(a):
    with db() as con:
        sync_crm(con)
        print(meeting_pack(con))


def cmd_minutes(a):
    with db() as con:
        if a.action == "show":
            print(minutes_for(con, a.week) or "No minutes for that week.")
            return
        text = a.text if a.text else sys.stdin.read()
        if not text.strip():
            raise Refused("empty minutes")
        no_email(text)
        con.execute("INSERT INTO meetings(week,ts,minutes) VALUES(?,?,?) ON CONFLICT(week) DO UPDATE SET "
                    "ts=excluded.ts, minutes=excluded.minutes", (week_key(), now(), text.strip()))
        p = HOME / "meetings" / f"{week_key()}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# Agents meeting · week of {week_key()}\n\n{text.strip()}\n")
    print(f"Minutes saved ({p}).")


# --------------------------------------------------------------------------- notifications (UI tab + Telegram for crit)
def current_alerts(con):
    """Conditions that are true right now. Keys are stable per condition and period, so state (read/dismissed)
    sticks while the condition lasts and a new period raises a fresh alert."""
    c = cfg()
    out = []

    def add(key, source, level, title, body=""):
        out.append({"key": key, "source": source, "level": level, "title": title, "body": body})

    b = budget_state(con)
    wk, tot, sp = b["week"], b["total"], b["total_spent"]
    pct = sp / tot if tot else 0
    days_left = 7 - local().weekday()
    if pct >= 1:
        add(f"budget:week:{wk}:100", "budget", "crit", f"Weekly AI budget used up: ${sp:.2f} of ${tot:g}",
            "The CEO and its agents are report-only until Monday. Raise budget_usd_week in /root/ceo/ceo.yaml to "
            "unblock them.")
    elif pct >= 0.8:
        add(f"budget:week:{wk}:80", "budget", "warn", f"{pct:.0%} of the weekly AI budget used: ${sp:.2f} of ${tot:g}",
            f"{days_left} day(s) left in the week. Agents stop when it's used up.")
    if b["projected_week"] is not None and b["projected_week"] > tot and pct < 1:
        add(f"budget:pace:{wk}", "budget", "warn", f"On pace to overspend this week: ~${b['projected_week']:.2f} of ${tot:g}",
            "Projected from spend so far this week.")
    for agent in AGENTS + ["web"]:
        s_, al = b["spent"].get(agent, 0), b["alloc"].get(agent, 0)
        if s_ > al and s_ > 0.01:
            hint = ("Plank's own Telegram chats count here: long chats are the biggest cost, start fresh ones more often."
                    if agent == "ceo" else "Its standing orders are skipped until money moves (`ceo budget plan`) or Monday.")
            add(f"budget:agent:{wk}:{agent}", "budget", "warn", f"{agent} is over its allowance: ${s_:.2f} of ${al:.2f}", hint)
    k = b.get("key")
    if k and k.get("remaining") is not None:
        rem, floor = float(k["remaining"]), float(c.get("key_floor_usd", 3))
        month = local().strftime("%Y-%m")
        if rem < floor:
            add(f"budget:key:{month}:floor", "budget", "crit", f"OpenRouter key almost empty: ${rem:.2f} left",
                f"Below the ${floor:g} floor: the CEO is report-only and the Opus meeting is skipped so Plank keeps "
                "working. Raise the key's limit at openrouter.ai (Settings → API Keys) or wait for the reset.")
        elif rem < 2 * floor:
            add(f"budget:key:{month}:low", "budget", "warn", f"OpenRouter key running low: ${rem:.2f} of ${k.get('limit')} left")
        if k.get("reset") == "monthly":
            d = local().date()
            reset = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
            elapsed = max(1, local().weekday() + 1)
            daily = float(k.get("weekly") or 0) / elapsed
            if daily > 0 and rem / daily < (reset - d).days:
                empty = d + timedelta(days=int(rem / daily))
                add(f"budget:runway:{month}", "budget", "crit" if rem / daily < 3 else "warn",
                    f"At this pace the OpenRouter key runs out around {empty.strftime('%a %d %b')}",
                    f"${rem:.2f} left, burning ~${daily:.2f}/day, limit resets {reset.strftime('%d %b')}. Everything "
                    "on the key stops when it's empty, Plank included.")
    n = len(unrated(con))
    if n:
        add(f"ceo:unrated:{today()}", "ceo", "info", f"{n} CEO decision{'s' if n > 1 else ''} wait for your 👍/👎",
            "Reply in Telegram, e.g. 'D3 👍' or 'D3 👎 <why>'. Your answers steer what the CEO does next.")
    for d in proposals(con):
        if d["due_at"] - now() < 6 * 3600:
            add(f"ceo:proposal:{d['id']}", "ceo", "warn", f"D{d['id']} goes ahead {hhmm(d['due_at'])} unless you object",
                d["summary"])
    nc = con.execute("SELECT COUNT(*) FROM decisions WHERE status='contested'").fetchone()[0]
    if nc:
        add(f"ceo:contested:{today()}", "ceo", "info", f"{nc} decision{'s' if nc > 1 else ''} you rejected: the CEO "
            "undoes them on its next run")
    if kv_get(con, "pip_week") == wk:
        add(f"ceo:pip:{wk}", "ceo", "warn", "The CEO is on a PIP this week", "Targets missed 2 weeks running: half budget, one segment.")
    if review_due(con):
        add(f"ceo:review:{wk}", "ceo", "crit", "Board review due: targets missed 3 weeks running",
            f"Read the CEO's memo ({kv_get(con, 'board_review')}) and decide: keep it, or swap the SOUL addendum.")
    s = first_sale(con)
    if s:
        add("ceo:first-sale", "ceo", "info", f"🎉 First sale: {s['label'] or s['lead']}", "North star hit.")
    return out


def refresh_alerts(con):
    """Upsert current conditions, resolve the ones that cleared. Returns the alerts that are new this call."""
    cur = current_alerts(con)
    new = []
    for a in cur:
        row = con.execute("SELECT key FROM alerts WHERE key=?", (a["key"],)).fetchone()
        if row:
            con.execute("UPDATE alerts SET level=?, title=?, body=?, last_seen=?, resolved_at=NULL WHERE key=?",
                        (a["level"], a["title"], a["body"], now(), a["key"]))
        else:
            con.execute("INSERT INTO alerts(key,source,level,title,body,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)",
                        (a["key"], a["source"], a["level"], a["title"], a["body"], now(), now()))
            new.append(a)
    keys = [a["key"] for a in cur]
    con.execute(f"UPDATE alerts SET resolved_at=? WHERE resolved_at IS NULL AND key NOT IN ({','.join('?' * len(keys)) or 'NULL'})",
                [now()] + keys)
    return new


def alert_rows(con):
    rows = [dict(r) for r in con.execute("SELECT * FROM alerts WHERE COALESCE(resolved_at, dismissed_at, last_seen) >= ? "
                                         "ORDER BY first_seen DESC LIMIT 200", (now() - 14 * 86400,))]
    rank = {"crit": 0, "warn": 1, "info": 2}
    active = sorted([r for r in rows if not r["resolved_at"] and not r["dismissed_at"]],
                    key=lambda r: (rank.get(r["level"], 3), -r["first_seen"]))
    earlier = [r for r in rows if r["resolved_at"] or r["dismissed_at"]][:50]
    return active, earlier


def push_crit(con):
    """Active critical alerts also reach Telegram, once each (from the hourly dispatch, never from the UI path)."""
    sent = []
    for r in con.execute("SELECT * FROM alerts WHERE level='crit' AND pushed=0 AND resolved_at IS NULL "
                         "AND dismissed_at IS NULL").fetchall():
        notify(f"🔴 {r['title']}\n{r['body']}\n(ui.techno-wanderlust.com → Notifications)")
        con.execute("UPDATE alerts SET pushed=1 WHERE key=?", (r["key"],))
        sent.append(r["title"])
    return sent


def cmd_alerts(a):
    with db() as con:
        if a.action in ("read", "dismiss"):
            col = "read_at" if a.action == "read" else "dismissed_at"
            if a.all:
                n = con.execute(f"UPDATE alerts SET {col}=? WHERE {col} IS NULL AND resolved_at IS NULL", (now(),)).rowcount
            elif a.key:
                n = con.execute(f"UPDATE alerts SET {col}=COALESCE({col}, ?) WHERE key=?", (now(), a.key)).rowcount
                if not n:
                    raise Refused(f"no alert {a.key}")
            else:
                raise Refused("give an alert key or --all")
            if a.action == "dismiss" and not a.all:
                con.execute("UPDATE alerts SET read_at=COALESCE(read_at, ?) WHERE key=?", (now(), a.key))
            print(json.dumps({"ok": True, "changed": n}) if a.json else f"{n} alert(s) marked {a.action}")
            return
        refresh_alerts(con)
        active, earlier = alert_rows(con)
        b = budget_state(con)
        k = b.get("key") or {}
        payload = {"ok": True, "generated_at": now(), "unread": sum(1 for r in active if not r["read_at"]),
                   "active": active, "earlier": earlier,
                   "budget": {"week": b["week"], "spent": b["total_spent"], "total": b["total"],
                              "projected": b["projected_week"], "by_agent": b["spent"], "alloc": b["alloc"],
                              "key_remaining": k.get("remaining"), "key_limit": k.get("limit"), "key_reset": k.get("reset"),
                              "key_low": b["key_low"]}}
    if a.json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    icon = {"crit": "🔴", "warn": "🟠", "info": "🔵"}
    print("\n".join(f"{icon.get(r['level'], '·')} {'' if r['read_at'] else '● '}{r['title']} [{r['key']}]" for r in active)
          or "No active alerts.")


def cmd_gate(a):
    """Pre-script for the Hermes cron jobs: prints the snapshot, then a wake-gate line.
    Hermes skips the LLM entirely when the last line is {"wakeAgent": false} (no cost, no message)."""
    c = cfg()
    loc = local()
    wake, body = True, ""
    with db() as con:
        sync_crm(con)
        if a.job == "meeting":
            if not meeting_on(c) or loc.weekday() != 0 or loc.hour != int(c["meeting"]["hour"]) or minutes_for(con):
                wake = False
            elif budget_state(con)["key_low"]:
                wake = False
                notify("🗓 Agents meeting skipped: the OpenRouter key is almost at its monthly limit, so Plank keeps working.")
            else:
                body = meeting_pack(con)
        else:
            slot = f"{today()}:{loc.hour}"
            if loc.weekday() >= 5 or loc.hour not in [int(h) for h in c["operate_hours"]]:
                wake = False
            elif meeting_on(c) and loc.weekday() == 0 and loc.hour == int(c["meeting"]["hour"]):
                wake = False  # the agents meeting replaces Monday's first operate run
            elif kv_get(con, "operate_slot") == slot:
                wake = False
            else:
                kv_set(con, "operate_slot", slot)
    if a.job == "operate" and wake:
        cmd_context(a)
    elif body:
        print(body)
    print(json.dumps({"wakeAgent": wake}))


def review_due(con):
    p = kv_get(con, "board_review")
    try:
        return bool(p) and "## Why I failed\n\n##" in Path(p).read_text()
    except OSError:
        return False


def cmd_context(a):
    """Everything the ceo-operate agent needs in one read, plus the mode it must run in."""
    c = cfg()
    with db() as con:
        sync_crm(con)
        b = budget_state(con)
        ceo_left = b["alloc"].get("ceo", 0) - b["spent"].get("ceo", 0)
        mode = "NORMAL"
        if b["exhausted"] or ceo_left <= 0:
            mode = "REPORT-ONLY (budget used up: no cards, no orders, just report)"
        elif review_due(con):
            mode = f"BOARD REVIEW DUE: fill in {kv_get(con, 'board_review')} (the 3 empty sections) first, then operate"
        elif kv_get(con, "pip_week") == week_key():
            mode = "PIP: one segment, half budget; name one thing you stop this week (ceo decide)"
        f = funnel(con)
        _, pool = candidates(con, 0)
        L = [f"MODE: {mode}", north_star_line(con),
             f"Week {week_key()} funnel: " + funnel_line(f) + f" · weekdays left {weekdays_left()}",
             budget_lines(con, b),
             "Plan: " + (current_plan(con) or "NONE: set one with `ceo plan set`"),
             "Segments: " + ", ".join(segments(con)) + f" ({pool} uncontacted leads) · Offer: " + (kv_get(con, "offer") or "NOT DECIDED"),
             "", "CONTESTED (founder said 👎: undo it, or argue once with NEW info):"]
        L += [f"  D{d['id']}: {d['summary']} · founder: {d['feedback_note'] or '-'}" for d in
              con.execute("SELECT * FROM decisions WHERE status='contested'")] or ["  none"]
        L += ["PROPOSALS waiting out the objection window:"]
        L += [f"  D{d['id']} at {hhmm(d['due_at'])}: {d['summary']}" for d in proposals(con)] or ["  none"]
        L += ["FOUNDER PREFERENCES (respect these):"]
        L += [f"  - {r['text']}" for r in con.execute("SELECT text FROM prefs ORDER BY id DESC LIMIT 20")] or ["  none"]
        L += ["RECENT FEEDBACK:"]
        L += [f"  D{r['id']} {({1: '👍', -1: '👎', 0: '😐'})[r['feedback']]} {r['summary'][:60]}"
              f"{' · ' + r['feedback_note'] if r['feedback_note'] else ''}" for r in con.execute(
                  "SELECT * FROM decisions WHERE feedback IS NOT NULL ORDER BY feedback_at DESC LIMIT 20")] or ["  none"]
        L += ["STANDING ORDERS:"]
        L += [f"  #{o['id']} [{o['status']}] {o['agent']} {o['every']} ${o['budget']:.2f} runs {o['runs']}: {o['task'][:70]}"
              for o in con.execute("SELECT * FROM orders WHERE status IN ('active','paused','proposed')")] or ["  none"]
        L += ["FOUNDER MOVES (open):", "  " + moves_text(con).replace("\n", "\n  ")]
        sk = con.execute("SELECT text,reason FROM moves WHERE status='skipped' AND closed_at>=?", (week_start_ts(),)).fetchall()
        if sk:
            L += ["Skipped this week: " + "; ".join(f"{s['text'][:40]} ({s['reason']})" for s in sk)]
        L += ["CREW BOARD (open cards): " + json.dumps(kanban_open())]
        L += ["", f"Targets: {json.dumps(c['week_targets'])} · weekly total ${c['budget_usd_week']:.0f} · "
              f"big decision = >${c['big_decision']['usd_over']} or >{c['big_decision']['realloc_pct_over']}% realloc "
              f"(objection window {c['big_decision']['objection_hours']}h)"]
    print("\n".join(L))


def cmd_status(a):
    with db() as con:
        sync_crm(con)
        b = budget_state(con)
        f = funnel(con)
        s = first_sale(con)
        d = {"north_star_hit": bool(s), "funnel": f, "targets": cfg()["week_targets"],
             "spent": b["total_spent"], "budget": b["total"], "by_agent": b["spent"], "alloc": b["alloc"],
             "open_moves": len(open_moves(con)), "unrated": len(unrated(con)), "proposals": len(proposals(con)),
             "contested": con.execute("SELECT COUNT(*) FROM decisions WHERE status='contested'").fetchone()[0],
             "orders_active": con.execute("SELECT COUNT(*) FROM orders WHERE status='active'").fetchone()[0],
             "pip": kv_get(con, "pip_week") == week_key(), "offer": kv_get(con, "offer"),
             "key_remaining": (b["key"] or {}).get("remaining"), "key_low": b["key_low"],
             "meeting_minutes": bool(minutes_for(con)),
             "plan": current_plan(con)}
    if a.json:
        print(json.dumps(d))
        return
    with db() as con:
        print("\n".join([north_star_line(con), "This week: " + funnel_line(f), budget_lines(con, b),
                         f"Open moves {d['open_moves']} · decisions awaiting your take {d['unrated']} · proposals "
                         f"{d['proposals']} · contested {d['contested']} · standing orders {d['orders_active']}",
                         "Offer: " + (d["offer"] or "not decided"), "Plan: " + (d["plan"] or "none")]))


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(prog="ceo")
    sp = ap.add_subparsers(dest="cmd", required=True)

    def P(name, f, *args):
        p = sp.add_parser(name)
        p.set_defaults(f=f)
        return p

    p = P("brief", cmd_brief); p.add_argument("--week", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true"); p.add_argument("--at"); p.add_argument("--notify", action="store_true")
    p = P("checkin", cmd_checkin); p.add_argument("--at"); p.add_argument("--notify", action="store_true")
    p = P("scorecard", cmd_scorecard); p.add_argument("--at"); p.add_argument("--notify", action="store_true")
    p.add_argument("--force", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p = P("done", cmd_done); p.add_argument("n", type=int); p.add_argument("--leads")
    p = P("skip", cmd_skip); p.add_argument("n", type=int); p.add_argument("reason")
    p = P("log", cmd_log); p.add_argument("kind"); p.add_argument("lead"); p.add_argument("--note")
    p = P("offer", cmd_offer); p.add_argument("action", choices=["set", "show"]); p.add_argument("text", nargs="?")
    p = P("feedback", cmd_feedback); p.add_argument("ref"); p.add_argument("verdict"); p.add_argument("comment", nargs="?")
    p = P("budget", cmd_budget); p.add_argument("action", nargs="?", choices=["show", "plan"], default="show")
    p.add_argument("pairs", nargs="*"); p.add_argument("--why"); p.add_argument("--week"); p.add_argument("--agent")
    p.add_argument("--json", action="store_true"); p.add_argument("--verify", action="store_true")
    p = P("card", cmd_card); p.add_argument("worker"); p.add_argument("title"); p.add_argument("body", nargs="?")
    p.add_argument("--why"); p.add_argument("--est", type=float, default=0.3)
    p = P("order", cmd_order); p.add_argument("action", choices=["add", "pause", "resume", "stop"])
    p.add_argument("agent_or_id"); p.add_argument("task", nargs="?"); p.add_argument("--body")
    p.add_argument("--every", default="weekly"); p.add_argument("--budget", type=float, default=0.3)
    p.add_argument("--until"); p.add_argument("--why")
    P("orders", cmd_orders)
    p = P("plan", cmd_plan); p.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    p.add_argument("text", nargs="?"); p.add_argument("--why")
    p = P("segments", cmd_segments); p.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    p.add_argument("segs", nargs="?"); p.add_argument("--why")
    p = P("decide", cmd_decide); p.add_argument("summary"); p.add_argument("--why"); p.add_argument("--big", action="store_true")
    p.add_argument("--irreversible", action="store_true"); p.add_argument("--cost", type=float, default=0.0)
    p = P("undo", cmd_undo); p.add_argument("ref")
    p = P("argue", cmd_argue); p.add_argument("ref"); p.add_argument("text")
    p = P("move", cmd_move); p.add_argument("action", choices=["add"]); p.add_argument("text")
    P("moves", cmd_moves)
    p = P("prefs", cmd_prefs); p.add_argument("action", nargs="?", choices=["show", "add"], default="show")
    p.add_argument("text", nargs="?")
    p = P("pref", cmd_prefs); p.add_argument("action", choices=["add"]); p.add_argument("text")
    p = P("decisions", cmd_decisions); p.add_argument("--open", action="store_true")
    p.add_argument("--unrated", action="store_true"); p.add_argument("--contested", action="store_true")
    p.add_argument("--json", action="store_true"); p.add_argument("--limit", type=int, default=20)
    p = P("dispatch", cmd_dispatch); p.add_argument("--notify", action="store_true")
    P("context", cmd_context)
    p = P("gate", cmd_gate); p.add_argument("job", choices=["operate", "meeting"])
    p = P("alerts", cmd_alerts); p.add_argument("action", nargs="?", choices=["list", "read", "dismiss"], default="list")
    p.add_argument("key", nargs="?"); p.add_argument("--all", action="store_true"); p.add_argument("--json", action="store_true")
    p = P("standups", cmd_standups); p.add_argument("--at")
    P("meeting-pack", cmd_meeting_pack)
    p = P("minutes", cmd_minutes); p.add_argument("action", choices=["save", "show"]); p.add_argument("text", nargs="?")
    p.add_argument("--week")
    p = P("status", cmd_status); p.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "order":
        if a.action == "add":
            a.agent = a.agent_or_id
            if not a.task:
                ap.error("order add AGENT \"task\" --every SPEC --budget USD --why ...")
        else:
            a.id = int(a.agent_or_id.lstrip("#"))
    if a.cmd == "offer" and a.action == "set" and not a.text:
        ap.error("offer set \"<text>\"")
    if a.cmd == "plan" and a.action == "set" and not a.text:
        ap.error("plan set \"<text>\" --why ...")
    if a.cmd == "segments" and a.action == "set" and not a.segs:
        ap.error("segments set a,b --why ...")
    if a.cmd in ("prefs", "pref") and a.action == "add" and not a.text:
        ap.error("pref add \"<text>\"")
    try:
        a.f(a)
    except Refused as e:
        print(f"⛔ {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()

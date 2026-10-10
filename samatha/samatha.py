#!/usr/local/lib/hermes-agent/venv/bin/python
"""samatha — LinkedIn social media manager engine behind the @Samatha Telegram bot (Hermes profile `samatha`).

The Hermes agent is only the conversational front-end; every expensive or stateful step lives here so it is capped,
logged and testable. LLM calls go ONLY through the tool-less capped llm-gateway (172.17.0.1:8650), never the agent model.

Subcommands (all print Telegram-ready text; --json where useful):
  idea "<topic>" [--research FILE|JSON] [--wait]   hooks (GLM) -> 3 drafts (Claude) -> filters -> critics -> <=2 revisions
  show [RUN]                                       show the 3 options of a run (default: latest)
  pick N [--run RUN]                               choose option N, prints the clean post for copy-paste
  revise N "instructions" [--run RUN]              rewrite one option with your notes, re-score
  posted [N] --url URL [--text-file F|--text T]     log a published post (+ edit distance -> example feedback loop)
  lead NAME [--post URL] [--note T]                log a lead / qualified DM (north-star KPI)
  metrics POST --impressions .. --reactions ..     manual per-post numbers (POST = url, activity id or post id)
  import-posts PATH                                LinkedIn data export (zip or Shares.csv)
  import-analytics PATH                            LinkedIn analytics export (.xlsx/.csv, bulk or single-post)
  kpi | week | alerts [--notify] | plan | calibrate [--apply] | remind | status --json | voice
The LinkedIn Drafter in Open WebUI is the fallback: on cap/gateway failure Samatha says so and points there.
"""
import argparse
import csv
import difflib
import io
import json
import math
import os
import random
import re
import sqlite3
import statistics
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

HOME = Path(os.environ.get("SAMATHA_HOME", "/root/samatha"))
SKILL = Path(os.environ.get("SAMATHA_SKILL", "/root/.hermes/profiles/samatha/skills/linkedin"))
PROFILE_ENV = Path(os.environ.get("SAMATHA_ENV", "/root/.hermes/profiles/samatha/.env"))
DB_PATH = HOME / "samatha.db"
GATEWAY = os.environ.get("SAMATHA_GATEWAY", "http://172.17.0.1:8650")
GW_TOKEN_FILE = os.environ.get("SAMATHA_GW_TOKEN_FILE",
                               "/var/lib/docker/volumes/open-webui/_data/linkedin_drafter/.gateway_token")
FAKE = os.environ.get("SAMATHA_FAKE_LLM") == "1"
FALLBACK_HINT = ("Fallback: open ui.techno-wanderlust.com → sidebar 'LinkedIn Drafter', paste the idea there "
                 "(separate pipeline + budget path).")

HOOK_TYPES = ["contrarian", "data", "story", "question", "stat_shock", "myth_bust", "listicle", "prediction"]
COLD_START_ORDER = ["story", "contrarian", "data", "question", "myth_bust", "listicle", "prediction", "stat_shock"]

# Same hard rules as the drafter (linkedin_drafter_router.py analyze()), plus VOICE.md forbidden words.
AI_ISMS = re.compile(
    r"\b(game[- ]?chang\w*|delve\w*|unlock\w*|supercharg\w*|leverag\w*|in today'?s (fast[- ]paced|digital|competitive)|"
    r"landscape|tapestry|elevat\w*|seamless\w*|revolutioni[sz]\w*|cutting[- ]edge|synerg\w*|embark\w*|"
    r"thrilled|excited to (announce|share)|let that sink in|here'?s the thing|buckle up)\b", re.I)
LINK_RE = re.compile(r"(https?://|www\.)\S+", re.I)
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
ACTIVITY_RE = re.compile(r"(\d{16,20})")


class CapReached(Exception):
    pass


# --------------------------------------------------------------------------- config / storage
def load_yaml(name, default=None):
    p = SKILL / name
    try:
        return yaml.safe_load(p.read_text()) or {}
    except OSError:
        return default if default is not None else {}


def cfg():
    c = load_yaml("samatha.yaml")
    m = c.get("models") or {}
    return {
        "hooks": m.get("hooks", "z-ai/glm-5.3-flash"),
        "hooks_fallback": m.get("hooks_fallback", "deepseek/deepseek-v4.1-flash"),
        "draft": m.get("draft", "anthropic/claude-sonnet-5"),
        "structural": m.get("structural", "deepseek/deepseek-v4.1-flash"),
        "voice": m.get("voice", "anthropic/claude-sonnet-5"),
        "cheap": m.get("cheap", "deepseek/deepseek-v4.1-flash"),
        "daily_cap_usd": float(c.get("daily_cap_usd", 1.50)),
        "per_idea_cap_usd": float(c.get("per_idea_cap_usd", 0.35)),
        "max_revisions": int(c.get("max_revisions", 2)),
        "examples_in_prompt": int(c.get("examples_in_prompt", 20)),
    }


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, research TEXT, pillar TEXT,
  status TEXT NOT NULL, step TEXT NOT NULL DEFAULT '', error TEXT, cost REAL NOT NULL DEFAULT 0, hooks TEXT,
  created_at INTEGER NOT NULL, finished_at INTEGER, notified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS drafts(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, slot INTEGER NOT NULL,
  hook_type TEXT NOT NULL, hook TEXT NOT NULL, explore INTEGER NOT NULL DEFAULT 0, pillar TEXT, text TEXT NOT NULL,
  flags TEXT NOT NULL DEFAULT '[]', scores TEXT, weighted REAL, passed INTEGER NOT NULL DEFAULT 0,
  revisions INTEGER NOT NULL DEFAULT 0, selected INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id INTEGER, activity_id TEXT UNIQUE, url TEXT,
  text TEXT, published_at INTEGER NOT NULL, source TEXT NOT NULL, hook_type TEXT, pillar TEXT, edit_distance REAL,
  rubric_scores TEXT, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL, captured_at INTEGER NOT NULL,
  impressions INTEGER, reactions INTEGER, comments INTEGER, reposts INTEGER, engagements INTEGER, saves INTEGER, sends INTEGER,
  profile_views INTEGER, followers_gained INTEGER, source TEXT);
CREATE INDEX IF NOT EXISTS idx_metrics_post ON metrics(post_id);
CREATE TABLE IF NOT EXISTS followers(day TEXT PRIMARY KEY, new_followers INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS profile_views(week TEXT PRIMARY KEY, views INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, post_id INTEGER, note TEXT, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, run_id INTEGER, tag TEXT, model TEXT,
  ok INTEGER NOT NULL, cost REAL NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS alerts(key TEXT PRIMARY KEY, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kpi_snapshots(week TEXT PRIMARY KEY, data TEXT NOT NULL, created_at INTEGER NOT NULL);
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


def now():
    return int(time.time())


def utc_day_start():
    d = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp())


def week_start(ts=None):
    d = datetime.fromtimestamp(ts if ts is not None else now(), timezone.utc).date()
    return d - timedelta(days=d.weekday())


def ws_ts(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


# --------------------------------------------------------------------------- LLM via llm-gateway
def spent_today(con):
    return con.execute("SELECT COALESCE(SUM(cost),0) FROM calls WHERE ts>=?", (utc_day_start(),)).fetchone()[0]


def llm(system, user, *, model, tag, run_id=None, max_tokens=1500, temperature=0.7):
    c = cfg()
    with db() as con:
        if spent_today(con) >= c["daily_cap_usd"]:
            raise CapReached(f"Samatha daily budget ${c['daily_cap_usd']:.2f} reached")
        if run_id is not None:
            rc = con.execute("SELECT cost FROM runs WHERE id=?", (run_id,)).fetchone()
            if rc and rc[0] >= c["per_idea_cap_usd"]:
                raise CapReached(f"per-idea budget ${c['per_idea_cap_usd']:.2f} reached")
    if FAKE:
        text, cost = fake_llm(system, user, tag), 0.001
    else:
        tok = Path(GW_TOKEN_FILE).read_text().strip()
        body = json.dumps({"system": system, "user": user, "model": model, "max_tokens": max_tokens,
                           "temperature": temperature, "tag": ("sam:" + tag)[:40]}).encode()
        err = None
        text, cost = None, 0.0
        for attempt in range(2):
            try:
                req = urllib.request.Request(GATEWAY + "/complete", body,
                                             {"X-Gateway-Token": tok, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=110) as r:
                    d = json.load(r)
                text, cost = d.get("text", ""), float((d.get("usage") or {}).get("cost") or 0.0)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    log_call(run_id, tag, model, False, 0, "gateway daily cap")
                    raise CapReached("llm-gateway daily cap reached (shared with LinkedIn Drafter)")
                err = f"gateway HTTP {e.code}: {e.read()[:200]!r}"
                if e.code not in (502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                err = f"gateway unreachable ({type(e).__name__})"
            time.sleep(2)
        if text is None:
            log_call(run_id, tag, model, False, 0, err)
            raise RuntimeError(err)
    log_call(run_id, tag, model, True, cost, None)
    return text


def log_call(run_id, tag, model, ok, cost, error):
    with db() as con:
        con.execute("INSERT INTO calls(ts,run_id,tag,model,ok,cost,error) VALUES(?,?,?,?,?,?,?)",
                    (now(), run_id, tag, model, 1 if ok else 0, cost, error))
        if run_id is not None and cost:
            con.execute("UPDATE runs SET cost=cost+? WHERE id=?", (cost, run_id))


def fake_llm(system, user, tag):
    """Deterministic offline stand-in (SAMATHA_FAKE_LLM=1) so the pipeline + KPI math can be tested for $0."""
    if tag.startswith("hooks"):
        return json.dumps({"pillar": "build-in-public", "hooks": [
            {"type": t, "text": f"{t} hook about the topic in plain words"} for t in HOOK_TYPES] + [
            {"type": "data", "text": "this hook is far too long and should be rejected by the fifteen word length validator for sure"}]})
    if tag.startswith("struct"):
        return json.dumps({"hook": 8, "clarity": 8, "insight": 8, "cta": 7, "formatting": 8, "factual": "pass",
                           "instructions": ""})
    if tag.startswith("voice"):
        return json.dumps({"voice": 8, "instructions": ""})
    if tag.startswith("plan"):
        return "1. [build-in-public] What broke this week\n2. [creator-sourcing] Vetting creators in 10 minutes\n3. [explore: opinion] Databases go stale"
    if tag.startswith("why"):
        return "- Top post opened with a concrete number.\n- Bottom post had a vague hook."
    body = ("I tried something small this week and it changed how I work.\n\n" +
            "Short sentences. Honest numbers. Nothing finished yet.\n\n" * 12 + "What would you try first?")
    return body


def parse_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    for s in (m.group(0), re.sub(r",\s*([}\]])", r"\1", m.group(0))):
        try:
            return json.loads(s)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- voice + examples
def voice_text():
    try:
        return (SKILL / "VOICE.md").read_text()
    except OSError:
        return ""


def forbidden_words():
    m = re.search(r"FORBIDDEN WORDS:\s*(.+)", voice_text())
    return [w.strip().lower() for w in m.group(1).split(",") if w.strip()] if m else []


def pillars():
    m = re.search(r"PILLARS:\s*(.+)", voice_text())
    return [p.strip() for p in m.group(1).split("|") if p.strip()] if m else []


def load_examples(kind, limit):
    d = SKILL / "examples" / kind
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[:limit]:
        t = p.read_text().strip()
        if t:
            out.append(t)
    return out


def writer_system():
    c = cfg()
    pos = load_examples("positive", c["examples_in_prompt"])
    neg = load_examples("negative", 3)
    parts = ["You ghost-write LinkedIn posts for ONE specific founder, in their own voice. You are not a marketer.",
             "The TOPIC is the subject from hook to last line. Use ONLY facts from the topic, research and voice file; never invent "
             "customers, numbers, quotes, results or events. A hypothetical scenario must be labelled hypothetical.",
             "Structure: hook (the given hook, may be lightly adapted) -> tension -> insight -> takeaway -> a genuine question or soft CTA. "
             "No emoji in the first line. Short paragraphs. 900-1,400 characters. No links. Max 2 hashtags, only if natural. "
             "Max one emoji. Avoid em-dash overuse.",
             "Output contract: reply with the post text only. No preamble, no notes, no markdown fences.",
             "\n=== VOICE FILE ===\n" + voice_text()]
    if pos:
        parts.append("\n=== HOW THE FOUNDER ACTUALLY WRITES (real posts, match rhythm and wording) ===\n" +
                     "\n\n---\n\n".join(pos))
    if neg:
        parts.append("\n=== DO NOT SOUND LIKE THIS (drafts the founder rewrote heavily) ===\n" + "\n\n---\n\n".join(neg))
    return "\n".join(parts)


def clean_post(t):
    t = (t or "").strip()
    t = re.sub(r"^```\w*\n|\n```$", "", t).strip()
    t = re.sub(r"^(here('s| is)[^\n]*:|post:)\s*\n+", "", t, flags=re.I).strip()
    if len(t) > 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    return t


def analyze(text):
    """Code-only filters. Returns (flags, hard) — hard flags force a revision."""
    flags, hard = [], []
    n = len(text)
    if n < 300:
        hard.append("too short")
    elif n < 600:
        flags.append("short")
    if n > 3000:
        hard.append("too long")
    elif n > 1800:
        flags.append("long")
    first = text.split("\n")[0]
    if len(first) > 230:
        flags.append("hook too long")
    if EMOJI_RE.search(first):
        hard.append("emoji in first line")
    if LINK_RE.search(text):
        hard.append("link in body")
    if len(HASHTAG_RE.findall(text)) > 2:
        hard.append("too many hashtags")
    if len(EMOJI_RE.findall(text)) > 1:
        flags.append("emoji overuse")
    if text.count("—") > 3:
        flags.append("em-dash overuse")
    hits = sorted({m.group(0).lower() for m in AI_ISMS.finditer(text)})
    if hits:
        hard.append("AI-isms: " + ", ".join(hits[:4]))
    low = text.lower()
    fw = [w for w in forbidden_words() if re.search(r"\b" + re.escape(w) + r"\b", low)]
    if fw:
        hard.append("forbidden: " + ", ".join(fw[:4]))
    return flags + hard, hard


# --------------------------------------------------------------------------- rubric
def rubric():
    r = load_yaml("rubric.yaml")
    dims = r.get("dimensions") or {
        "hook": {"weight": 20, "min": 8}, "voice": {"weight": 25, "min": 8}, "clarity": {"weight": 15, "min": 8},
        "insight": {"weight": 15, "min": 7}, "cta": {"weight": 10, "min": 7}, "formatting": {"weight": 10, "min": 8},
        "factual": {"weight": 5, "min": "pass"}}
    return dims, float(r.get("pass_score", 80))


def weigh(scores):
    dims, pass_score = rubric()
    total_w = sum(float(d["weight"]) for d in dims.values())
    acc, misses = 0.0, []
    for k, d in dims.items():
        v = scores.get(k)
        if k == "factual":
            ok = str(v).lower() == "pass"
            acc += float(d["weight"]) * (10 if ok else 0)
            if not ok:
                misses.append("factual")
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        acc += float(d["weight"]) * v
        if v < float(d["min"]):
            misses.append(k)
    w = round(acc / total_w * 10, 1)
    return w, (w >= pass_score and not misses), misses


STRUCT_SYS = ("You are a strict LinkedIn editor. Score the draft 1-10 on: hook (first line stops the scroll), clarity (one idea per "
              "sentence), insight (contrarian or concrete, not generic), cta (clear, non-salesy, invites replies), formatting "
              "(skimmable, short paragraphs). factual = 'fail' if the draft states numbers, customers, quotes or events that are not "
              "in the TOPIC/RESEARCH given, else 'pass'. Output JSON only: "
              '{"hook":n,"clarity":n,"insight":n,"cta":n,"formatting":n,"factual":"pass|fail","instructions":"specific rewrite '
              'instructions for every score below 8, empty if none"}')


def voice_sys():
    ex = load_examples("positive", 12)
    return ("You judge ONLY voice consistency: does this read like the founder wrote it? Compare sentence rhythm, vocabulary, "
            "argument order, punctuation and warmth against the voice file and real examples. Ignore whether the post is good "
            "marketing. Output JSON only: {\"voice\": n (1-10), \"instructions\": \"specific rewrite instructions if < 8, else empty\"}\n\n"
            "=== VOICE FILE ===\n" + voice_text() + "\n\n=== REAL EXAMPLES ===\n" + "\n\n---\n\n".join(ex))


def judge(system, user, models, tag, run_id, keys, max_tokens):
    """Critic call that tries each model in turn until the JSON has every score key. A critic outage must
    never cost us the draft: it returns {} and the draft is shown as 'not scored'."""
    for i, m in enumerate(models):
        try:
            raw = llm(system, user, model=m, tag=f"{tag}" + (f":fb{i}" if i else ""), run_id=run_id,
                      max_tokens=max_tokens, temperature=0.2)
        except CapReached:
            raise
        except Exception:
            continue
        d = parse_json(raw) or {}
        for k in keys:  # tolerate broken JSON (unescaped quotes in 'instructions'): pull "key": n by regex
            if not isinstance(d.get(k), (int, float)):
                m_ = re.search(r'"%s"\s*:\s*"?(\d+(?:\.\d+)?)' % k, raw or "")
                if m_:
                    d[k] = float(m_.group(1))
        if all(isinstance(d.get(k), (int, float)) for k in keys):
            if "factual" in system and "factual" not in d:
                d["factual"] = "fail" if re.search(r'"factual"\s*:\s*"fail', raw or "") else "pass"
            return d
    return {}


def critique(run_id, slot, text, topic, research, rev):
    c = cfg()
    ctx = f"TOPIC:\n{topic}\n\nRESEARCH:\n{research or '(none)'}\n\nDRAFT:\n{text}"
    s = judge(STRUCT_SYS, ctx, [c["structural"], c["hooks"], c["voice"]], f"struct:{slot}:{rev}", run_id,
              ["hook", "clarity", "insight", "cta", "formatting"], 600)
    v = judge(voice_sys(), "DRAFT:\n" + text, [c["voice"], c["structural"]], f"voice:{slot}:{rev}", run_id, ["voice"], 400)
    scores = {k: s.get(k) for k in ("hook", "clarity", "insight", "cta", "formatting", "factual")}
    scores["voice"] = v.get("voice")
    instr = "\n".join(x for x in (s.get("instructions"), v.get("instructions")) if x)
    return scores, instr


# --------------------------------------------------------------------------- KPI engine
def kpi_cfg():
    k = load_yaml("kpi.yaml")
    return k.get("targets") or {}


def post_engagement(m):
    if m is None:
        return None, None
    imp = m["impressions"]
    parts = [m["reactions"], m["comments"], m["reposts"]]
    eng = m["engagements"] if m["engagements"] is not None else (sum(x or 0 for x in parts) if any(x is not None for x in parts) else None)
    return imp, eng


def latest_metrics(con):
    rows = con.execute("""SELECT m.* FROM metrics m JOIN (SELECT post_id, MAX(captured_at) mx FROM metrics GROUP BY post_id) t
                          ON m.post_id=t.post_id AND m.captured_at=t.mx""").fetchall()
    out = {}
    for r in rows:
        out[r["post_id"]] = r  # last captured row wins
    return out


def post_rows(con):
    ms = latest_metrics(con)
    out = []
    for p in con.execute("SELECT * FROM posts ORDER BY published_at").fetchall():
        m = ms.get(p["id"])
        imp, eng = post_engagement(m)
        er = (eng / imp) if (imp and eng is not None and imp > 0) else None
        leads = con.execute("SELECT COUNT(*) FROM leads WHERE post_id=?", (p["id"],)).fetchone()[0]
        out.append({"id": p["id"], "published_at": p["published_at"], "hook_type": p["hook_type"], "pillar": p["pillar"],
                    "url": p["url"], "text": p["text"] or "", "source": p["source"], "edit_distance": p["edit_distance"],
                    "impressions": imp, "engagements": eng, "er": er, "comments": m["comments"] if m else None,
                    "saves": m["saves"] if m else None, "leads": leads,
                    "rubric": json.loads(p["rubric_scores"]) if p["rubric_scores"] else None})
    return out


def week_bounds(n_back):
    """[start,end) timestamps of the week n_back weeks before the current one (0 = current)."""
    s = week_start() - timedelta(weeks=n_back)
    return ws_ts(s), ws_ts(s + timedelta(weeks=1)), s


def kpi_week(con, posts, n_back):
    t0, t1, s = week_bounds(n_back)
    wp = [p for p in posts if t0 <= p["published_at"] < t1]
    with_m = [p for p in wp if p["er"] is not None]
    ers = [p["er"] for p in with_m]
    comments = [p["comments"] for p in wp if p["comments"] is not None]
    imps = [p["impressions"] for p in wp if p["impressions"] is not None]
    eds = [p["edit_distance"] for p in wp if p["edit_distance"] is not None]
    leads = con.execute("SELECT COUNT(*) FROM leads WHERE created_at>=? AND created_at<?", (t0, t1)).fetchone()[0]
    fol = con.execute("SELECT SUM(new_followers) FROM followers WHERE day>=? AND day<?",
                      (s.isoformat(), (s + timedelta(weeks=1)).isoformat())).fetchone()[0]
    pv = con.execute("SELECT views FROM profile_views WHERE week=?", (s.isoformat(),)).fetchone()
    cost = con.execute("SELECT COALESCE(SUM(cost),0) FROM calls WHERE ts>=? AND ts<?", (t0, t1)).fetchone()[0]
    sam_posts = [p for p in wp if p["source"] == "samatha"]
    # Engagement rate is impression-weighted (sum eng / sum imp) so one tiny post can't swing the week.
    er = (sum(p["engagements"] for p in with_m) / sum(p["impressions"] for p in with_m)) if with_m and sum(p["impressions"] for p in with_m) else None
    return {"week": s.isoformat(), "posts": len(wp), "leads": leads, "followers": fol, "profile_views": pv[0] if pv else None,
            "engagement_rate": er, "comments_per_post": statistics.mean(comments) if comments else None,
            "impressions_median": statistics.median(imps) if imps else None,
            "edit_distance_median": statistics.median(eds) if eds else None,
            "cost": cost, "cost_per_post": (cost / len(sam_posts)) if sam_posts else None, "n_metrics": len(ers)}


KPI_DEFS = [  # key, label, higher_is_better, fmt
    ("leads", "Leads / qualified DMs", True, "{:.0f}"),
    ("followers", "New followers", True, "{:.0f}"),
    ("profile_views", "Profile views", True, "{:.0f}"),
    ("engagement_rate", "Engagement rate", True, "{:.1%}"),
    ("comments_per_post", "Comments / post", True, "{:.1f}"),
    ("impressions_median", "Median impressions", True, "{:.0f}"),
    ("posts", "Posts", True, "{:.0f}"),
    ("edit_distance_median", "Your edit distance", False, "{:.0%}"),
    ("cost_per_post", "Cost / post", False, "${:.2f}"),
]
TARGET_KEYS = {"leads": "leads_per_week", "followers": "followers_per_week", "profile_views": "profile_views_per_week",
               "engagement_rate": "engagement_rate", "comments_per_post": "comments_per_post",
               "impressions_median": "impressions_median", "posts": "posts_per_week",
               "edit_distance_median": "edit_distance_median", "cost_per_post": "cost_per_post_usd"}


def status_dot(val, target, higher):
    if val is None or target in (None, ""):
        return "⚪"
    target = float(target)
    if higher:
        return "🟢" if val >= target else ("🟡" if val >= 0.7 * target else "🔴")
    return "🟢" if val <= target else ("🟡" if val <= 1.3 * target else "🔴")


def trend(vals, higher):
    """vals: newest first. Compares mean of last 2 complete weeks vs the 2 before."""
    a = [v for v in vals[1:3] if v is not None]
    b = [v for v in vals[3:5] if v is not None]
    if not a or not b:
        return "·"
    ma, mb = statistics.mean(a), statistics.mean(b)
    if mb == 0:
        return "↑" if ma > 0 else "→"
    ch = (ma - mb) / abs(mb)
    if abs(ch) < 0.1:
        return "→"
    return "↑" if ch > 0 else "↓"


def kpi_table(con):
    posts = post_rows(con)
    weeks = [kpi_week(con, posts, i) for i in range(5)]  # 0 = current (partial), 1..4 complete
    return posts, weeks


def fmt_val(fmt, v):
    return "–" if v is None else fmt.format(v)


def kpi_text(con, weeks=None):
    if weeks is None:
        _, weeks = kpi_table(con)
    t = kpi_cfg()
    cur, last = weeks[0], weeks[1]
    lines = [f"📊 KPI scorecard — last full week ({last['week']}) vs target · [this week so far]", ""]
    for key, label, higher, fmt in KPI_DEFS:
        tgt = t.get(TARGET_KEYS[key])
        dot = status_dot(last[key], tgt, higher)
        tr = trend([w[key] for w in weeks], higher)
        tgt_s = "" if tgt in (None, "") else f" / target {fmt_val(fmt, float(tgt))}"
        lines.append(f"{dot} {label}: {fmt_val(fmt, last[key])}{tgt_s} {tr} [{fmt_val(fmt, cur[key])}]")
    lines += ["", "🟢 on target · 🟡 within 30% · 🔴 off · ⚪ no data/target · arrow = last 2 weeks vs the 2 before"]
    if all(w["n_metrics"] == 0 for w in weeks):
        lines.append("No post metrics yet — send me your LinkedIn analytics export (Analytics → Posts → Export).")
    return "\n".join(lines)


def type_weights(con, field):
    """Score hook types / pillars by trailing performance: impression-weighted ER over the last 4 weeks
    (falls back to 12 weeks if fewer than 4 measured posts), relative to the overall ER, + 0.5 per attributed lead."""
    posts = [p for p in post_rows(con) if p[field] and p["er"] is not None]
    cutoff = now() - 28 * 86400
    recent = [p for p in posts if p["published_at"] >= cutoff]
    if len(recent) < 4:
        recent = [p for p in posts if p["published_at"] >= now() - 84 * 86400]
    if not recent:
        return {}, 0
    tot_i = sum(p["impressions"] for p in recent) or 1
    base = sum(p["engagements"] for p in recent) / tot_i or 1e-9
    groups = {}
    for p in recent:
        groups.setdefault(p[field], []).append(p)
    out = {}
    for k, ps in groups.items():
        i = sum(p["impressions"] for p in ps) or 1
        er = sum(p["engagements"] for p in ps) / i
        out[k] = {"n": len(ps), "er": er, "score": er / base + 0.5 * sum(p["leads"] for p in ps)}
    return out, len(recent)


def choose_hooks(con, hooks):
    """2 exploit + 1 explore. Exploit = top 2 hook types by shrunk score (n*score + 2*1.0)/(n+2), so one lucky post
    can't dominate and untested types sit at the neutral 1.0; ties follow COLD_START_ORDER (= the cold-start pick).
    Explore = random type among the rest with the fewest measured posts."""
    w, _ = type_weights(con, "hook_type")
    by_type = {}
    for h in hooks:
        by_type.setdefault(h["type"], h)
    avail = list(by_type)

    def shrunk(t):
        n = w.get(t, {}).get("n", 0)
        return (n * w[t]["score"] + 2.0) / (n + 2) if n else 1.0
    order = {t: i for i, t in enumerate(COLD_START_ORDER)}
    exploit = sorted(avail, key=lambda t: (-shrunk(t), order.get(t, 99)))[:2]
    rest = [t for t in avail if t not in exploit]
    if rest:
        least = min(w.get(t, {}).get("n", 0) for t in rest)
        explore = random.choice([t for t in rest if w.get(t, {}).get("n", 0) == least])
    else:
        explore = None
    picks = [(by_type[t], False) for t in exploit]
    if explore:
        picks.append((by_type[explore], True))
    return picks, w


# --------------------------------------------------------------------------- pipeline
HOOK_SYS = ("You write LinkedIn opening lines. Given a topic (and optional research), write exactly 8 hooks, one per angle: "
            "contrarian, data, story, question, stat_shock, myth_bust, listicle, prediction. Each hook max 15 words, plain words, "
            "no emoji, no hashtags, never invent numbers that are not in the research. Also pick the best-fitting content pillar "
            "from the list given. Output JSON only: {\"pillar\": \"...\", \"hooks\": [{\"type\": \"contrarian\", \"text\": \"...\"}, ...]}")


def gen_hooks(run_id, topic, research):
    c = cfg()
    user = f"PILLARS: {' | '.join(pillars()) or 'n/a'}\nTOPIC: {topic}\nRESEARCH: {research or '(none)'}"
    for attempt, model in enumerate([c["hooks"], c["hooks"], c["hooks_fallback"]]):
        d = parse_json(llm(HOOK_SYS, user + ("\nREMINDER: max 15 words per hook." if attempt else ""), model=model,
                           tag=f"hooks:{attempt}", run_id=run_id, max_tokens=1500, temperature=0.9)) or {}
        good = []
        for h in d.get("hooks") or []:
            t = str(h.get("type", "")).lower().replace("-", "_").replace(" ", "_")
            t = {"data_led": "data", "personal_story": "story", "myth": "myth_bust", "stat": "stat_shock"}.get(t, t)
            txt = str(h.get("text", "")).strip().strip('"')
            if t in HOOK_TYPES and txt and len(txt.split()) <= 15 and not any(x["type"] == t for x in good):
                good.append({"type": t, "text": txt})
        if len(good) >= 4:
            return d.get("pillar") or "", good
    raise RuntimeError("hook generation returned too few valid hooks (length/format validation)")


def draft_one(run_id, slot, hook, topic, research, pillar):
    c = cfg()
    user = (f"TOPIC:\n{topic}\n\nRESEARCH (only facts you may use):\n{research or '(none)'}\n\nPILLAR: {pillar}\n"
            f"HOOK TYPE: {hook['type']}\nHOOK: {hook['text']}\n\nWrite the post.")
    try:
        return clean_post(llm(writer_system(), user, model=c["draft"], tag=f"draft:{slot}", run_id=run_id,
                              max_tokens=1200, temperature=0.8))
    except CapReached:
        raise
    except Exception:  # one fallback writer so a provider hiccup doesn't cost the whole option
        return clean_post(llm(writer_system(), user, model=c["cheap"], tag=f"draft:{slot}:fb", run_id=run_id,
                              max_tokens=1200, temperature=0.8))


def revise_text(run_id, slot, text, instructions, topic, research, rev):
    c = cfg()
    user = (f"TOPIC:\n{topic}\n\nRESEARCH:\n{research or '(none)'}\n\nCURRENT DRAFT:\n{text}\n\nREWRITE INSTRUCTIONS:\n{instructions}\n\n"
            "Rewrite the post following the instructions. Keep what already works, keep the founder's voice. Output the post only.")
    return clean_post(llm(writer_system(), user, model=c["draft"], tag=f"revise:{slot}:{rev}", run_id=run_id,
                          max_tokens=1200, temperature=0.6))


def process_slot(run_id, slot, hook, explore, topic, research, pillar):
    c = cfg()
    text = draft_one(run_id, slot, hook, topic, research, pillar)
    best = None
    for rev in range(c["max_revisions"] + 1):
        flags, hard = analyze(text)
        scores, instr = critique(run_id, slot, text, topic, research, rev)
        w, ok, misses = weigh(scores)
        ok = ok and not hard
        cand = {"text": text, "flags": flags, "scores": scores, "weighted": w, "passed": ok, "revisions": rev}
        if best is None or (cand["passed"], cand["weighted"]) > (best["passed"], best["weighted"]):
            best = cand
        unscored = all(v is None for k, v in scores.items() if k != "factual")
        if ok or rev == c["max_revisions"] or (unscored and not hard):  # critics down: don't revise blind
            break
        fix = "\n".join(filter(None, [instr, ("Fix these hard problems: " + "; ".join(hard)) if hard else "",
                                      ("Below threshold: " + ", ".join(misses)) if misses else ""]))
        try:
            text = revise_text(run_id, slot, text, fix, topic, research, rev + 1)
        except CapReached:
            break
    with db() as con:
        con.execute("""INSERT INTO drafts(run_id,slot,hook_type,hook,explore,pillar,text,flags,scores,weighted,passed,revisions,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, slot, hook["type"], hook["text"], 1 if explore else 0, pillar, best["text"], json.dumps(best["flags"]),
                     json.dumps(best["scores"]), best["weighted"], 1 if best["passed"] else 0, best["revisions"], now()))


def set_run(run_id, **kw):
    with db() as con:
        sets = ", ".join(f"{k}=?" for k in kw)
        con.execute(f"UPDATE runs SET {sets} WHERE id=?", (*kw.values(), run_id))


def execute_run(run_id):
    with db() as con:
        r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    topic, research = r["topic"], r["research"]
    try:
        set_run(run_id, status="running", step="hooks")
        pillar, hooks = gen_hooks(run_id, topic, research)
        with db() as con:
            picks, _ = choose_hooks(con, hooks)
        set_run(run_id, pillar=pillar, hooks=json.dumps(hooks), step="drafting + critics")
        with ThreadPoolExecutor(3) as ex:
            futs = [ex.submit(process_slot, run_id, i + 1, h, e, topic, research, pillar) for i, (h, e) in enumerate(picks)]
            errs = []
            for f in futs:
                try:
                    f.result()
                except Exception as e:  # keep the other slots
                    errs.append(e)
        with db() as con:
            n = con.execute("SELECT COUNT(*) FROM drafts WHERE run_id=?", (run_id,)).fetchone()[0]
        if n == 0:
            raise errs[0] if errs else RuntimeError("no drafts produced")
        set_run(run_id, status="done", step="", finished_at=now(),
                error=("; ".join(str(e) for e in errs)[:500] or None))
    except CapReached as e:
        set_run(run_id, status="failed", error=f"BUDGET: {e}", finished_at=now())
    except Exception as e:
        traceback.print_exc()
        set_run(run_id, status="failed", error=str(e)[:500], finished_at=now())
    notify(run_text(run_id))


def run_text(run_id):
    with db() as con:
        r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return f"No run #{run_id}."
        ds = con.execute("SELECT * FROM drafts WHERE run_id=? ORDER BY slot", (run_id,)).fetchall()
    if r["status"] in ("queued", "running"):
        return f"⏳ Run #{run_id} is still working ({r['step'] or r['status']}). Usually 2–4 min; I'll message you when it's done."
    if r["status"] == "failed" and not ds:
        budget = (r["error"] or "").startswith("BUDGET")
        return (f"⚠️ Run #{run_id} failed: {r['error']}\n" + ("Budget guard stopped it. " if budget else "") + FALLBACK_HINT)
    out = [f"✍️ Run #{run_id} — \"{r['topic'][:80]}\" · pillar: {r['pillar'] or '–'} · cost ${r['cost']:.3f}", ""]
    with db() as con:
        hw, _ = type_weights(con, "hook_type")
    for d in ds:
        s = json.loads(d["scores"] or "{}")
        n = hw.get(d["hook_type"], {}).get("n", 0)
        tag = "🧪 explore" if d["explore"] else (f"📈 proven, {hw[d['hook_type']]['score']:.1f}× avg ER" if n >= 2 else "⭐ core")
        verdict = "✅ passed" if d["passed"] else "⚠️ below bar"
        sc = " ".join(f"{k} {'–' if s.get(k) is None else s.get(k)}" for k in ("hook", "voice", "clarity", "insight", "cta", "formatting"))
        flags = json.loads(d["flags"] or "[]")
        out.append(f"━━ Option {d['slot']} · {d['hook_type']} ({tag}) · {d['weighted']}/100 {verdict}"
                   f"{' · ' + str(d['revisions']) + ' rev' if d['revisions'] else ''}")
        out.append(f"{sc} · factual {s.get('factual')}" + (f"\nflags: {', '.join(flags)}" if flags else ""))
        out.append("")
        out.append(d["text"])
        out.append("")
    if r["error"]:
        out.append(f"(partial: {r['error'][:200]})")
    out.append("Reply 1/2/3 to pick · 'edit 2: <notes>' to revise · after posting send 'posted <url>' (+ your final text if you changed it).")
    return "\n".join(out)


def notify(text):
    """Send directly via the Samatha bot (results arrive even if the agent turn ended). Telegram limit 4096 chars."""
    tok = env_get("TELEGRAM_BOT_TOKEN")
    chat = env_get("TELEGRAM_HOME_CHANNEL") or (env_get("TELEGRAM_ALLOWED_USERS") or "").split(",")[0].strip()
    if not tok or not chat or os.environ.get("SAMATHA_NO_NOTIFY") == "1":
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
    return ok


def env_get(key):
    if os.environ.get(key):
        return os.environ[key]
    try:
        for line in PROFILE_ENV.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------- commands
def cmd_idea(a):
    research = a.research
    if research and Path(research).is_file():
        research = Path(research).read_text()[:6000]
    with db() as con:
        busy = con.execute("SELECT id FROM runs WHERE status IN ('queued','running') AND created_at>?", (now() - 900,)).fetchone()
        if busy:
            print(f"Run #{busy[0]} is still working — one at a time. Check with: samatha show {busy[0]}")
            return
        c = cfg()
        if spent_today(con) >= c["daily_cap_usd"]:
            print(f"⚠️ Samatha's daily budget (${c['daily_cap_usd']:.2f}) is used up. {FALLBACK_HINT}")
            return
        cur = con.execute("INSERT INTO runs(topic,research,status,created_at) VALUES(?,?,?,?)",
                          (a.topic.strip(), research, "queued", now()))
        run_id = cur.lastrowid
    if a.wait:  # foreground (tests/CLI); execute_run() prints the result when notifications are off
        os.environ["SAMATHA_NO_NOTIFY"] = os.environ.get("SAMATHA_NO_NOTIFY", "1")
        execute_run(run_id)
        return
    # Detach: the Hermes terminal tool times out at 180 s; the worker messages the result to Telegram itself.
    # Prefer a transient systemd unit so a gateway restart (KillMode=mixed) can't kill a run mid-way.
    import shutil
    import subprocess
    cmd = [sys.executable, os.path.abspath(__file__), "_worker", str(run_id)]
    logf = open(HOME / "worker.log", "a")
    if shutil.which("systemd-run") and os.geteuid() == 0:
        envs = [f"--setenv={k}={os.environ[k]}" for k in ("SAMATHA_HOME", "SAMATHA_SKILL", "SAMATHA_ENV", "SAMATHA_FAKE_LLM", "SAMATHA_NO_NOTIFY") if k in os.environ]
        subprocess.run(["systemd-run", "--quiet", "--collect", f"--unit=samatha-run-{run_id}-{now()}",
                        "-p", "MemoryMax=300M", "-p", "RuntimeMaxSec=900", "-p", f"StandardOutput=append:{HOME}/worker.log",
                        "-p", f"StandardError=append:{HOME}/worker.log", *envs, *cmd], check=True)
    else:
        subprocess.Popen(cmd, stdout=logf, stderr=logf, start_new_session=True, close_fds=True)
    print(f"Started run #{run_id}. Hooks → 3 drafts → critics → revisions takes ~2–4 min; "
          f"the 3 options will arrive as a separate Telegram message. (Check: samatha show {run_id})")


def latest_run(con, run=None):
    if run:
        return run
    r = con.execute("SELECT id FROM runs WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else None


def get_draft(con, n, run=None):
    rid = latest_run(con, run)
    if not rid:
        return None
    return con.execute("SELECT * FROM drafts WHERE run_id=? AND slot=?", (rid, n)).fetchone()


def cmd_show(a):
    with db() as con:
        rid = a.run or (con.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone() or [None])[0]
    print(run_text(rid) if rid else "No runs yet. Send me a topic.")


def cmd_pick(a):
    with db() as con:
        d = get_draft(con, a.n, a.run)
        if not d:
            print("No such option. (samatha show)")
            return
        con.execute("UPDATE drafts SET selected=0 WHERE run_id=?", (d["run_id"],))
        con.execute("UPDATE drafts SET selected=1 WHERE id=?", (d["id"],))
    print(f"Option {a.n} selected (draft #{d['id']}). Copy-paste below, edit freely, post it, then send 'posted <url>' "
          f"(and paste your final text if you changed it).\n\n{d['text']}")


def cmd_revise(a):
    with db() as con:
        d = get_draft(con, a.n, a.run)
        if not d:
            print("No such option.")
            return
        r = con.execute("SELECT * FROM runs WHERE id=?", (d["run_id"],)).fetchone()
    try:
        text = revise_text(d["run_id"], d["slot"], d["text"], a.instructions, r["topic"], r["research"], d["revisions"] + 1)
        flags, hard = analyze(text)
        scores, _ = critique(d["run_id"], d["slot"], text, r["topic"], r["research"], d["revisions"] + 1)
    except CapReached as e:
        print(f"⚠️ {e}. {FALLBACK_HINT}")
        return
    w, ok, _ = weigh(scores)
    ok = ok and not hard
    with db() as con:
        con.execute("UPDATE drafts SET text=?, flags=?, scores=?, weighted=?, passed=?, revisions=revisions+1 WHERE id=?",
                    (text, json.dumps(flags), json.dumps(scores), w, 1 if ok else 0, d["id"]))
    print(f"Option {a.n} revised · {w}/100 {'✅' if ok else '⚠️'}" + (f" · flags: {', '.join(flags)}" if flags else "") + f"\n\n{text}")


def activity_id(url):
    if not url:
        return None
    m = ACTIVITY_RE.search(url)
    return m.group(1) if m else None


def edit_distance(a, b):
    return round(1 - difflib.SequenceMatcher(None, a or "", b or "").ratio(), 3)


def save_example(kind, text, name):
    d = SKILL / "examples" / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text.strip() + "\n")


def cmd_posted(a):
    final = a.text
    if a.text_file:
        final = Path(a.text_file).read_text()
    with db() as con:
        d = None
        if a.n:
            d = get_draft(con, a.n, a.run)
        elif not (a.hook_type or a.pillar or a.manual):  # a hand-written post carries its own tags
            d = con.execute("""SELECT * FROM drafts WHERE selected=1 AND created_at>? AND id NOT IN
                               (SELECT draft_id FROM posts WHERE draft_id IS NOT NULL) ORDER BY id DESC LIMIT 1""",
                            (now() - 14 * 86400,)).fetchone()
        aid = activity_id(a.url)
        if aid and con.execute("SELECT 1 FROM posts WHERE activity_id=?", (aid,)).fetchone():
            print("Already logged this post.")
            return
        base = d["text"] if d else None
        final = (final or base or "").strip()
        ed = edit_distance(base, final) if base else None
        ts = now()
        if a.date:
            ts = int(datetime.fromisoformat(a.date).replace(tzinfo=timezone.utc).timestamp())
        con.execute("""INSERT INTO posts(draft_id,activity_id,url,text,published_at,source,hook_type,pillar,edit_distance,rubric_scores,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (d["id"] if d else None, aid, a.url, final, ts, "samatha" if d else "manual",
                     d["hook_type"] if d else a.hook_type, d["pillar"] if d else a.pillar, ed, d["scores"] if d else None, now()))
        pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    msg = f"Logged post #{pid}."
    if ed is not None:
        msg += f" Your edits: {ed:.0%}."
        if ed < 0.10:
            save_example("positive", final, f"post-{pid}")
            msg += " Saved as a positive voice example (<10% edits)."
        elif ed > 0.30:
            save_example("negative", base, f"draft-{d['id']}")
            save_example("positive", final, f"post-{pid}")
            msg += " My draft is now a negative example (>30% edits); your final version is a positive one."
        else:
            save_example("positive", final, f"post-{pid}")
    print(msg + " Send the analytics export on Monday so I can score it.")


def find_post(con, ref):
    if ref is None:
        return None
    ref = str(ref)
    aid = activity_id(ref)
    if aid:
        r = con.execute("SELECT id FROM posts WHERE activity_id=?", (aid,)).fetchone()
        return r[0] if r else None
    if ref.isdigit():
        r = con.execute("SELECT id FROM posts WHERE id=?", (int(ref),)).fetchone()
        return r[0] if r else None
    return None


def cmd_lead(a):
    with db() as con:
        pid = find_post(con, a.post) if a.post else None
        con.execute("INSERT INTO leads(name,post_id,note,created_at) VALUES(?,?,?,?)", (a.name, pid, a.note, now()))
        n = con.execute("SELECT COUNT(*) FROM leads WHERE created_at>=?", (ws_ts(week_start()),)).fetchone()[0]
    tgt = kpi_cfg().get("leads_per_week")
    print(f"Lead logged: {a.name}" + (f" (from post #{pid})" if pid else "") + f". This week: {n}" + (f"/{tgt}" if tgt else "") + ".")


def cmd_metrics(a):
    with db() as con:
        pid = find_post(con, a.post)
        if not pid:
            print("Post not found — use the post URL, activity id, or Samatha post #.")
            return
        con.execute("""INSERT INTO metrics(post_id,captured_at,impressions,reactions,comments,reposts,engagements,saves,sends,
                       profile_views,followers_gained,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, now(), a.impressions, a.reactions, a.comments, a.reposts, a.engagements, a.saves, a.sends,
                     a.profile_views, a.followers_gained, "manual"))
    print(f"Metrics saved for post #{pid}.")


# ---- LinkedIn export parsing (stdlib only: xlsx is a zip of XML)
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def xlsx_sheets(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rmap = {r.get("Id"): r.get("Target") for r in rels}
    out = {}
    for s in wb.find("m:sheets", NS):
        target = rmap[s.get("{%s}id" % NS["r"])].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        rows = []
        for row in ET.fromstring(z.read(target)).iter("{%s}row" % NS["m"]):
            cells = {}
            for c in row.findall("m:c", NS):
                ref = re.match(r"([A-Z]+)", c.get("r", "A")).group(1)
                col = 0
                for ch in ref:
                    col = col * 26 + ord(ch) - 64
                t = c.get("t")
                v = c.find("m:v", NS)
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
                else:
                    val = v.text if v is not None else None
                cells[col - 1] = val
            if cells:
                rows.append([cells.get(i) for i in range(max(cells) + 1)])
        out[s.get("name")] = rows
    return out


def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except ValueError:
        return None


def parse_date(v):
    if v is None:
        return None
    s = str(v).strip()
    f = num(s)
    if isinstance(f, (int, float)) and 20000 < f < 80000:  # Excel serial date
        return (date(1899, 12, 30) + timedelta(days=int(f)))
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    return None


def ensure_post_for(con, url, pub):
    aid = activity_id(url)
    if not aid:
        return None
    r = con.execute("SELECT id FROM posts WHERE activity_id=?", (aid,)).fetchone()
    if r:
        return r[0]
    ts = ws_ts(pub) + 9 * 3600 if pub else now()
    con.execute("INSERT INTO posts(activity_id,url,published_at,source,created_at) VALUES(?,?,?,?,?)",
                (aid, url, ts, "analytics", now()))
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]


def import_tabular(sheets):
    """Handles LinkedIn's bulk content export (DISCOVERY/ENGAGEMENT/TOP POSTS/FOLLOWERS sheets) and the single-post
    export (key/value rows). Column headers are matched loosely because LinkedIn renames them now and then."""
    per_post, followers, single = {}, {}, {}
    for name, rows in sheets.items():
        # key/value style (single post export): "Impressions | 1234"
        for r in rows:
            if len(r) >= 2 and r[0] and isinstance(r[0], str):
                k = r[0].strip().lower()
                if k in ("post url", "impressions", "members reached", "reactions", "comments", "reposts", "saves",
                         "sends on linkedin", "sends", "profile viewers from this post", "followers gained from this post",
                         "post publish date", "post date"):
                    single[k] = r[1]
        # header-row tables
        for hi, r in enumerate(rows):
            low = [str(x).strip().lower() if x is not None else "" for x in r]
            if "post url" in low:
                idx = [i for i, x in enumerate(low) if x == "post url"]
                for start in idx:  # TOP POSTS sheet has two tables side by side
                    cols = {}
                    for j in range(start, min(start + 4, len(low))):
                        cols[low[j]] = j
                    for row in rows[hi + 1:]:
                        if start >= len(row) or not row[start]:
                            continue
                        url = str(row[start]).strip()
                        e = per_post.setdefault(url, {})
                        for key, field in (("engagements", "engagements"), ("impressions", "impressions"),
                                           ("post publish date", "date"), ("post date", "date")):
                            if key in cols and cols[key] < len(row):
                                e[field] = row[cols[key]]
            if "new followers" in low and "date" in low:
                di, fi = low.index("date"), low.index("new followers")
                for row in rows[hi + 1:]:
                    d = parse_date(row[di] if di < len(row) else None)
                    n = num(row[fi] if fi < len(row) else None)
                    if d and n is not None:
                        followers[d.isoformat()] = n
    return per_post, followers, single


def cmd_import_analytics(a):
    p = Path(a.path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        sheets = xlsx_sheets(p)
    else:
        sheets = {"csv": [r for r in csv.reader(io.StringIO(p.read_text(encoding="utf-8-sig", errors="replace")))]}
    per_post, followers, single = import_tabular(sheets)
    n_posts = 0
    with db() as con:
        for url, e in per_post.items():
            pid = ensure_post_for(con, url, parse_date(e.get("date")))
            if not pid:
                continue
            prev = con.execute("SELECT * FROM metrics WHERE post_id=? ORDER BY captured_at DESC LIMIT 1", (pid,)).fetchone()
            imp = num(e.get("impressions")) if e.get("impressions") is not None else (prev["impressions"] if prev else None)
            eng = num(e.get("engagements")) if e.get("engagements") is not None else (prev["engagements"] if prev else None)
            con.execute("""INSERT INTO metrics(post_id,captured_at,impressions,engagements,reactions,comments,reposts,saves,sends,
                           profile_views,followers_gained,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (pid, now(), imp, eng, *(prev[k] if prev else None for k in
                         ("reactions", "comments", "reposts", "saves", "sends", "profile_views", "followers_gained")), "bulk"))
            n_posts += 1
        if single.get("post url") and single.get("impressions") is not None:
            pid = ensure_post_for(con, str(single["post url"]), parse_date(single.get("post publish date") or single.get("post date")))
            if pid:
                g = lambda k: num(single.get(k))
                con.execute("""INSERT INTO metrics(post_id,captured_at,impressions,reactions,comments,reposts,saves,sends,profile_views,
                               followers_gained,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (pid, now(), g("impressions"), g("reactions"), g("comments"), g("reposts"), g("saves"),
                             g("sends on linkedin") if single.get("sends on linkedin") is not None else g("sends"),
                             g("profile viewers from this post"), g("followers gained from this post"), "single"))
                n_posts += 1
        for d, n in followers.items():
            con.execute("INSERT OR REPLACE INTO followers(day,new_followers) VALUES(?,?)", (d, n))
    print(f"Imported metrics for {n_posts} post(s), {len(followers)} day(s) of follower data from {p.name}.")
    if n_posts == 0 and not followers:
        print("Nothing recognised. Expected LinkedIn → Analytics → Posts/Content → Export (.xlsx) or a single post's export.")


def cmd_import_posts(a):
    p = Path(a.path)
    text = None
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if n.lower().endswith("shares.csv")]
            if not names:
                print("No Shares.csv in that zip — request 'Posts' (or the full archive) in LinkedIn → Settings → Data privacy → Get a copy of your data.")
                return
            text = z.read(names[0]).decode("utf-8-sig", errors="replace")
    else:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    added = enriched = examples = 0
    with db() as con:
        for r in rows:
            body = (r.get("ShareCommentary") or "").replace('""', '"').strip().strip('"')
            body = re.sub(r"\s*\n\s*\n\s*", "\n\n", body)
            url = r.get("ShareLink") or ""
            aid = activity_id(url)
            d = parse_date((r.get("Date") or "")[:10])
            if not body or not aid:
                continue
            ts = int(datetime.fromisoformat(r["Date"]).replace(tzinfo=timezone.utc).timestamp()) if r.get("Date") else (ws_ts(d) if d else now())
            ex = con.execute("SELECT id, text FROM posts WHERE activity_id=?", (aid,)).fetchone()
            if ex:
                if ex["text"]:
                    continue
                con.execute("UPDATE posts SET text=?, published_at=? WHERE id=?", (body, ts, ex["id"]))  # known from analytics
                enriched += 1
            else:
                con.execute("INSERT INTO posts(activity_id,url,text,published_at,source,created_at) VALUES(?,?,?,?,?,?)",
                            (aid, url, body, ts, "import", now()))
                added += 1
            if len(body) >= 300:
                save_example("positive", body, f"li-{aid}")
                examples += 1
    print(f"Imported {added} new post(s), filled text for {enriched} known post(s) from {p.name}; "
          f"{examples} added as voice examples (≥300 chars).")
    if added or enriched:
        print("Tip: run 'samatha tag-imports' so I can label hook type + pillar on old posts for KPI attribution (cheap model).")


def cmd_tag_imports(a):
    c = cfg()
    with db() as con:
        rows = con.execute("SELECT id, text FROM posts WHERE (hook_type IS NULL OR pillar IS NULL) AND text IS NOT NULL LIMIT ?",
                           (a.limit,)).fetchall()
    sys_p = (f"Classify a LinkedIn post. hook_type is one of {HOOK_TYPES}; pillar is one of {pillars()}. "
             "Output JSON only: {\"hook_type\": \"...\", \"pillar\": \"...\"}")
    n = 0
    for r in rows:
        try:
            d = parse_json(llm(sys_p, r["text"][:2500], model=c["cheap"], tag="tag", max_tokens=200, temperature=0)) or {}
        except CapReached as e:
            print(f"Stopped: {e}")
            break
        ht = d.get("hook_type") if d.get("hook_type") in HOOK_TYPES else None
        with db() as con:
            con.execute("UPDATE posts SET hook_type=COALESCE(hook_type,?), pillar=COALESCE(pillar,?) WHERE id=?",
                        (ht, d.get("pillar"), r["id"]))
        n += 1
    print(f"Tagged {n} post(s).")


# ---- KPI reports
def cmd_kpi(a):
    with db() as con:
        posts, weeks = kpi_table(con)
        if a.json:
            print(json.dumps({"weeks": weeks}, indent=1))
            return
        print(kpi_text(con, weeks))
        hw, n = type_weights(con, "hook_type")
        if hw:
            print("\nHook types (trailing ER vs your average, n posts): " +
                  ", ".join(f"{k} {v['score']:.2f}×({v['n']})" for k, v in sorted(hw.items(), key=lambda x: -x[1]["score"])))
        pw, _ = type_weights(con, "pillar")
        if pw:
            print("Pillars: " + ", ".join(f"{k} {v['score']:.2f}×({v['n']})" for k, v in sorted(pw.items(), key=lambda x: -x[1]["score"])))


def actions_for(con, weeks):
    t = kpi_cfg()
    acts = []
    last = weeks[1]
    if t.get("posts_per_week") and last["posts"] < float(t["posts_per_week"]):
        acts.append(f"Cadence: {last['posts']}/{t['posts_per_week']} last week — let's queue topics now (I'll propose them with 'plan').")
    hw, _ = type_weights(con, "hook_type")
    proven = sorted([(k, v) for k, v in hw.items() if v["n"] >= 2], key=lambda x: -x[1]["score"])
    if proven:
        acts.append(f"Lean on '{proven[0][0]}' hooks ({proven[0][1]['score']:.2f}× your average ER)."
                    + (f" Drop/rework '{proven[-1][0]}' ({proven[-1][1]['score']:.2f}×)." if len(proven) > 1 and proven[-1][1]["score"] < 0.8 else ""))
    pw, _ = type_weights(con, "pillar")
    pw = {k: v for k, v in pw.items() if v["n"] >= 2}
    if pw:
        best = max(pw.items(), key=lambda x: x[1]["score"])
        acts.append(f"Best pillar right now: {best[0]} ({best[1]['score']:.2f}×) — 2 of this week's posts should come from it.")
    if t.get("leads_per_week") and last["leads"] < float(t["leads_per_week"]):
        acts.append("Leads below target: end the next 2 posts with a concrete, low-friction ask (e.g. 'DM me \"list\" and I'll send it').")
    if last["edit_distance_median"] is not None and last["edit_distance_median"] > float(t.get("edit_distance_median", 0.2)):
        acts.append("You're rewriting a lot of my drafts — send me 5 more of your own posts or comments for the voice file.")
    if not acts:
        acts.append("Keep the rhythm; I'll keep 1 of 3 options as an experiment each time.")
    return acts[:3]


def cmd_week(a):
    with db() as con:
        posts, weeks = kpi_table(con)
        t0, t1, s = week_bounds(1)
        recent = [p for p in posts if p["er"] is not None and p["published_at"] >= now() - 14 * 86400]
        recent.sort(key=lambda p: -p["er"])
        lines = [f"🗓 Weekly report — week of {s.isoformat()}", "", kpi_text(con, weeks), ""]
        if recent:
            def desc(p):
                first = (p["text"].split("\n")[0][:90] if p["text"] else (p["url"] or ""))
                return f"{p['er']:.1%} ER · {p['impressions']} impr · {p['hook_type'] or '?'}/{p['pillar'] or '?'} — {first}"
            lines.append("🏆 Top: " + desc(recent[0]))
            if len(recent) > 1:
                lines.append("🪫 Bottom: " + desc(recent[-1]))
            if not a.no_llm and len(recent) > 1:
                try:
                    why = llm("You are a LinkedIn analyst. In max 3 short bullets, explain why the top post outperformed the bottom "
                              "one (hook, specificity, format, topic). No fluff.",
                              f"TOP ({recent[0]['er']:.1%}):\n{recent[0]['text'][:1500]}\n\nBOTTOM ({recent[-1]['er']:.1%}):\n{recent[-1]['text'][:1500]}",
                              model=cfg()["cheap"], tag="why", max_tokens=300, temperature=0.3)
                    lines += ["", "Why:", why.strip()]
                except Exception as e:
                    lines.append(f"(why-analysis skipped: {e})")
        lines += ["", "🎯 This week's 3 actions:"] + [f"{i + 1}. {x}" for i, x in enumerate(actions_for(con, weeks))]
        rp = rank_corr(con)
        if rp:
            lines.append("\n" + rp)
        con.execute("INSERT OR REPLACE INTO kpi_snapshots(week,data,created_at) VALUES(?,?,?)",
                    (weeks[1]["week"], json.dumps(weeks[1]), now()))
    out = "\n".join(lines)
    if a.notify:
        notify(out)
    else:
        print(out)


def spearman(x, y):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(x), ranks(y)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num_ = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num_ / den if den else 0.0


def scored_posts(con):
    return [p for p in post_rows(con) if p["rubric"] and p["er"] is not None]


def rank_corr(con):
    ps = scored_posts(con)
    if len(ps) < 10:
        return f"Rubric-vs-reality check unlocks at 10 measured Samatha posts ({len(ps)} so far)."
    w = [weigh(p["rubric"])[0] for p in ps]
    rho = spearman(w, [p["er"] for p in ps])
    return f"Rubric score vs real engagement (Spearman, n={len(ps)}): {rho:+.2f} " + \
        ("— the rubric predicts well." if rho >= 0.4 else "— weak; run 'calibrate' to re-weight." if rho < 0.2 else "— moderate.")


def cmd_calibrate(a):
    dims, pass_score = rubric()
    with db() as con:
        ps = scored_posts(con)
    if len(ps) < 10:
        print(f"Need ≥10 measured Samatha posts to calibrate (have {len(ps)}).")
        return
    ers = [p["er"] for p in ps]
    rhos = {}
    for k in dims:
        if k == "factual":
            continue
        vals = [float(p["rubric"].get(k) or 0) for p in ps]
        rhos[k] = spearman(vals, ers)
    raw = {k: max(r, 0.05) for k, r in rhos.items()}
    tot = sum(raw.values())
    movable = sum(float(d["weight"]) for k, d in dims.items() if k != "factual")
    proposal = {k: dict(dims[k]) for k in dims}
    for k in raw:
        data_w = raw[k] / tot * movable
        proposal[k]["weight"] = round(0.5 * float(dims[k]["weight"]) + 0.5 * data_w, 1)  # blend: no wild swings
    lines = ["Proposed rubric weights (50% current, 50% data):"]
    for k in dims:
        lines.append(f"- {k}: {dims[k]['weight']} → {proposal[k]['weight']}" + (f"  (ρ={rhos[k]:+.2f})" if k in rhos else ""))
    prop_path = SKILL / "rubric.proposed.yaml"
    prop_path.write_text(yaml.safe_dump({"pass_score": pass_score, "dimensions": proposal}, sort_keys=False))
    if a.apply:
        src = SKILL / "rubric.yaml"
        bak = SKILL / f"rubric.yaml.bak-{date.today().isoformat()}"
        if src.exists():
            bak.write_text(src.read_text())
        src.write_text(prop_path.read_text())
        lines.append(f"Applied. Previous weights backed up to {bak.name}.")
    else:
        lines.append("Not applied. Say 'apply the new rubric' if you agree.")
    print("\n".join(lines))


def cmd_alerts(a):
    t = kpi_cfg()
    out = []
    with db() as con:
        posts, weeks = kpi_table(con)

        def once(key, msg):
            if not con.execute("SELECT 1 FROM alerts WHERE key=?", (key,)).fetchone():
                con.execute("INSERT INTO alerts(key,created_at) VALUES(?,?)", (key, now()))
                out.append(msg)
        wd = datetime.now(timezone.utc).weekday()
        tgt_posts = t.get("posts_per_week")
        if tgt_posts and wd >= 3 and weeks[0]["posts"] < float(tgt_posts) - 1:
            once(f"cadence:{weeks[0]['week']}", f"⏰ Cadence: {weeks[0]['posts']}/{tgt_posts} posts this week and it's "
                 f"{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][wd]}. Want me to draft from your top pillar? Send a topic or say 'plan'.")
        tgt_er = t.get("engagement_rate")
        if tgt_er and all(w["engagement_rate"] is not None and w["engagement_rate"] < float(tgt_er) for w in weeks[1:3]):
            hw, _ = type_weights(con, "hook_type")
            best = max(hw.items(), key=lambda x: x[1]["score"])[0] if hw else "story"
            once(f"er:{weeks[1]['week']}", f"📉 Engagement rate below {float(tgt_er):.1%} two weeks running "
                 f"({weeks[2]['engagement_rate']:.1%}, {weeks[1]['engagement_rate']:.1%}). Proposed fix: next 3 posts use "
                 f"'{best}' hooks and end with a direct question.")
        meas = [p for p in posts if p["engagements"] is not None]
        if len(meas) >= 4:
            med = statistics.median(p["engagements"] for p in meas)
            for p in meas:
                if p["published_at"] >= now() - 14 * 86400 and med and p["engagements"] > 2 * med:
                    once(f"spike:{p['id']}", f"🚀 Post #{p['id']} is at {p['engagements']} engagements (>2× your median {med:.0f}). "
                         f"Worth a follow-up post or replying to every comment today.\n{p['url'] or ''}")
    if not out:
        if not a.quiet:
            print("No alerts.")
        return
    msg = "\n\n".join(out)
    if a.notify:
        notify(msg)
    else:
        print(msg)


def cmd_plan(a):
    c = cfg()
    with db() as con:
        pw, _ = type_weights(con, "pillar")
        recent = [r[0][:120] for r in con.execute("SELECT text FROM posts WHERE text IS NOT NULL ORDER BY published_at DESC LIMIT 8")]
        rtopics = [r[0][:100] for r in con.execute("SELECT topic FROM runs ORDER BY id DESC LIMIT 8")]
    allp = pillars()
    ranked = sorted(allp, key=lambda p: -(pw.get(p, {}).get("score", 1.0)))
    least = min(allp, key=lambda p: pw.get(p, {}).get("n", 0)) if allp else "any"
    user = (f"PILLARS ranked by real performance (best first): {ranked}\nUNDER-TESTED PILLAR: {least}\n"
            f"RECENT POSTS (don't repeat): {recent}\nRECENT TOPICS (don't repeat): {rtopics}\n\n"
            f"VOICE FILE:\n{voice_text()[:3000]}")
    try:
        txt = llm("You plan LinkedIn topics for a founder. Propose 3 concrete post topics: 2 from the best pillar(s), 1 from the "
                  "under-tested pillar (label it 'explore'). Each: one line '[pillar] topic — why now'. Only use facts from the "
                  "voice file; topics should be things the founder can write from their own week.", user,
                  model=c["cheap"], tag="plan", max_tokens=500, temperature=0.8)
    except CapReached as e:
        print(f"⚠️ {e}")
        return
    print("🧭 Topic plan (weighted by what's working):\n" + txt.strip() + "\n\nReply with one (or your own) and I'll draft it.")


def cmd_remind(a):
    notify("📥 Monday KPI check-in: please send me last week's LinkedIn analytics export\n"
           "(LinkedIn → Me → Analytics/Creator analytics → Posts → Export, past 7 or 28 days, .xlsx). "
           "Also tell me any leads/DMs that came from LinkedIn ('lead <name> <post url>').")


def cmd_status(a):
    c = cfg()
    with db() as con:
        _, weeks = kpi_table(con)
        pend = con.execute("SELECT COUNT(*) FROM runs r WHERE status='done' AND r.id > COALESCE((SELECT MAX(d.run_id) FROM drafts d "
                           "JOIN posts p ON p.draft_id=d.id),0) AND r.created_at > ?", (now() - 7 * 86400,)).fetchone()[0]
        running = con.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
        failed = con.execute("SELECT COUNT(*) FROM runs WHERE status='failed' AND created_at>?", (now() - 86400,)).fetchone()[0]
        d = {"cost_today": round(spent_today(con), 4), "daily_cap": c["daily_cap_usd"], "pending_approvals": pend,
             "running": running, "failed_24h": failed, "leads_week": weeks[0]["leads"], "posts_week": weeks[0]["posts"],
             "posts_target": kpi_cfg().get("posts_per_week"), "er_last_week": weeks[1]["engagement_rate"],
             "er_target": kpi_cfg().get("engagement_rate")}
    print(json.dumps(d) if a.json else "\n".join(f"{k}: {v}" for k, v in d.items()))


def cmd_voice(a):
    print(voice_text() or "VOICE.md missing.")
    pos = len(list((SKILL / "examples" / "positive").glob("*.md"))) if (SKILL / "examples" / "positive").is_dir() else 0
    neg = len(list((SKILL / "examples" / "negative").glob("*.md"))) if (SKILL / "examples" / "negative").is_dir() else 0
    print(f"\nExamples: {pos} positive, {neg} negative (the plan's target is 100+ positive).")


def main():
    ap = argparse.ArgumentParser(prog="samatha")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("idea"); p.add_argument("topic"); p.add_argument("--research"); p.add_argument("--wait", action="store_true"); p.set_defaults(f=cmd_idea)
    p = sp.add_parser("show"); p.add_argument("run", nargs="?", type=int); p.set_defaults(f=cmd_show)
    p = sp.add_parser("pick"); p.add_argument("n", type=int); p.add_argument("--run", type=int); p.set_defaults(f=cmd_pick)
    p = sp.add_parser("revise"); p.add_argument("n", type=int); p.add_argument("instructions"); p.add_argument("--run", type=int); p.set_defaults(f=cmd_revise)
    p = sp.add_parser("posted"); p.add_argument("n", nargs="?", type=int); p.add_argument("--run", type=int); p.add_argument("--url", required=True)
    p.add_argument("--text"); p.add_argument("--text-file"); p.add_argument("--date"); p.add_argument("--hook-type"); p.add_argument("--pillar")
    p.add_argument("--manual", action="store_true", help="post was not from a Samatha draft"); p.set_defaults(f=cmd_posted)
    p = sp.add_parser("lead"); p.add_argument("name"); p.add_argument("--post"); p.add_argument("--note"); p.set_defaults(f=cmd_lead)
    p = sp.add_parser("metrics"); p.add_argument("post")
    for k in ("impressions", "reactions", "comments", "reposts", "engagements", "saves", "sends", "profile-views", "followers-gained"):
        p.add_argument("--" + k, type=int)
    p.set_defaults(f=cmd_metrics)
    p = sp.add_parser("import-posts"); p.add_argument("path"); p.set_defaults(f=cmd_import_posts)
    p = sp.add_parser("import-analytics"); p.add_argument("path"); p.set_defaults(f=cmd_import_analytics)
    p = sp.add_parser("tag-imports"); p.add_argument("--limit", type=int, default=60); p.set_defaults(f=cmd_tag_imports)
    p = sp.add_parser("kpi"); p.add_argument("--json", action="store_true"); p.set_defaults(f=cmd_kpi)
    p = sp.add_parser("week"); p.add_argument("--notify", action="store_true"); p.add_argument("--no-llm", action="store_true"); p.set_defaults(f=cmd_week)
    p = sp.add_parser("alerts"); p.add_argument("--notify", action="store_true"); p.add_argument("--quiet", action="store_true"); p.set_defaults(f=cmd_alerts)
    p = sp.add_parser("plan"); p.set_defaults(f=cmd_plan)
    p = sp.add_parser("calibrate"); p.add_argument("--apply", action="store_true"); p.set_defaults(f=cmd_calibrate)
    p = sp.add_parser("remind"); p.set_defaults(f=cmd_remind)
    p = sp.add_parser("status"); p.add_argument("--json", action="store_true"); p.set_defaults(f=cmd_status)
    p = sp.add_parser("voice"); p.set_defaults(f=cmd_voice)
    p = sp.add_parser("_worker"); p.add_argument("run_id", type=int); p.set_defaults(f=lambda a: execute_run(a.run_id))
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()

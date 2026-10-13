"""LinkedIn Drafter backend for the Hermes OpenWebUI fork.

Mounted at /api/v1/linkedin-drafter (see patched_main.py). All routes require a verified user.

Pipeline for one raw idea (<= 22 LLM calls, hard cap MAX_CALLS_PER_RUN; V2 compare mode MAX_CALLS_PER_RUN_V2):
  1. WRITE    6 drafts, one forced angle each                       (6 calls, parallel x3)
  2. FILTER   code checks: length, hook, links, hashtags, AI-isms   (no LLM)
  3. SIMULATE 3 audience personas x 2 shuffled passes score drafts  (6 calls)
  4. MATCHUP  blind pairwise on the top 3, per persona              (9 calls)
  5. POLISH   light voice-preserving edit of the winner             (1 call)

Weekly review (phase 2): the user pastes per-post numbers for posted drafts; "Analyze week" asks the gateway what over/under-performed and
returns a few short "learnings" (max 8 active) appended to the brief of later runs. Rank correlation between simulated score and real
engagement is reported only once there are >= 10 reviewed posts.

Polish correction: the founder can tell the editor what is off; hand edits + a diff of them are sent along, every correction/edit is
stored as a revision, one reusable lesson per correction goes into memory, and recent real edits are shown in later briefs.

V3 (engine='v3', see V3_SPEC.md): Sonnet plans first (core line, audience, post type, hooks) and may ask the founder up to 3
questions about missing facts (run status 'needs_input'), then writes 3 English drafts, checks them against the founder's
standards checklist reading as the chosen audience, rewrites failures once, picks the best, writes a native German (du) version
and a first comment for the link. One audience and one post type per post.

Notes for the agent: each idea can carry short founder notes (tone, length, hook, include/avoid). They are snapshotted
into the run and sent to the writer, the polish step and Polish correction, where they outrank the angle and format rules.

Reliability rules: every LLM result is persisted per step (a restart/failure resumes without
re-spending), one run at a time, hard per-run call cap, calls go ONLY through the tool-less,
capped host gateway (never the hermes-agent model). Simulated scores are a heuristic, not a
prediction of LinkedIn reach — the API says so and the UI repeats it.
"""
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.request
import zlib
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from open_webui.utils.auth import get_verified_user

log = logging.getLogger("linkedin_drafter")
router = APIRouter()

DATA_DIR = Path(os.environ.get("DRAFTER_DATA_DIR", "/app/backend/data/linkedin_drafter"))
GATEWAY_URL = os.environ.get("DRAFTER_GATEWAY_URL", "http://host.docker.internal:8650")
FAKE = os.environ.get("DRAFTER_FAKE_LLM") == "1"
MAX_CALLS_PER_RUN = 36
MAX_CALLS_PER_RUN_V2 = 60  # 12 writes + 6 scores + 18 matchups + 2 polish = 38, plus retries


MAX_CALLS_PER_RUN_V3 = 24  # plan + 3 writes + check + up to 3 fixes + recheck + German (write/check/fix/recheck) + comment, plus retries


def max_calls(v2, engine=None):
    if engine == "v3":
        return MAX_CALLS_PER_RUN_V3
    return MAX_CALLS_PER_RUN_V2 if v2 else MAX_CALLS_PER_RUN
# Writer steps use a non-reasoning model: glm-5.3-flash (reasoning forced low) intermittently burns
# the whole completion budget on hidden reasoning and returns empty posts. Scoring/pair/polish stay
# on the gateway default so rankings stay comparable across runs.
WRITER_MODEL = os.environ.get("DRAFTER_WRITER_MODEL") or "deepseek/deepseek-v4.1-flash"
PARALLEL = 3
DISCLAIMER = "Simulated score: a heuristic for catching weak hooks, slop and off-audience posts. Not a forecast of LinkedIn reach."

# --------------------------------------------------------------------------- defaults
DEFAULT_BRIEF = """WHO I AM
Solo founder in Berlin building Bunny Search. Previously built HackaMaps (a hackathon directory).

BUNNY SEARCH ONE-LINER (say this once per post, in natural words, meaning intact)
Bunny Search finds UGC creators on Instagram and TikTok by niche and city, pulled fresh when you search instead of from a stale $300/month database.

STATUS (October 2026)
Live with payments since October 2026. $0 MRR, a handful of warm leads. I missed my September 2026 launch target.

LINK (only for the first comment, never in the post body)
[add your Bunny Search URL here]

WHO I WRITE FOR
One audience per post. Main buyer: owners and growth/marketing leads of small direct-to-consumer / e-commerce brands in DACH and Europe who source UGC creators themselves. Also: marketing agencies running creator campaigns, Berlin SMEs, and indie founders following build-in-public (they amplify, they rarely buy).

WHAT THEY CARE ABOUT
Finding real creators fast without paying for stale databases, avoiding fake followers, creators in the right country/audience, not losing hours to manual DMs, proof before spending.

CONTENT PILLARS (rotate)
1) Build in public: honest progress and real numbers. 2) Buyer insight: creator-sourcing problems, data and mistakes I see while building. 3) Engineer credibility: how it is built (fresh scraping, data quality, privacy). 4) Sharp opinions on creator-tool pricing and database staleness.

VOICE
Plain, specific, a bit dry. First person. Short sentences. Admit what is unfinished. No hype, no corporate speak, no "excited to announce". Honest numbers only; never invent customers, results or quotes.

LINKEDIN FORMAT
The hook must work in the first two lines (before "see more"). Short paragraphs, generous line breaks. 900-1,400 characters. No links in the body. At most 2 hashtags, only if natural. At most one emoji."""

DEFAULT_PERSONAS = [
    "Maya, 34, founder of a 2-person skincare DTC brand in Berlin. Does creator outreach by hand on Fridays. Skeptical of tools, hates fluff, buys when something saves her hours.",
    "Jonas, 29, growth lead at a 15-person home & lifestyle e-commerce brand in Hamburg. Runs paid + UGC. Judges a post by whether it teaches him something he can use on Monday.",
    "Priya, 41, bootstrapped SaaS founder who follows build-in-public. A skeptical peer: spots clichés, humble-brags and vague claims instantly.",
]

ANGLES = [
    ("contrarian", "Contrarian take: use the raw idea to challenge a belief your audience holds. If the idea is about a tool, method or habit, challenge the belief about THAT — not about the founder's product."),
    ("lesson", "Personal lesson: the specific moment, mistake or realization inside the raw idea (use only what the raw idea gives you)."),
    ("example", "Concrete example: a small scenario or mini-case that makes the raw idea tangible. If it is an illustration, say it is hypothetical."),
    ("howto", "How-to / checklist: 3-5 practical steps the reader can apply this week, derived from the raw idea."),
    ("buildinpublic", "Build-in-public update: share the raw idea as an honest note from your week of building — what you tried, what changed, what is still unfinished. If the raw idea is a list of findings or features, present it as what you learned from actually using the thing. Use only facts that appear in the raw idea; if it contains no numbers, write it without numbers."),
    ("question", "Question-led: open with a sharp question the raw idea answers, then answer it briefly and invite replies."),
]

# --------------------------------------------------------------------------- V3 (Sonnet) building blocks
V3_MODEL = os.environ.get("DRAFTER_V3_MODEL") or "anthropic/claude-sonnet-5"
AUDIENCES = {
    "dtc": ("DTC brands", "Owners and growth/marketing leads of small direct-to-consumer / e-commerce brands in DACH and Europe who find "
            "and contact UGC creators themselves. Their pain: hours lost hunting creators by hand, stale paid databases, fake followers, "
            "creators in the wrong country. They answer questions about how they find, vet and pay creators."),
    "agency": ("Agencies", "People at small marketing / influencer agencies who build creator shortlists for several client brands. "
               "Their pain: a fresh shortlist for every new brief, proving creator fit to the client, many niches and cities at once. "
               "They answer questions about their sourcing workflow and tools."),
    "sme": ("Berlin SMEs", "Owners of local Berlin small businesses (cafes, studios, shops, services) who want local creators or content "
            "but have no marketing team. Their pain: not knowing where to start, little time, small budgets. They answer simple, "
            "practical questions about their own business."),
    "founders": ("Founders", "Indie founders and solo builders who follow build-in-public. They amplify and give feedback but rarely "
                 "buy. They answer questions about their own building habits, pricing and launches."),
}
POST_TYPES = {
    "build": ("Build in public", "An honest update from building Bunny Search: what happened, real numbers, what is unfinished, "
              "what you learned."),
    "insight": ("Buyer insight", "Something useful for the chosen audience about THEIR problem (finding, vetting, working with "
                "creators), drawn from what you see while building Bunny Search. The product is context, not the topic."),
    "engineer": ("Engineer credibility", "How it is built: a technical decision, trade-off or problem you solved (fresh scraping, data "
                 "quality, privacy, speed), explained so a non-technical buyer trusts the product more."),
}
STANDARDS = {
    "hook": ("Hook = best line", "The single strongest line or insight of the post is in the first two lines (before 'see more'), "
             "not buried in the middle."),
    "brand": ("Says what Bunny Search is", "The post says what Bunny Search does, once, using the one-liner from the brief (natural "
              "wording, meaning intact), speaks to the chosen audience's problem, and never reads like an ad."),
    "ending": ("Easy closing question", "The last line is a concrete question with a short factual answer: yes/no, a choice "
               "between two options, a number or a tool name (e.g. 'Did you charge from day one, or wait?'). A two-option question "
               "passes only if each option is a plain fact about what the reader did or uses. It FAILS if answering needs "
               "reflection or self-analysis ('What are you putting off?', 'Do you avoid the task you know or the one you don't?'), "
               "or if it is a thank-you or journey closer."),
    "truth": ("True, current, specific", "Every date fits TODAY (no past date presented as a future target). Every concrete detail "
              "(numbers, durations, quotes, what a user said, named events, how something went) appears in the SOURCE FACTS; a "
              "detail that is not there is invented and FAILS, even if it sounds harmless ('rewrote the function four times'). At "
              "least one real concrete detail from the sources is used, and no vague stand-in hides a missing fact."),
}
V3_DISCLAIMER = ("Checks and scores come from Sonnet reading as your chosen audience. They are a quality bar, not a reach forecast; "
                 "your real numbers in Review are what count.")
V3_PLAN = (
    "You are the founder's LinkedIn editor. Before anything is written, think the raw idea through and make a plan.\n"
    "Decide: who this one post is for (keep the founder's audience and post type unless they say 'auto'), the single strongest "
    "line, three different opening hooks that lead with it, the concrete facts to use (only facts that are in the sources), and "
    "a closing question with a short factual answer (yes/no, one of two options, a number or a tool), e.g. 'Did you charge from "
    "day one, or wait?'.\n"
    "Then list what is MISSING: at most 3 short questions to the founder about facts that would make the post concrete or true "
    "and that are NOT already in the idea, notes or brief (e.g. what exactly an early user said, a number, a date). Never ask "
    "about style or tone. If nothing important is missing, return an empty list.\n"
    'Return ONLY JSON: {"audience":"<key>","post_type":"<key>","reasoning":"2-4 sentences: who it is for, what the strongest '
    'line is, what is missing","core_line":"...","hooks":["...","...","..."],"must_include":["..."],'
    '"ending_question":"...","questions":[{"ask":"...","why":"max 12 words"}]}')
V3_WRITER = (
    "You ghost-write ONE LinkedIn post for one founder, in their voice, for the one reader described below.\n"
    "The raw idea is the subject. Use only facts from the brief, the raw idea, the author's notes and the founder's answers; "
    "never invent customers, quotes, numbers, durations, dates, events or colour details (no made-up 'rewrote it four times', "
    "'fought webhooks for days', no user quote you were not given). When a fact is missing, say less instead of filling the gap.\n"
    "Plain, specific, a bit dry, first person, short sentences, short paragraphs with line breaks. No hype, no corporate phrases, "
    "no 'game-changer', no 'excited to announce', no 'Here's the thing', no lists of three adjectives, few em-dashes.\n"
    "900-1,400 characters. No links in the body (the link goes in the first comment). At most 2 hashtags, only if natural; at "
    "most one emoji. Meet every standard listed below. AUTHOR'S NOTES outrank the default format.\n"
    "Output contract: reply with the post text only, starting with its first character. No preamble, no notes, no fences.")
V3_CHECK = (
    "You are a strict LinkedIn editor, and you also read as the target reader described below. Check each post against EVERY "
    "listed standard. Pass only if the post clearly meets it. For 'truth', compare every concrete detail in the post with the "
    "SOURCE FACTS and the brief: anything not there is invented. For each standard give pass true/false and 'why' (max 18 words; "
    "quote the problem line when it fails). Then score the post 1-10 as the target reader (10 = I stop, read to the end and "
    "reply) and give a one-sentence first-person reaction as that reader.\n"
    'Return ONLY JSON: {"posts":{"A":{"checks":{"<standard key>":{"pass":true,"why":"..."}},"score":7,"reaction":"..."}}} '
    "with one entry per post letter.")
V3_FIX = (
    "You are the founder's editor. Rewrite the post so it passes the failed standards listed, keeping everything that already "
    "works: voice, facts, language, most of the wording. NEVER add a quote, number, duration, anecdote or detail that is not in "
    "the brief, idea, notes or founder's answers, not even to pass a standard. If a detail failed as invented, remove it. If a "
    "standard needs a fact you do not have (e.g. a real user quote), say less: use a true detail that IS available, or cut the "
    "vague claim.\nOutput contract: the full revised post only, starting with its first character.")
V3_COMMENT = (
    "Write the founder's FIRST COMMENT under their own LinkedIn post, once in English and once in German (informal du). One or two "
    "short sentences each: a plain pointer to Bunny Search with the link from the brief (section LINK). If the brief has no real "
    "URL there, write [link]. No hype, no emoji, no hashtags.\n"
    'Return ONLY JSON: {"en":"...","de":"..."}')


AI_ISMS = re.compile(
    r"\b(game[- ]?chang\w*|delve\w*|unlock\w*|supercharg\w*|leverag\w*|in today'?s (fast[- ]paced|digital|competitive)|"
    r"landscape|tapestry|elevat\w*|seamless\w*|revolutioni[sz]\w*|cutting[- ]edge|synerg\w*|embark\w*|"
    r"thrilled|excited to (announce|share)|let that sink in|here'?s the thing|buckle up)\b", re.I)
LINK_RE = re.compile(r"(https?://|www\.)\S+", re.I)
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

CRITERIA = ["stop_scroll", "relevance", "credibility", "comment", "action"]
WEIGHTS = {"stop_scroll": 0.30, "relevance": 0.25, "credibility": 0.20, "comment": 0.15, "action": 0.10}

# --------------------------------------------------------------------------- storage
_init_lock = threading.Lock()
_inited = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile(user_id TEXT PRIMARY KEY, brief TEXT NOT NULL, personas TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ideas(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, idea_id INTEGER NOT NULL, user_id TEXT NOT NULL,
  status TEXT NOT NULL, step TEXT NOT NULL DEFAULT '', progress INTEGER NOT NULL DEFAULT 0, calls INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0, error TEXT, brief TEXT NOT NULL, personas TEXT NOT NULL, idea_text TEXT NOT NULL,
  summary TEXT, created_at INTEGER NOT NULL, finished_at INTEGER);
CREATE TABLE IF NOT EXISTS steps(run_id INTEGER NOT NULL, key TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(run_id, key));
CREATE TABLE IF NOT EXISTS drafts(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, idea_id INTEGER NOT NULL, user_id TEXT NOT NULL,
  angle TEXT NOT NULL, text TEXT NOT NULL, flags TEXT NOT NULL DEFAULT '[]', dropped INTEGER NOT NULL DEFAULT 0,
  scores TEXT, score REAL, rank INTEGER, selected INTEGER NOT NULL DEFAULT 0, pairwise_wins INTEGER NOT NULL DEFAULT 0,
  polished_text TEXT, edited_text TEXT, posted_at INTEGER);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, draft_id INTEGER NOT NULL,
  impressions INTEGER, reactions INTEGER, comments INTEGER, reposts INTEGER, profile_views INTEGER, icp_note TEXT,
  week_start TEXT, created_at INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_draft ON reviews(draft_id);
CREATE TABLE IF NOT EXISTS learnings(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, note TEXT NOT NULL,
  source_week TEXT, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS insights(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, week_start TEXT NOT NULL,
  result TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id INTEGER NOT NULL, user_id TEXT NOT NULL,
  kind TEXT NOT NULL, instruction TEXT, before_text TEXT NOT NULL, after_text TEXT NOT NULL, lesson TEXT, learning_id INTEGER,
  undone INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_revisions_draft ON revisions(draft_id);
CREATE INDEX IF NOT EXISTS idx_runs_idea ON runs(idea_id);
CREATE INDEX IF NOT EXISTS idx_drafts_run ON drafts(run_id);
"""


@contextmanager
def db():
    """Short-lived connection: commit on success, roll back on error, always closed."""
    global _inited
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DATA_DIR / "drafter.db"), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        if not _inited:
            with _init_lock:
                if not _inited:
                    con.execute("PRAGMA journal_mode=WAL")
                    con.executescript(SCHEMA)
                    for table, col, ddl in (('learnings', 'kind', "ALTER TABLE learnings ADD COLUMN kind TEXT NOT NULL DEFAULT 'review'"),
                                            ('drafts', 'ai_text', 'ALTER TABLE drafts ADD COLUMN ai_text TEXT'),
                                            ('ideas', 'notes', "ALTER TABLE ideas ADD COLUMN notes TEXT NOT NULL DEFAULT ''"),
                                            ('runs', 'notes', "ALTER TABLE runs ADD COLUMN notes TEXT NOT NULL DEFAULT ''"),
                                            ('runs', 'engine', "ALTER TABLE runs ADD COLUMN engine TEXT NOT NULL DEFAULT ''"),
                                            ('runs', 'meta', "ALTER TABLE runs ADD COLUMN meta TEXT"),
                                            ('ideas', 'audience', "ALTER TABLE ideas ADD COLUMN audience TEXT NOT NULL DEFAULT ''"),
                                            ('ideas', 'post_type', "ALTER TABLE ideas ADD COLUMN post_type TEXT NOT NULL DEFAULT ''"),
                                            ('ideas', 'standards', "ALTER TABLE ideas ADD COLUMN standards TEXT"),
                                            ('drafts', 'lang', "ALTER TABLE drafts ADD COLUMN lang TEXT NOT NULL DEFAULT 'en'"),
                                            ('reviews', 'dms', "ALTER TABLE reviews ADD COLUMN dms INTEGER"),
                                            ('reviews', 'signups', "ALTER TABLE reviews ADD COLUMN signups INTEGER")):
                        if col not in {r[1] for r in con.execute(f'PRAGMA table_info({table})')}:
                            con.execute(ddl)
                    con.commit()
                    _inited = True
        _migrate_v2(con)
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def now():
    return int(time.time())


def _migrate_v2(con):
    """Safe, silent upgrade for the v2 compare-mode columns (old DBs gain them on first use)."""
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
        if "v2" not in cols:
            con.execute("ALTER TABLE runs ADD COLUMN v2 INTEGER NOT NULL DEFAULT 0")
    except Exception:  # noqa: BLE001
        pass
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(drafts)")}
        if "variant_set" not in cols:
            con.execute("ALTER TABLE drafts ADD COLUMN variant_set TEXT NOT NULL DEFAULT 'v1'")
    except Exception:  # noqa: BLE001
        pass
    con.commit()


# --------------------------------------------------------------------------- LLM access (gateway only)
class CapReached(Exception):
    pass


class RunAbort(Exception):
    pass


_fake_calls = {"n": 0}


def gateway_token():
    try:
        return (DATA_DIR / ".gateway_token").read_text().strip()
    except OSError:
        return ""


def gw_complete(system, user, *, max_tokens=1400, temperature=0.8, tag="drafter", model=None, timeout=100, retry_timeout=True):
    """Returns {'text','cost'}. Raises CapReached on the gateway's daily cap, RuntimeError otherwise."""
    if FAKE:
        return fake_complete(system, user, tag)
    tok = gateway_token()
    if not tok:
        raise RuntimeError("gateway token missing")
    payload = {"system": system, "user": user, "max_tokens": max_tokens, "temperature": temperature, "tag": tag}
    if model:
        payload["model"] = model
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(GATEWAY_URL + "/complete", body,
                                         {"X-Gateway-Token": tok, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            return {"text": d.get("text", ""), "cost": float((d.get("usage") or {}).get("cost") or 0.0)}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise CapReached() from e
            last = f"gateway HTTP {e.code}"
            if e.code not in (502, 503):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"gateway unreachable ({type(e).__name__})"
            if not retry_timeout and ("timed out" in str(e) or isinstance(e, TimeoutError)):
                break
        time.sleep(2)
    raise RuntimeError(last or "gateway error")


def fake_complete(system, user, tag):
    """Deterministic stand-in so the whole pipeline is testable without spending credits."""
    _fake_calls["n"] += 1
    fail_at = int(os.environ.get("DRAFTER_FAKE_FAIL_AT", "0") or 0)
    if fail_at and _fake_calls["n"] == fail_at:
        raise RuntimeError("fake transient failure")
    delay = float(os.environ.get("DRAFTER_FAKE_DELAY", "0") or 0)
    if delay:
        time.sleep(delay)
    seed = zlib.crc32((tag + user[:200]).encode())
    rnd = random.Random(seed)
    if tag.startswith("v3:plan"):
        auto = "AUDIENCE SET BY THE FOUNDER: auto" in user
        return {"text": json.dumps({"audience": "founders" if auto else re.search(r"AUDIENCE SET BY THE FOUNDER: (\w+)", user).group(1),
                                    "post_type": "build", "reasoning": "Fake plan: the strongest line is the payment button one.",
                                    "core_line": "As long as there is no payment button, the product is still potentially useful.",
                                    "hooks": ["Adding payments took 2 hours.", "Avoiding it took 6 weeks.", "The button forces an answer."],
                                    "must_include": ["Stripe took a week on HackaMaps"], "ending_question": "Did you charge from day one?",
                                    "questions": [] if "NOQUESTIONS" in user else
                                    [{"ask": "What did the early user actually say?", "why": "a real quote beats a summary"},
                                     {"ask": "How many early users gave feedback?", "why": "a number makes it concrete"}]}), "cost": 0.0}
    if tag.startswith("v3:write") or tag.startswith("v3:de"):
        idx = tag.split(":")[-1]
        de = tag.startswith("v3:de")
        body = (("Zahlungen in Bunny Search haben 2 Stunden gedauert. " if de else f"Adding payments took 2 hours (draft {idx}). ")
                + "\n\n" + ("Konkrete Details zählen. " if de else "Specifics matter here. ") * 20
                + ("\n\nHast du ab Tag eins Geld verlangt?" if de else "\n\nDid you charge from day one, or wait?"))
        return {"text": body, "cost": 0.0}
    if tag.startswith("v3:check") or tag.startswith("v3:recheck"):
        letters = sorted(set(re.findall(r"^\[([A-C])\]", user, re.M)))
        keys = re.findall(r"^- (hook|brand|ending|truth):", system, re.M)
        out = {}
        for n, L in enumerate(letters):
            fail = "truth" if (n == 1 and not tag.startswith("v3:recheck")) else None
            out[L] = {"checks": {k: {"pass": k != fail, "why": "fake: no concrete detail" if k == fail else "fake ok"} for k in keys},
                      "score": 8 - n, "reaction": f"As this reader I would read post {L} to the end."}
        return {"text": json.dumps({"posts": out}), "cost": 0.0}
    if tag.startswith("v3:fix"):
        m = re.search(r"POST:\n(.*)\Z", user, re.S)
        return {"text": (m.group(1) if m else "x" * 300).strip() + "\n\n[fixed: added a concrete detail]", "cost": 0.0}
    if tag == "v3:comment":
        return {"text": json.dumps({"en": "Bunny Search, if you want to try it: [link]", "de": "Bunny Search zum Ausprobieren: [link]"}),
                "cost": 0.0}
    if tag.startswith("write"):
        idx = int(tag.split(":")[1])
        hook = f"Most creator databases are wrong about {idx} thing{'s' if idx else ''}."
        body = (hook + "\n\nI check this every week. Fresh data changes who you contact.\n\n" + ("Specifics matter. " * 40)).strip()
        if idx == 2:
            body += "\n\nTry it: https://example.com a real game-changer to leverage."  # trips link + AI-ism filters
        if idx == 4:
            body = "Too short."  # dropped by the length filter
        return {"text": body, "cost": 0.0}
    if tag.startswith("score"):
        letters = sorted(set(re.findall(r"^\[([A-L])\]", user, re.M)))
        out = {}
        for L in letters:
            out[L] = {c: rnd.randint(2, 5) for c in CRITERIA}
            out[L]["slop"] = rnd.randint(1, 3)
            out[L]["reaction"] = f"As a reader I would {'stop' if rnd.random() > .4 else 'scroll'} on {L}."
            out[L]["flaw"] = "needs a sharper first line"
        return {"text": json.dumps({"scores": out}), "cost": 0.0}
    if tag.startswith("pair"):
        return {"text": json.dumps({"winner": rnd.choice(["X", "Y"]), "why": "clearer hook and more specific"}), "cost": 0.0}
    if tag == "refine":
        cur = re.search(r"CURRENT POST[^\n]*:\n(.*?)\n\nNUMBERED LINES.*?FOUNDER'S INSTRUCTION:\n(.*?)\n\nReturn", user, re.S)
        text, instr = (cur.group(1), cur.group(2)) if cur else ("x" * 150, "")
        kept = " [kept-edits]" if re.search(r"FOUNDER EDITS[^\n]*\n(?!\(none)", user) else ""
        lesson = "Never open with a dramatic rhetorical hook." if "fake" in instr.lower() else ""
        return {"text": json.dumps({"text": text + "\n\n[revised: " + instr[:40] + "]" + kept, "lesson": lesson}), "cost": 0.0}
    if tag == "review":
        return {"text": json.dumps({"summary": "Fake summary of the week.", "worked": ["Concrete numbers in the hook"],
                                    "didnt": ["Long checklists"], "icp_signal": "mixed: some brand owners, mostly peers",
                                    "notes": ["Open with one concrete number.", "Keep checklists to 3 steps.", "Ask one specific question at the end."]}),
                "cost": 0.0}
    if tag == "polish":
        m = re.search(r"DRAFT:\n(.*)\Z", user, re.S)
        return {"text": (m.group(1) if m else user).strip() + "\n", "cost": 0.0}
    return {"text": "ok", "cost": 0.0}


# --------------------------------------------------------------------------- run context (budget + persisted steps)
class Ctx:
    def __init__(self, run_id):
        self.run_id = run_id
        self.lock = threading.Lock()

    def status(self):
        with db() as con:
            r = con.execute("SELECT status FROM runs WHERE id=?", (self.run_id,)).fetchone()
        return r["status"] if r else "missing"

    def check(self):
        if self.status() == "cancelled":
            raise RunAbort("cancelled")

    def progress(self, step, pct):
        with db() as con:
            con.execute("UPDATE runs SET step=?, progress=? WHERE id=?", (step, pct, self.run_id))

    def cached(self, key):
        with db() as con:
            r = con.execute("SELECT result FROM steps WHERE run_id=? AND key=?", (self.run_id, key)).fetchone()
        return json.loads(r["result"]) if r else None

    def llm_step(self, key, system, user, *, max_tokens=1400, temperature=0.8, tag=None, min_chars=0, model=None,
                 timeout=100, retry_timeout=True):
        """Run (or reuse) one persisted LLM step. Returns the text. min_chars: results below this length are NOT cached (used by write steps)."""
        hit = self.cached(key)
        if hit is not None:
            return hit["text"]
        self.check()
        with self.lock:
            with db() as con:
                row = con.execute("SELECT calls, v2, engine FROM runs WHERE id=?", (self.run_id,)).fetchone()
                cap = max_calls(row["v2"], row["engine"])
                if row["calls"] >= cap:
                    raise RunAbort(f"call cap of {cap} reached")
                con.execute("UPDATE runs SET calls=calls+1 WHERE id=?", (self.run_id,))
        res = gw_complete(system, user, max_tokens=max_tokens, temperature=temperature, tag=tag or key, model=model,
                          timeout=timeout, retry_timeout=retry_timeout)
        if min_chars and len(res["text"].strip()) < min_chars:
            return res["text"]  # junk/empty output: leave nothing cached so a retry generates fresh
        with db() as con:
            con.execute("INSERT OR REPLACE INTO steps(run_id,key,result) VALUES(?,?,?)",
                        (self.run_id, key, json.dumps({"text": res["text"]})))
            con.execute("UPDATE runs SET cost_usd=cost_usd+? WHERE id=?", (res["cost"], self.run_id))
        return res["text"]


# --------------------------------------------------------------------------- prompts + parsing
def writer_system(brief, v2=False):
    base = ("You ghost-write LinkedIn posts for ONE specific founder, in their voice.\n"
            "THE RAW IDEA IS THE SUBJECT. The raw idea in the user message is what the post is about, from hook to closing line. "
            "Stay on it; do not swap in a different topic.\n"
            "The brief supplies voice, background and audience context only — it is not a content quota. Do not turn the post into an ad "
            "for the founder's product: mention the product or company at most once, and only if it connects naturally to the raw idea. "
            "If the idea has nothing to do with the product, a standalone post about the idea is the CORRECT output, not a flaw.\n"
            "Use ONLY facts from the brief, the raw idea and the author's notes. Never invent customers, numbers, quotes, results or events; "
            "if an illustration needs a scenario, say plainly that it is hypothetical.\n"
            "Write like a person, not like LinkedIn: no hype, no corporate phrases, no 'game-changer', no 'excited to announce', "
            "no lists of three adjectives, no rhetorical 'Here's the thing'. Avoid em-dash overuse.\n"
            "Format: hook in the first two lines, short paragraphs, 900-1,400 characters, no links, at most 2 hashtags and only if natural, "
            "at most one emoji. English.\n"
            "Output contract: your reply must START with the first character of the post itself. No thinking out loud, no preamble, "
            "no notes before or after, no markdown fences, no JSON, no 'Here is the draft'.\n"
            "AUTHOR'S NOTES: if the user message has an AUTHOR'S NOTES section, it is the founder talking to you directly about THIS post "
            "(tone, length, hook, what to include or avoid, how to end). Follow every note. Where a note conflicts with the angle or the "
            "format rules above, the note wins. Facts stated in the notes are true. Notes are instructions, not text to quote.\n")
    if v2:
        base += ("\nHARD RULES (never break):\n"
                 "1. Keep exactly one concrete, verifiable detail from the brief (an audience, a price, a number). "
                 "Generic numbered steps read as AI filler.\n"
                 "2. Never end with a generic thank-you or journey closer ('thanks for reading', 'the journey continues', "
                 "'excited for what's next'). The last line must be a question a stranger can answer in one line, or a concrete next step.\n")
    return base + "\n=== BRIEF ===\n" + brief


def clean_post(t):
    t = (t or "").strip()
    t = re.sub(r"^```\w*\n|\n```$", "", t).strip()
    t = re.sub(r"^(here('s| is)[^\n]*:|post:)\s*\n+", "", t, flags=re.I).strip()
    if len(t) > 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    return t


def analyze(text):
    flags, drop = [], False
    n = len(text)
    if n < 300:
        flags.append("too short"); drop = True
    elif n < 600:
        flags.append("short")
    if n > 3000:
        flags.append("too long"); drop = True
    elif n > 1800:
        flags.append("long")
    hook = text.split("\n\n")[0].split("\n")[0]
    if len(hook) > 230:
        flags.append("hook too long")
    if LINK_RE.search(text):
        flags.append("link in body")
    if len(HASHTAG_RE.findall(text)) > 3:
        flags.append("too many hashtags")
    if len(EMOJI_RE.findall(text)) > 2:
        flags.append("emoji overuse")
    if text.count("—") > 3:
        flags.append("em-dash overuse")
    hits = sorted({m.group(0).lower() for m in AI_ISMS.finditer(text)})
    if hits:
        flags.append("AI-isms: " + ", ".join(hits[:4]))
    return flags, drop


def parse_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except ValueError:
            return None


def persona_name(p):
    return p.split(",")[0].strip()[:30] or "Reader"


def score_system(persona, brief, v2=False):
    comment = ("comment (would you reply?)")
    if v2:
        comment = ("comment (would you reply? A post that ends with a question you could answer in one line scores high here; "
                   "a post that ends with a statement or a thank-you caps at 2)")
    return (f"You are {persona}\nYou are scrolling LinkedIn. Candidate posts from the same founder follow. React as yourself: busy, a little skeptical.\n"
            "Judge each post on the topic it actually presents — not on whether it promotes the founder's product. A post gets no bonus for "
            "mentioning the founder's product and no penalty for leaving it out.\n"
            "Score honestly: most posts deserve 2-3; give 4-5 rarely. Integers 1-5 for stop_scroll (would you stop?), relevance (does this post "
            f"give you something interesting or useful as a founder, whatever the topic?), credibility (specific and believable?), {comment}, "
            "action (would you look the founder up / DM?), and slop (1 = sounds like a real person, 5 = generic AI/LinkedIn cliché).\n"
            'Return ONLY JSON: {"scores":{"A":{"stop_scroll":n,"relevance":n,"credibility":n,"comment":n,"action":n,"slop":n,'
            '"reaction":"max 20 words, first person","flaw":"max 12 words"}}} with one entry per post letter.\n'
            "Context about the founder:\n" + brief[:900])


def pair_system(persona):
    return (f"You are {persona}\nTwo LinkedIn posts from the same founder, X and Y. Pick the one you would rather read and engage with. "
            'Return ONLY JSON: {"winner":"X" or "Y","why":"max 20 words"}.')


# --------------------------------------------------------------------------- pipeline
def execute_run(run_id):
    with db() as con:
        eng = con.execute("SELECT engine FROM runs WHERE id=?", (run_id,)).fetchone()
    if eng is not None and eng["engine"] == "v3":
        return execute_v3(run_id)
    ctx = Ctx(run_id)
    try:
        with db() as con:
            run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            return
        brief, idea_text = run["brief"], run["idea_text"]
        notes_block = notes_section(run["notes"])
        personas = json.loads(run["personas"])
        v2 = bool(run["v2"])
        with db() as con:
            con.execute("UPDATE runs SET status='running', error=NULL WHERE id=?", (run_id,))

        # 1. WRITE -------------------------------------------------------------------
        if v2:
            ctx.progress("Writing 12 variants (6 V1 + 6 V2)", 5)
            sets = [("v1", False), ("v2", True)]
            write_jobs = [(set_key, v2_flag, i) for set_key, v2_flag in sets for i in range(len(ANGLES))]
        else:
            ctx.progress("Writing 6 variants", 5)
            write_jobs = [(None, False, i) for i in range(len(ANGLES))]

        def write(job):
            set_key, v2_flag, i = job
            angle_key, angle = ANGLES[i]
            wsys = writer_system(brief, v2=v2_flag)
            user = f"RAW IDEA (the subject of the post):\n{idea_text}\n\n{notes_block}ANGLE: {angle}\n\nWrite the post now."
            last_err = None
            for temp, suffix in ((0.5, ""),
                                 (0.35, "\n\nReply with the finished post only. The first character of your reply "
                                        "must be the first character of the post — no reasoning, no notes.")):
                key = f"write:{set_key}:{i}" if v2 else f"write:{i}"
                try:
                    t = ctx.llm_step(key, wsys, user + suffix, max_tokens=1400, temperature=temp,
                                     tag=f"write:{i}", min_chars=200, model=WRITER_MODEL)
                    if len(t.strip()) >= 200:
                        return set_key, i, t
                    last_err = RuntimeError(f"writer angle {i} ({angle_key}) returned only {len(t.strip())} chars of post")
                except Exception as e:  # noqa: BLE001
                    last_err = e
                time.sleep(2)
            raise last_err or RuntimeError(f"writer angle {i} ({angle_key}) produced no usable post")

        with ThreadPoolExecutor(PARALLEL) as ex:
            written = list(ex.map(write, write_jobs))

        # 2. FILTER + store drafts (idempotent) ------------------------------------------
        ctx.progress("Checking drafts", 30)
        with db() as con:
            have = con.execute("SELECT COUNT(*) c FROM drafts WHERE run_id=?", (run_id,)).fetchone()["c"]
            if have == 0:
                for set_key, i, t in written:
                    t = clean_post(t)
                    flags, drop = analyze(t)
                    con.execute("INSERT INTO drafts(run_id,idea_id,user_id,angle,text,flags,dropped,variant_set) VALUES(?,?,?,?,?,?,?,?)",
                                (run_id, run["idea_id"], run["user_id"], ANGLES[i][0], t, json.dumps(flags),
                                 1 if drop else 0, (set_key if v2 else "v1")))
            drafts = [dict(r) for r in con.execute("SELECT * FROM drafts WHERE run_id=? ORDER BY id", (run_id,))]
        alive = [d for d in drafts if not d["dropped"]]
        if not alive:
            raise RunAbort("all drafts were rejected by the quality filters; try a richer raw idea")

        # 3. SIMULATE: personas x 2 shuffled passes ---------------------------------------------
        ctx.progress("Simulating audience reactions", 40)
        letters = "ABCDEFGHIJKL"
        jobs = [(pi, k) for pi in range(len(personas)) for k in range(2)]

        def simulate(job):
            pi, k = job
            order = alive[:]
            random.Random(f"{run_id}:{pi}:{k}").shuffle(order)
            listing = "\n\n".join(f"[{letters[j]}]\n{d['text']}" for j, d in enumerate(order))
            ssys = score_system(personas[pi], brief, v2=v2)
            # GLM sometimes reasons until its budget is gone and answers empty after 60-150 s (seen on 12-post V2 batches).
            # Such a pass is skipped: the other passes still score every draft, so the run goes on.
            budget = 1600 if len(order) <= 6 else 2400
            try:
                txt = ctx.llm_step(f"score:{pi}:{k}", ssys, "Posts:\n\n" + listing, max_tokens=budget, temperature=0.4,
                                   tag=f"score:{pi}:{k}", timeout=80, retry_timeout=False)
                data = parse_json(txt)
                if data is None:  # one parse retry with a stricter nudge (separate persisted step)
                    txt = ctx.llm_step(f"score:{pi}:{k}:retry", ssys,
                                       "Return ONLY valid JSON.\n\nPosts:\n\n" + listing, max_tokens=budget, temperature=0.2,
                                       tag=f"score:{pi}:{k}", timeout=80, retry_timeout=False)
                    data = parse_json(txt)
            except RuntimeError as e:
                log.warning("run %s: scoring pass %s:%s skipped (%s)", run_id, pi, k, e)
                return pi, {}
            out = {}
            for j, d in enumerate(order):
                e = ((data or {}).get("scores") or {}).get(letters[j])
                if isinstance(e, dict):
                    out[d["id"]] = e
            return pi, out

        with ThreadPoolExecutor(PARALLEL) as ex:
            sim = list(ex.map(simulate, jobs))

        per = {d["id"]: {c: [] for c in CRITERIA + ["slop"]} for d in alive}
        reactions = {d["id"]: [] for d in alive}
        for pi, out in sim:
            for did, e in out.items():
                for c in CRITERIA + ["slop"]:
                    try:
                        per[did][c].append(max(1, min(5, float(e.get(c)))))
                    except (TypeError, ValueError):
                        pass
                if e.get("reaction") and not any(r["persona"] == persona_name(personas[pi]) for r in reactions[did]):
                    reactions[did].append({"persona": persona_name(personas[pi]), "reaction": str(e.get("reaction"))[:200],
                                           "flaw": str(e.get("flaw") or "")[:120]})
        agg = {}
        for d in alive:
            m = {c: (sum(v) / len(v) if v else None) for c, v in per[d["id"]].items()}
            if any(m[c] is None for c in CRITERIA):
                agg[d["id"]] = None
                continue
            base = sum(WEIGHTS[c] * m[c] for c in CRITERIA)
            slop = m["slop"] if m["slop"] is not None else 2.0
            nflags = len(json.loads(d["flags"]))
            pen = 0.25 * (slop - 1) + min(0.6, 0.15 * nflags)
            agg[d["id"]] = {"means": {c: round(m[c], 2) for c in m if m[c] is not None}, "agg": base - pen,
                            "norm": max(0.0, min(100.0, (base - pen - 1) / 4 * 100))}
        scored = [d for d in alive if agg[d["id"]]]
        if not scored:
            raise RunAbort("the audience simulation returned no usable scores; retry the run")

        # 4. MATCHUPS: blind pairwise on the top 3 (per set in compare mode) ---------------------
        ctx.progress("Final matchups", 70)
        if v2:
            pools = {set_key: [d for d in scored if d["variant_set"] == set_key] for set_key in ("v1", "v2")}
        else:
            pools = {"v1": scored}

        all_wins = {}
        per_pool_order = {}
        per_pool_winner = {}
        per_pool_final = {}
        for set_key in ("v1", "v2"):
            if set_key not in pools:
                continue
            pool = sorted(pools[set_key], key=lambda d: agg[d["id"]]["agg"], reverse=True)
            top = pool[:3]
            wins = {d["id"]: 0 for d in top}
            pairs = [(top[a], top[b]) for a in range(len(top)) for b in range(a + 1, len(top))]
            pjobs = [(pi, pr) for pi in range(len(personas)) for pr in range(len(pairs))]

            def matchup(job, _pairs=pairs):
                pi, pr = job
                a, b = _pairs[pr]
                rnd = random.Random(f"{run_id}:{set_key}:{pi}:{pr}")
                x, y = (a, b) if rnd.random() < 0.5 else (b, a)
                txt = ctx.llm_step(f"pair:{set_key}:{pi}:{pr}", pair_system(personas[pi]), f"[X]\n{x['text']}\n\n[Y]\n{y['text']}",
                                   max_tokens=500, temperature=0.2, tag=f"pair:{pi}:{pr}")
                data = parse_json(txt) or {}
                w = str(data.get("winner", "")).strip().upper()[:1]
                return (x["id"] if w == "X" else y["id"] if w == "Y" else None)

            if len(top) >= 2:
                with ThreadPoolExecutor(PARALLEL) as ex:
                    for winner_id in ex.map(matchup, pjobs):
                        if winner_id in wins:
                            wins[winner_id] += 1
            denom = max(1, len(personas) * (len(top) - 1)) if len(top) >= 2 else 1

            final = {}
            for d in pool:
                norm = agg[d["id"]]["norm"]
                final[d["id"]] = (0.65 * norm + 0.35 * (wins[d["id"]] / denom * 100)) if d["id"] in wins and len(top) >= 2 else norm
            top_ids = [d["id"] for d in top]
            order = sorted(top_ids, key=lambda i: final[i], reverse=True) + \
                sorted([d["id"] for d in pool[3:]], key=lambda i: final[i], reverse=True)
            all_wins.update(wins)
            per_pool_order[set_key] = order
            per_pool_winner[set_key] = order[0]
            per_pool_final[set_key] = final

        # 5. POLISH the winner(s) -------------------------------------------------------------
        ctx.progress("Polishing the winner", 90)
        polished_map = {}

        def do_polish(winner_id):
            winner = next(d for d in scored if d["id"] == winner_id)
            try:
                ptxt = ctx.llm_step(f"polish:{winner_id}" if v2 else "polish",
                                    "You are a careful editor. Lightly edit the draft: tighten the hook, cut clichés and filler, keep the "
                                    "founder's voice, meaning, facts and roughly the same length. Do not add claims or facts. "
                                    "If AUTHOR'S NOTES are given, keep the draft in line with them and never edit against them. Output ONLY the post text.",
                                    notes_block + "DRAFT:\n" + winner["text"], max_tokens=1400, temperature=0.4,
                                    tag=f"polish:{winner_id}" if v2 else "polish")
                p = clean_post(ptxt)
                return p if len(p) >= 200 else None
            except CapReached:
                raise
            except (RuntimeError, RunAbort):
                return None  # polish is optional; keep the original

        for set_key, winner_id in per_pool_winner.items():
            polished_map[winner_id] = do_polish(winner_id)

        with db() as con:
            for set_key, order in per_pool_order.items():
                final = per_pool_final[set_key]
                winner_id = per_pool_winner[set_key]
                for rank, did in enumerate(order, start=1):
                    con.execute("UPDATE drafts SET rank=?, score=?, scores=?, selected=?, pairwise_wins=? WHERE id=?",
                                (rank, round(final[did], 1),
                                 json.dumps({"means": agg[did]["means"], "reactions": reactions.get(did, [])}),
                                 1 if did == winner_id else 0, all_wins.get(did, 0), did))
            for wid, ptext in polished_map.items():
                con.execute("UPDATE drafts SET polished_text=? WHERE id=?", (ptext, wid))
            if v2:
                winner_ids = [per_pool_winner[s] for s in ("v1", "v2") if s in per_pool_winner]
                first_winner = winner_ids[0]
            else:
                winner_ids = [per_pool_winner["v1"]]
                first_winner = winner_ids[0]
            weakest = min(agg[first_winner]["means"].items(), key=lambda kv: kv[1] if kv[0] in CRITERIA else 99)[0]
            summary = {"winner_id": first_winner, "winner_ids": winner_ids, "weakest_criterion": weakest,
                       "personas": [persona_name(p) for p in personas], "disclaimer": DISCLAIMER, "v2": v2}
            con.execute("UPDATE runs SET status='done', step='Done', progress=100, summary=?, finished_at=? WHERE id=?",
                        (json.dumps(summary), now(), run_id))
    except CapReached:
        _fail(run_id, "The daily LLM gateway cap was reached. Try again tomorrow (completed steps are kept).")
    except RunAbort as e:
        if str(e) == "cancelled":
            return
        _fail(run_id, str(e))
    except Exception as e:  # noqa: BLE001
        log.error("run %s failed: %s\n%s", run_id, e, traceback.format_exc())
        _fail(run_id, f"{type(e).__name__}: {str(e)[:200]} (use Retry to resume; completed steps are kept)")
    finally:
        _ACTIVE.pop(run_id, None)
        try:
            _RUN_LOCK.release()
        except RuntimeError:
            pass


def v3_meta(run):
    try:
        return json.loads(run["meta"] or "{}")
    except (ValueError, TypeError):
        return {}


def _save_meta(run_id, meta):
    with db() as con:
        con.execute("UPDATE runs SET meta=? WHERE id=?", (json.dumps(meta), run_id))


def _today_line(meta):
    from datetime import date
    try:
        d = date.fromisoformat(meta.get("today") or "")
    except ValueError:
        from datetime import datetime, timezone
        d = datetime.now(timezone.utc).date()
    return f"{d.isoformat()} ({d.strftime('%A, %d %B %Y')})"


def v3_context(meta, brief, menus=False):
    std = [k for k in (meta.get("standards") or []) if k in STANDARDS]
    parts = [f"TODAY: {_today_line(meta)}. This is the real current date. Never present a date before it as a future plan or "
             "target; a past target in the brief was missed.",
             "=== FOUNDER BRIEF (facts, voice, audience) ===\n" + brief]
    if menus:
        parts.append("=== AUDIENCES (keys) ===\n" + "\n".join(f"- {k}: {v[0]}. {v[1]}" for k, v in AUDIENCES.items()))
        parts.append("=== POST TYPES (keys) ===\n" + "\n".join(f"- {k}: {v[0]}. {v[1]}" for k, v in POST_TYPES.items()))
    else:
        a, t = AUDIENCES.get(meta.get("audience")), POST_TYPES.get(meta.get("post_type"))
        if a:
            parts.append(f"=== WHO THIS POST IS FOR (one reader; write for them) ===\n{a[0]}: {a[1]}")
        if t:
            parts.append(f"=== POST TYPE ===\n{t[0]}: {t[1]}")
    if std:
        parts.append("=== STANDARDS THIS POST MUST MEET ===\n" + "\n".join(f"- {k}: {STANDARDS[k][1]}" for k in std))
    return "\n\n".join(parts)


def v3_facts(run, meta):
    out = f"RAW IDEA (the subject of the post):\n{run['idea_text']}\n\n" + notes_section(run["notes"])
    ans = meta.get("answers") or []
    got = [a for a in ans if a.get("answer")]
    skipped = [a for a in ans if not a.get("answer")]
    if got:
        out += "FOUNDER'S ANSWERS (true facts; use them):\n" + "\n".join(f"Q: {a['ask']}\nA: {a['answer']}" for a in got) + "\n\n"
    if skipped:
        out += ("NOT ANSWERED (do NOT invent these; write around them honestly):\n"
                + "\n".join("- " + a["ask"] for a in skipped) + "\n\n")
    pl = meta.get("plan") or {}
    if pl:
        out += (f"PLAN\nCore line: {pl.get('core_line', '')}\nMust include: {'; '.join(pl.get('must_include') or []) or '-'}\n"
                f"Closing question idea: {pl.get('ending_question', '')}\n\n")
    return out


def clean_plan(pl, meta):
    def s(v, n):
        return str(v or "").strip()[:n]
    aud = meta.get("audience") if meta.get("audience") in AUDIENCES else s(pl.get("audience"), 20)
    typ = meta.get("post_type") if meta.get("post_type") in POST_TYPES else s(pl.get("post_type"), 20)
    core = s(pl.get("core_line"), 400)
    hooks = [s(h, 300) for h in (pl.get("hooks") or []) if s(h, 300)][:3]
    while len(hooks) < 3:
        hooks.append(core or "Lead with the strongest line of the idea.")
    qs = []
    for q in (pl.get("questions") or [])[:3]:
        ask = s(q.get("ask") if isinstance(q, dict) else q, 220)
        if ask:
            qs.append({"ask": ask, "why": s(q.get("why") if isinstance(q, dict) else "", 120)})
    return {"audience": aud if aud in AUDIENCES else "dtc", "post_type": typ if typ in POST_TYPES else "build",
            "reasoning": s(pl.get("reasoning"), 900), "core_line": core, "hooks": hooks,
            "must_include": [s(x, 200) for x in (pl.get("must_include") or [])[:6] if s(x, 200)],
            "ending_question": s(pl.get("ending_question"), 300), "questions": qs}


def v3_check(ctx, key, meta, brief, texts, std, lang="English", facts=""):
    """One call checks up to 3 posts. Returns [{'checks':{k:{pass,why}},'score':int|None,'reaction':str}] (empty dicts on failure).
    facts = the run's source facts (idea, notes, answers, skipped questions) so invented details can be caught."""
    letters = "ABC"
    system = V3_CHECK + "\n\n" + v3_context(meta, brief)
    listing = "\n\n".join(f"[{letters[i]}]\n{t}" for i, t in enumerate(texts))
    user = (f"=== SOURCE FACTS (the only facts the founder gave) ===\n{facts}\n" if facts else "") + f"Posts ({lang}):\n\n{listing}"
    data = parse_json(ctx.llm_step(key, system, user, max_tokens=2400, temperature=0.2, tag=key, model=V3_MODEL))
    if not isinstance(data, dict) or not isinstance(data.get("posts"), dict):
        data = parse_json(ctx.llm_step(key + ":retry", system, "Return ONLY valid JSON.\n\n" + user, max_tokens=2400,
                                       temperature=0.1, tag=key, model=V3_MODEL))
    posts = (data or {}).get("posts") if isinstance(data, dict) else None
    out = []
    for i in range(len(texts)):
        e = (posts or {}).get(letters[i]) if isinstance(posts, dict) else None
        if not isinstance(e, dict):
            out.append({})
            continue
        checks = {}
        for k in std:
            c = (e.get("checks") or {}).get(k)
            if isinstance(c, dict):
                checks[k] = {"pass": bool(c.get("pass")), "why": str(c.get("why") or "")[:200]}
        try:
            score = max(1, min(10, int(round(float(e.get("score"))))))
        except (TypeError, ValueError):
            score = None
        out.append({"checks": checks, "score": score, "reaction": str(e.get("reaction") or "")[:240]})
    return out


def v3_passes(c, std):
    return {k for k in std if (c.get("checks") or {}).get(k, {}).get("pass")}


def v3_not_worse(new, old, std):
    """A fix may only replace the original if it passes at least as many standards and loses none it passed (truth included)."""
    return v3_passes(old, std) <= v3_passes(new, std)


def v3_failed(c, std):
    return any(not (c.get("checks") or {}).get(k, {"pass": True})["pass"] for k in std)


def v3_fix(ctx, key, meta, brief, facts, text, c, std, lang):
    failed = [k for k in std if not (c.get("checks") or {}).get(k, {"pass": True})["pass"]]
    if not failed:
        return None
    why = "\n".join(f"- {k} ({STANDARDS[k][0]}): {c['checks'][k]['why']}" for k in failed)
    system = V3_FIX + "\n\n" + v3_context(meta, brief)
    user = facts + f"FAILED STANDARDS:\n{why}\n\nLANGUAGE: {lang}.\n\nPOST:\n{text}"
    t = clean_post(ctx.llm_step(key, system, user, max_tokens=1800, temperature=0.4, tag=key, min_chars=200, model=V3_MODEL))
    return t if len(t) >= 200 else None


def _v3_scores(c):
    return json.dumps({"checks": c.get("checks") or {}, "reaction": c.get("reaction") or "", "score10": c.get("score")})


def execute_v3(run_id):
    ctx = Ctx(run_id)
    try:
        with db() as con:
            run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            return
        meta, brief = v3_meta(run), run["brief"]
        std = [k for k in (meta.get("standards") or []) if k in STANDARDS]
        with db() as con:
            con.execute("UPDATE runs SET status='running', error=NULL WHERE id=?", (run_id,))

        # 1. PLAN (+ questions) ------------------------------------------------------------
        if not meta.get("plan"):
            ctx.progress("Planning", 5)
            psys = V3_PLAN + "\n\n" + v3_context(meta, brief, menus=True)
            pu = (v3_facts(run, meta) + f"AUDIENCE SET BY THE FOUNDER: {meta.get('audience') or 'auto'}\n"
                  f"POST TYPE SET BY THE FOUNDER: {meta.get('post_type') or 'auto'}\n\nMake the plan now.")
            plan = parse_json(ctx.llm_step("v3:plan", psys, pu, max_tokens=2000, temperature=0.4, tag="v3:plan", model=V3_MODEL))
            if not isinstance(plan, dict):
                plan = parse_json(ctx.llm_step("v3:plan:retry", psys, pu + "\n\nReturn ONLY valid JSON.", max_tokens=2000,
                                               temperature=0.2, tag="v3:plan", model=V3_MODEL))
            if not isinstance(plan, dict):
                raise RunAbort("the planner returned no usable plan; use Retry")
            plan = clean_plan(plan, meta)
            meta.update(plan=plan, audience=plan["audience"], post_type=plan["post_type"])
            _save_meta(run_id, meta)
        plan = meta["plan"]
        if plan.get("questions") and meta.get("answers") is None:
            with db() as con:
                con.execute("UPDATE runs SET status='needs_input', step='Waiting for your answers', progress=15 WHERE id=?", (run_id,))
            return

        # 2. WRITE 3 English drafts, one per hook ---------------------------------------------
        ctx.progress("Writing 3 drafts", 25)
        wsys = V3_WRITER + "\n\n" + v3_context(meta, brief)
        facts = v3_facts(run, meta)

        def write(i):
            u = facts + f"OPENING: build the first two lines from this hook (tighten it if needed):\n{plan['hooks'][i]}\n\nLANGUAGE: English.\n\nWrite the post now."
            for key, temp in ((f"v3:write:{i}", 0.6), (f"v3:write:{i}:retry", 0.4)):
                t = clean_post(ctx.llm_step(key, wsys, u, max_tokens=1600, temperature=temp, tag=f"v3:write:{i}", min_chars=200, model=V3_MODEL))
                if len(t) >= 200:
                    return i, t
            raise RunAbort(f"draft {i + 1} came back empty; use Retry")

        with ThreadPoolExecutor(PARALLEL) as ex:
            written = list(ex.map(write, range(3)))
        with db() as con:
            if con.execute("SELECT COUNT(*) c FROM drafts WHERE run_id=? AND lang='en'", (run_id,)).fetchone()["c"] == 0:
                for i, t in written:
                    con.execute("INSERT INTO drafts(run_id,idea_id,user_id,angle,text,flags,dropped,variant_set,lang) VALUES(?,?,?,?,?,?,0,'v3','en')",
                                (run_id, run["idea_id"], run["user_id"], f"hook{i + 1}", t, json.dumps(analyze(t)[0])))
            en = [dict(r) for r in con.execute("SELECT * FROM drafts WHERE run_id=? AND lang='en' ORDER BY id", (run_id,))]

        # 3. CHECK against the standards, reading as the audience ------------------------------
        ctx.progress("Checking against your standards", 50)
        checked = v3_check(ctx, "v3:check", meta, brief, [d["text"] for d in en], std, facts=facts)

        # 4. FIX each failing draft once, then re-check the fixed ones -----------------------
        fails = [j for j, c in enumerate(checked) if v3_failed(c, std)]
        if fails:
            ctx.progress("Fixing what failed", 62)
            with ThreadPoolExecutor(PARALLEL) as ex:
                fixed = {j: t for j, t in ex.map(lambda j: (j, v3_fix(ctx, f"v3:fix:{j}", meta, brief, facts, en[j]["text"],
                                                                        checked[j], std, "English")), fails) if t}
            if fixed:
                order = sorted(fixed)
                for j, c in zip(order, v3_check(ctx, "v3:recheck", meta, brief, [fixed[j] for j in order], std, facts=facts)):
                    if c and v3_not_worse(c, checked[j], std):
                        checked[j] = c
                    else:
                        fixed.pop(j)  # the fix made it worse (e.g. invented a detail): keep the original
                with db() as con:
                    for j, t in fixed.items():
                        con.execute("UPDATE drafts SET polished_text=? WHERE id=?", (t, en[j]["id"]))
                        en[j]["polished_text"] = t

        # 5. PICK: most standards passed, then reader score ----------------------------------
        def rank_key(j):
            c = checked[j]
            return (sum(1 for k in std if (c.get("checks") or {}).get(k, {}).get("pass")), c.get("score") or 0)
        order = sorted(range(len(en)), key=rank_key, reverse=True)
        best = en[order[0]]
        best_text = best.get("polished_text") or best["text"]
        with db() as con:
            for rank, j in enumerate(order, 1):
                c = checked[j]
                con.execute("UPDATE drafts SET rank=?, score=?, scores=?, selected=? WHERE id=?",
                            (rank, (c.get("score") or 0) * 10, _v3_scores(c), 1 if rank == 1 else 0, en[j]["id"]))

        # 6. GERMAN, written natively from the same plan --------------------------------------
        ctx.progress("Writing the German version", 75)
        du = (facts + f"ENGLISH VERSION (same plan; use it for content, do NOT translate it line by line):\n{best_text}\n\n"
              "LANGUAGE: German. Write natively for German-speaking LinkedIn, informal 'du'. Same core line, facts and closing-question "
              "idea; render the Bunny Search one-liner in natural German.\n\nWrite the post now.")
        de_text = None
        for key, temp in (("v3:de", 0.5), ("v3:de:retry", 0.35)):
            t = clean_post(ctx.llm_step(key, wsys, du, max_tokens=1800, temperature=temp, tag="v3:de", min_chars=200, model=V3_MODEL))
            if len(t) >= 200:
                de_text = t
                break
        de_id = None
        if de_text:
            with db() as con:
                row = con.execute("SELECT id FROM drafts WHERE run_id=? AND lang='de'", (run_id,)).fetchone()
                de_id = row["id"] if row else con.execute(
                    "INSERT INTO drafts(run_id,idea_id,user_id,angle,text,flags,dropped,variant_set,lang) VALUES(?,?,?,?,?,'[]',0,'v3','de')",
                    (run_id, run["idea_id"], run["user_id"], "de", de_text)).lastrowid
            dc = v3_check(ctx, "v3:check:de", meta, brief, [de_text], std, lang="German", facts=facts)[0]
            de_fixed = None
            if v3_failed(dc, std):
                de_fixed = v3_fix(ctx, "v3:fix:de", meta, brief, facts, de_text, dc, std, "German (informal du)")
                if de_fixed:
                    dc2 = v3_check(ctx, "v3:check:de:fixed", meta, brief, [de_fixed], std, lang="German", facts=facts)[0]
                    if dc2 and v3_not_worse(dc2, dc, std):
                        dc = dc2
                    else:
                        de_fixed = None
            with db() as con:
                con.execute("UPDATE drafts SET rank=1, score=?, scores=?, selected=1, polished_text=? WHERE id=?",
                            ((dc.get("score") or 0) * 10, _v3_scores(dc), de_fixed, de_id))

        # 7. FIRST COMMENT (link lives here, not in the post) ---------------------------------
        ctx.progress("Writing the first comment", 92)
        try:
            cm = parse_json(ctx.llm_step("v3:comment", V3_COMMENT + "\n\n=== FOUNDER BRIEF ===\n" + brief, "POST (English):\n" + best_text,
                                         max_tokens=600, temperature=0.4, tag="v3:comment", model=V3_MODEL)) or {}
        except RuntimeError:
            cm = {}
        meta["first_comment"] = {"en": str(cm.get("en") or "")[:600], "de": str(cm.get("de") or "")[:600]}
        _save_meta(run_id, meta)
        with db() as con:
            summary = {"winner_id": best["id"], "winner_ids": [best["id"]] + ([de_id] if de_id else []), "engine": "v3",
                       "disclaimer": V3_DISCLAIMER}
            con.execute("UPDATE runs SET status='done', step='Done', progress=100, summary=?, finished_at=? WHERE id=?",
                        (json.dumps(summary), now(), run_id))
    except CapReached:
        _fail(run_id, "The daily LLM gateway cap was reached. Try again tomorrow (completed steps are kept).")
    except RunAbort as e:
        if str(e) == "cancelled":
            return
        _fail(run_id, str(e))
    except Exception as e:  # noqa: BLE001
        log.error("v3 run %s failed: %s\n%s", run_id, e, traceback.format_exc())
        _fail(run_id, f"{type(e).__name__}: {str(e)[:200]} (use Retry to resume; completed steps are kept)")
    finally:
        _ACTIVE.pop(run_id, None)
        try:
            _RUN_LOCK.release()
        except RuntimeError:
            pass


def _fail(run_id, msg):
    with db() as con:
        con.execute("UPDATE runs SET status='failed', error=?, finished_at=? WHERE id=?", (msg, now(), run_id))


# --------------------------------------------------------------------------- worker management
_RUN_LOCK = threading.Lock()
_ACTIVE = {}


def start_worker(run_id):
    """Start the pipeline thread if no other run is active. Returns True if started."""
    if not _RUN_LOCK.acquire(blocking=False):
        return False
    t = threading.Thread(target=execute_run, args=(run_id,), name=f"drafter-run-{run_id}", daemon=True)
    _ACTIVE[run_id] = t
    t.start()
    return True


def ensure_worker(run_id):
    """A run marked 'running' with no live thread (e.g. after a restart) resumes from its persisted steps."""
    with db() as con:
        r = con.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
    if r and r["status"] == "running" and run_id not in _ACTIVE:
        start_worker(run_id)


# --------------------------------------------------------------------------- API models
class ProfileIn(BaseModel):
    brief: str
    personas: List[str]


class IdeaIn(BaseModel):
    text: str
    notes: Optional[str] = None  # None = leave unchanged on update
    audience: Optional[str] = None  # V3: key of AUDIENCES or 'auto'; None = leave unchanged
    post_type: Optional[str] = None  # V3: key of POST_TYPES or 'auto'
    standards: Optional[List[str]] = None  # V3: enabled STANDARDS keys; None = leave unchanged


def clean_choice(v, table):
    v = (v or "").strip()
    if v in ("", "auto"):
        return ""
    if v not in table:
        raise HTTPException(400, f"unknown value '{v}'")
    return v


def clean_standards(v):
    return json.dumps([k for k in STANDARDS if k in set(v or [])])


def idea_v3(i):
    try:
        std = json.loads(i["standards"]) if i["standards"] is not None else list(STANDARDS)
    except ValueError:
        std = list(STANDARDS)
    return {"audience": i["audience"] or "auto", "post_type": i["post_type"] or "auto", "standards": std}


MAX_NOTES = 1500


def clean_notes(notes):
    notes = (notes or "").strip()
    if len(notes) > MAX_NOTES:
        raise HTTPException(400, f"notes must be at most {MAX_NOTES} characters")
    return notes


def notes_section(notes):
    notes = (notes or "").strip()
    if not notes:
        return ""
    return ("AUTHOR'S NOTES FOR THIS POST (the founder's own hints; follow them, they override the angle and format where they conflict):\n"
            + notes + "\n\n")


class EditIn(BaseModel):
    text: str


class PostedIn(BaseModel):
    posted: bool = True


class RunIn(BaseModel):
    v2: bool = False
    engine: Optional[str] = None  # 'v3' | 'v2' | 'v1' (None: v2 flag decides between v1 and v2)


class AnswersIn(BaseModel):
    answers: List[str] = []


def uid(user):
    return str(user.id)


def get_profile_row(con, user_id):
    r = con.execute("SELECT * FROM profile WHERE user_id=?", (user_id,)).fetchone()
    if r:
        return {"brief": r["brief"], "personas": json.loads(r["personas"]), "updated_at": r["updated_at"], "is_default": False}
    return {"brief": DEFAULT_BRIEF, "personas": DEFAULT_PERSONAS, "updated_at": None, "is_default": True}


def serialize_draft(r):
    sc = json.loads(r["scores"]) if r["scores"] else {}
    return {"id": r["id"], "angle": r["angle"], "text": r["text"], "polished_text": r["polished_text"],
            "edited_text": r["edited_text"], "flags": json.loads(r["flags"]), "dropped": bool(r["dropped"]),
            "score": r["score"], "rank": r["rank"], "selected": bool(r["selected"]), "pairwise_wins": r["pairwise_wins"],
            "variant_set": (r["variant_set"] if "variant_set" in r.keys() else "v1"), "lang": r["lang"],
            "checks": sc.get("checks"), "reaction": sc.get("reaction"), "score10": sc.get("score10"),
            "means": sc.get("means"), "reactions": sc.get("reactions", []), "posted_at": r["posted_at"]}


def serialize_run(con, run_id):
    r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    counts = {x["draft_id"]: x["c"] for x in con.execute(
        "SELECT draft_id, COUNT(*) c FROM revisions WHERE undone=0 AND draft_id IN (SELECT id FROM drafts WHERE run_id=?) GROUP BY draft_id", (run_id,))}
    drafts = [dict(serialize_draft(d), revisions=counts.get(d["id"], 0)) for d in con.execute(
        "SELECT * FROM drafts WHERE run_id=? ORDER BY rank IS NULL, rank, id", (run_id,))]
    return {"id": r["id"], "idea_id": r["idea_id"], "status": r["status"], "step": r["step"], "progress": r["progress"],
            "calls": r["calls"], "max_calls": max_calls(r["v2"], r["engine"]), "cost_usd": round(r["cost_usd"], 4), "error": r["error"],
            "v2": bool(r["v2"]), "engine": r["engine"] or ("v2" if r["v2"] else "v1"), "meta": v3_meta(r) if r["engine"] == "v3" else None,
            "notes": r["notes"], "summary": json.loads(r["summary"]) if r["summary"] else None, "created_at": r["created_at"],
            "finished_at": r["finished_at"], "drafts": drafts, "disclaimer": DISCLAIMER}


def own_idea(con, idea_id, user_id):
    r = con.execute("SELECT * FROM ideas WHERE id=? AND user_id=?", (idea_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "idea not found")
    return r


def own_run(con, run_id, user_id):
    r = con.execute("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "run not found")
    return r


# --------------------------------------------------------------------------- routes
@router.get("/status")
def status(user=Depends(get_verified_user)):
    gw = {"ok": False}
    if not FAKE:
        try:
            with urllib.request.urlopen(GATEWAY_URL + "/health", timeout=3) as r:
                gw = json.load(r)
        except Exception as e:  # noqa: BLE001
            gw = {"ok": False, "error": type(e).__name__}
    else:
        gw = {"ok": True, "fake": True, "calls_today": 0, "cap_calls": 0}
    return {"gateway": gw, "busy": _RUN_LOCK.locked(), "fake": FAKE, "max_calls_per_run": MAX_CALLS_PER_RUN,
            "v3": {"model": V3_MODEL, "audiences": {k: v[0] for k, v in AUDIENCES.items()},
                   "post_types": {k: v[0] for k, v in POST_TYPES.items()},
                   "standards": {k: {"label": v[0], "desc": v[1]} for k, v in STANDARDS.items()}}}


@router.get("/profile")
def get_profile(user=Depends(get_verified_user)):
    with db() as con:
        return get_profile_row(con, uid(user))


@router.put("/profile")
def put_profile(body: ProfileIn, user=Depends(get_verified_user)):
    brief = body.brief.strip()
    personas = [p.strip() for p in body.personas if p.strip()]
    if not (50 <= len(brief) <= 8000):
        raise HTTPException(400, "brief must be 50-8000 characters")
    if not (1 <= len(personas) <= 5) or any(len(p) > 600 for p in personas):
        raise HTTPException(400, "give 1-5 personas (max 600 characters each)")
    with db() as con:
        con.execute("INSERT INTO profile(user_id,brief,personas,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET brief=excluded.brief, personas=excluded.personas, updated_at=excluded.updated_at",
                    (uid(user), brief, json.dumps(personas), now()))
        return get_profile_row(con, uid(user))


@router.delete("/profile")
def reset_profile(user=Depends(get_verified_user)):
    with db() as con:
        con.execute("DELETE FROM profile WHERE user_id=?", (uid(user),))
        return get_profile_row(con, uid(user))


@router.get("/ideas")
def list_ideas(user=Depends(get_verified_user)):
    with db() as con:
        rows = con.execute("SELECT * FROM ideas WHERE user_id=? ORDER BY updated_at DESC LIMIT 200", (uid(user),)).fetchall()
        out = []
        for i in rows:
            run = con.execute("SELECT id,status,step,progress,v2,engine FROM runs WHERE idea_id=? ORDER BY id DESC LIMIT 1", (i["id"],)).fetchone()
            if run and run["status"] == "running":
                ensure_worker(run["id"])
            posted = con.execute("SELECT MAX(posted_at) p FROM drafts WHERE idea_id=? AND posted_at IS NOT NULL", (i["id"],)).fetchone()["p"]
            out.append({"id": i["id"], "text": i["text"], "notes": i["notes"], "created_at": i["created_at"], "updated_at": i["updated_at"],
                        "run": dict(run) if run else None, "posted": bool(posted), "posted_at": posted, **idea_v3(i)})
        return {"ideas": out}


@router.post("/ideas")
def create_idea(body: IdeaIn, user=Depends(get_verified_user)):
    text = body.text.strip()
    if not (10 <= len(text) <= 4000):
        raise HTTPException(400, "idea must be 10-4000 characters")
    notes = clean_notes(body.notes)
    aud, typ = clean_choice(body.audience, AUDIENCES), clean_choice(body.post_type, POST_TYPES)
    std = None if body.standards is None else clean_standards(body.standards)
    with db() as con:
        cur = con.execute("INSERT INTO ideas(user_id,text,notes,audience,post_type,standards,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (uid(user), text, notes, aud, typ, std, now(), now()))
        return {"id": cur.lastrowid}


@router.put("/ideas/{idea_id}")
def update_idea(idea_id: int, body: IdeaIn, user=Depends(get_verified_user)):
    text = body.text.strip()
    if not (10 <= len(text) <= 4000):
        raise HTTPException(400, "idea must be 10-4000 characters")
    notes = None if body.notes is None else clean_notes(body.notes)
    with db() as con:
        own_idea(con, idea_id, uid(user))
        con.execute("UPDATE ideas SET text=?, updated_at=? WHERE id=?", (text, now(), idea_id))
        if notes is not None:
            con.execute("UPDATE ideas SET notes=? WHERE id=?", (notes, idea_id))
        if body.audience is not None:
            con.execute("UPDATE ideas SET audience=? WHERE id=?", (clean_choice(body.audience, AUDIENCES), idea_id))
        if body.post_type is not None:
            con.execute("UPDATE ideas SET post_type=? WHERE id=?", (clean_choice(body.post_type, POST_TYPES), idea_id))
        if body.standards is not None:
            con.execute("UPDATE ideas SET standards=? WHERE id=?", (clean_standards(body.standards), idea_id))
    return {"ok": True}


@router.delete("/ideas/{idea_id}")
def delete_idea(idea_id: int, user=Depends(get_verified_user)):
    with db() as con:
        own_idea(con, idea_id, uid(user))
        run_ids = [r["id"] for r in con.execute("SELECT id FROM runs WHERE idea_id=?", (idea_id,))]
        if any(rid in _ACTIVE for rid in run_ids):
            raise HTTPException(409, "a run for this idea is in progress; cancel it first")
        for rid in run_ids:
            con.execute("DELETE FROM steps WHERE run_id=?", (rid,))
        con.execute("DELETE FROM reviews WHERE draft_id IN (SELECT id FROM drafts WHERE idea_id=?)", (idea_id,))
        con.execute("DELETE FROM revisions WHERE draft_id IN (SELECT id FROM drafts WHERE idea_id=?)", (idea_id,))
        con.execute("DELETE FROM drafts WHERE idea_id=?", (idea_id,))
        con.execute("DELETE FROM runs WHERE idea_id=?", (idea_id,))
        con.execute("DELETE FROM ideas WHERE id=?", (idea_id,))
    return {"ok": True}


@router.get("/ideas/{idea_id}")
def get_idea(idea_id: int, user=Depends(get_verified_user)):
    with db() as con:
        i = own_idea(con, idea_id, uid(user))
        runs = [r["id"] for r in con.execute("SELECT id FROM runs WHERE idea_id=? ORDER BY id DESC", (idea_id,))]
        latest = None
        if runs:
            ensure_worker(runs[0])
            latest = serialize_run(con, runs[0])
        return {"id": i["id"], "text": i["text"], "notes": i["notes"], "created_at": i["created_at"], "run_ids": runs, "latest_run": latest,
                **idea_v3(i)}


@router.post("/ideas/{idea_id}/run")
def run_idea(idea_id: int, body: RunIn = RunIn(), user=Depends(get_verified_user)):
    engine = body.engine if body.engine in ("v1", "v2", "v3") else ("v2" if body.v2 else "v1")
    v2 = engine == "v2"
    with db() as con:
        i = own_idea(con, idea_id, uid(user))
        prof = get_profile_row(con, uid(user))
        if _RUN_LOCK.locked():
            raise HTTPException(409, "Another draft run is in progress; wait for it to finish.")
        brief = prof["brief"] + learnings_block(con, uid(user))
        meta = None
        if engine == "v3":
            from datetime import datetime, timezone
            meta = json.dumps({"today": datetime.now(timezone.utc).date().isoformat(), **idea_v3(i)})
        cur = con.execute("INSERT INTO runs(idea_id,user_id,status,step,brief,personas,idea_text,notes,v2,engine,meta,created_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (idea_id, uid(user), "running", "Starting", brief, json.dumps(prof["personas"]), i["text"], i["notes"],
                           1 if v2 else 0, engine, meta, now()))
        run_id = cur.lastrowid
        con.execute("UPDATE ideas SET updated_at=? WHERE id=?", (now(), idea_id))
    if not start_worker(run_id):
        with db() as con:
            con.execute("DELETE FROM runs WHERE id=?", (run_id,))
        raise HTTPException(409, "Another draft run is in progress; wait for it to finish.")
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: int, user=Depends(get_verified_user)):
    with db() as con:
        own_run(con, run_id, uid(user))
    ensure_worker(run_id)
    with db() as con:
        return serialize_run(con, run_id)


@router.post("/runs/{run_id}/retry")
def retry_run(run_id: int, user=Depends(get_verified_user)):
    with db() as con:
        r = own_run(con, run_id, uid(user))
        if r["status"] not in ("failed", "cancelled"):
            raise HTTPException(400, "only failed or cancelled runs can be retried")
        if _RUN_LOCK.locked():
            raise HTTPException(409, "Another draft run is in progress; wait for it to finish.")
        con.execute("UPDATE runs SET status='running', error=NULL, finished_at=NULL, step='Resuming' WHERE id=?", (run_id,))
    if not start_worker(run_id):
        raise HTTPException(409, "Another draft run is in progress; wait for it to finish.")
    return {"ok": True}


@router.post("/runs/{run_id}/answers")
def answer_run(run_id: int, body: AnswersIn, user=Depends(get_verified_user)):
    """V3: the founder answers (or skips, with '') the planner's questions; the run then continues."""
    with db() as con:
        r = own_run(con, run_id, uid(user))
        if r["status"] != "needs_input":
            raise HTTPException(400, "this run is not waiting for answers")
        if _RUN_LOCK.locked():
            raise HTTPException(409, "Another draft run is in progress; wait for it to finish.")
        meta = v3_meta(r)
        qs = (meta.get("plan") or {}).get("questions") or []
        ans = [str(a or "").strip()[:800] for a in body.answers][:len(qs)]
        ans += [""] * (len(qs) - len(ans))
        meta["answers"] = [{"ask": q["ask"], "answer": a} for q, a in zip(qs, ans)]
        con.execute("UPDATE runs SET meta=?, status='running', step='Writing 3 drafts', progress=20 WHERE id=?", (json.dumps(meta), run_id))
    if not start_worker(run_id):
        with db() as con:
            con.execute("UPDATE runs SET status='needs_input', step='Waiting for your answers' WHERE id=?", (run_id,))
        raise HTTPException(409, "Another draft run is in progress; wait for it to finish.")
    return {"ok": True}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, user=Depends(get_verified_user)):
    with db() as con:
        r = own_run(con, run_id, uid(user))
        if r["status"] not in ("running", "needs_input"):
            raise HTTPException(400, "run is not running")
        con.execute("UPDATE runs SET status='cancelled', step='Cancelled', finished_at=? WHERE id=?", (now(), run_id))
    return {"ok": True}


def own_draft(con, draft_id, user_id):
    r = con.execute("SELECT * FROM drafts WHERE id=? AND user_id=?", (draft_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "draft not found")
    return r


@router.put("/drafts/{draft_id}/edit")
def edit_draft(draft_id: int, body: EditIn, user=Depends(get_verified_user)):
    text = body.text.strip()
    if not (1 <= len(text) <= 6000):
        raise HTTPException(400, "text must be 1-6000 characters")
    with db() as con:
        d = own_draft(con, draft_id, uid(user))
        _record_manual_edit(con, d, uid(user), text)
        con.execute("UPDATE drafts SET edited_text=? WHERE id=?", (text, draft_id))
    return {"ok": True}


@router.post("/drafts/{draft_id}/posted")
def mark_posted(draft_id: int, body: PostedIn, user=Depends(get_verified_user)):
    with db() as con:
        own_draft(con, draft_id, uid(user))
        con.execute("UPDATE drafts SET posted_at=? WHERE id=?", (now() if body.posted else None, draft_id))
    return {"ok": True}


# =========================================================================== weekly review (phase 2)
MAX_ACTIVE_LEARNINGS = 8


class ReviewIn(BaseModel):
    impressions: Optional[int] = None
    reactions: Optional[int] = None
    comments: Optional[int] = None
    reposts: Optional[int] = None
    profile_views: Optional[int] = None
    dms: Optional[int] = None
    signups: Optional[int] = None
    icp_note: Optional[str] = None


class AnalyzeIn(BaseModel):
    week: Optional[str] = None


class LearningIn(BaseModel):
    active: bool


def _week_start(ts):
    from datetime import datetime, timedelta, timezone
    d = datetime.fromtimestamp(ts, timezone.utc).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def _week_bounds(week):
    from datetime import datetime, timedelta, timezone
    try:
        d = datetime.strptime(week, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        d = datetime.fromtimestamp(now(), timezone.utc).date()
    d = d - timedelta(days=d.weekday())
    start = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
    return d.isoformat(), start, start + 7 * 86400, (d - timedelta(days=7)).isoformat(), (d + timedelta(days=7)).isoformat()


def engagement(rv):
    """Reactions + 3x comments + 4x reposts per 100 impressions (None when impressions are unknown)."""
    if not rv or not rv.get("impressions"):
        return None
    return ((rv.get("reactions") or 0) + 3 * (rv.get("comments") or 0) + 4 * (rv.get("reposts") or 0)) / rv["impressions"] * 100


def _ranks(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return None if den == 0 else round(num / den, 2)


def calibration(con, user_id):
    rows = con.execute("SELECT d.score, r.impressions, r.reactions, r.comments, r.reposts FROM reviews r "
                       "JOIN drafts d ON d.id=r.draft_id WHERE r.user_id=? AND d.score IS NOT NULL", (user_id,)).fetchall()
    pts = [(r["score"], engagement(dict(r))) for r in rows]
    pts = [(a, b) for a, b in pts if b is not None]
    n = len(pts)
    if n < 10:
        return {"n": n, "spearman": None,
                "message": f"Not enough data yet: the simulated score is compared with real results after 10 reviewed posts (you have {n})."}
    rho = spearman([a for a, _ in pts], [b for _, b in pts])
    return {"n": n, "spearman": rho,
            "message": ("Simulated score and real engagement move together." if rho is not None and rho >= 0.4 else
                        "Weak link between simulated score and real engagement so far; treat scores as a rough filter only.")}


REVIEW_FIELDS = ("impressions", "reactions", "comments", "reposts", "profile_views", "dms", "signups")


def _post_row(r, rv, idea=None):
    return {"draft_id": r["id"], "idea_id": r["idea_id"], "angle": r["angle"], "sim_score": r["score"], "posted_at": r["posted_at"],
            "text": r["edited_text"] or r["polished_text"] or r["text"], "lang": r["lang"], "variant_set": r["variant_set"],
            "audience": (idea["audience"] if idea else "") or "", "post_type": (idea["post_type"] if idea else "") or "",
            "review": ({k: rv.get(k) for k in REVIEW_FIELDS + ("icp_note",)} if rv else None)}


@router.put("/drafts/{draft_id}/review")
def put_review(draft_id: int, body: ReviewIn, user=Depends(get_verified_user)):
    for k in REVIEW_FIELDS:
        v = getattr(body, k)
        if v is not None and not (0 <= v <= 100_000_000):
            raise HTTPException(400, f"{k} must be between 0 and 100,000,000")
    note = (body.icp_note or "").strip()[:600]
    with db() as con:
        d = own_draft(con, draft_id, uid(user))
        if not d["posted_at"]:
            raise HTTPException(400, "mark the draft as posted before adding results")
        con.execute("INSERT INTO reviews(user_id,draft_id,impressions,reactions,comments,reposts,profile_views,dms,signups,icp_note,week_start,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(draft_id) DO UPDATE SET impressions=excluded.impressions, "
                    "reactions=excluded.reactions, comments=excluded.comments, reposts=excluded.reposts, "
                    "profile_views=excluded.profile_views, dms=excluded.dms, signups=excluded.signups, icp_note=excluded.icp_note",
                    (uid(user), draft_id, body.impressions, body.reactions, body.comments, body.reposts, body.profile_views,
                     body.dms, body.signups, note, _week_start(d["posted_at"]), now()))
    return {"ok": True}


@router.get("/reviews")
def get_reviews(week: Optional[str] = None, user=Depends(get_verified_user)):
    ws, lo, hi, prev, nxt = _week_bounds(week)
    with db() as con:
        drafts = con.execute("SELECT * FROM drafts WHERE user_id=? AND posted_at>=? AND posted_at<? ORDER BY posted_at",
                             (uid(user), lo, hi)).fetchall()
        posts = []
        for d in drafts:
            rv = con.execute("SELECT * FROM reviews WHERE draft_id=?", (d["id"],)).fetchone()
            idea = con.execute("SELECT audience, post_type FROM ideas WHERE id=?", (d["idea_id"],)).fetchone()
            posts.append(_post_row(d, dict(rv) if rv else None, idea))
        ins = con.execute("SELECT result, created_at FROM insights WHERE user_id=? AND week_start=? ORDER BY id DESC LIMIT 1",
                          (uid(user), ws)).fetchone()
        learn = [dict(r) for r in con.execute(
            "SELECT id, note, source_week, active, created_at, kind FROM learnings WHERE user_id=? ORDER BY id DESC LIMIT 40", (uid(user),))]
        return {"week_start": ws, "prev": prev, "next": nxt, "posts": posts,
                "insight": ({"created_at": ins["created_at"], **json.loads(ins["result"])} if ins else None),
                "learnings": learn, "calibration": calibration(con, uid(user)), "disclaimer": DISCLAIMER}


REVIEW_SYSTEM = (
    "You are a sharp, honest LinkedIn performance analyst for ONE founder. You get this week's posts with the simulated score "
    "(a heuristic) and the real numbers the founder entered. Compare posts against each other; do not invent data; with 1-3 posts say "
    "that the sample is tiny and keep conclusions modest. Each post names the audience it was written for and its post type: "
    "judge whether THAT audience engaged. DMs, signups and profile views matter far more than reactions; the founder's note "
    "about WHO engaged matters more than raw reach. Compare audiences and post types when there is more than one.\n"
    'Return ONLY JSON: {"summary":"2-3 sentences","worked":["max 3 items"],"didnt":["max 3 items"],'
    '"icp_signal":"one sentence on whether the right people engaged",'
    '"notes":["0-4 short imperative guidelines for FUTURE posts, max 22 words each, only if the data supports them"]}')


@router.post("/reviews/analyze")
def analyze_week(body: AnalyzeIn, user=Depends(get_verified_user)):
    ws, lo, hi, _, _ = _week_bounds(body.week)
    with db() as con:
        drafts = con.execute("SELECT * FROM drafts WHERE user_id=? AND posted_at>=? AND posted_at<? ORDER BY posted_at",
                             (uid(user), lo, hi)).fetchall()
        items = []
        for d in drafts:
            rv = con.execute("SELECT * FROM reviews WHERE draft_id=?", (d["id"],)).fetchone()
            if rv and any(rv[k] is not None for k in REVIEW_FIELDS):
                idea = con.execute("SELECT audience, post_type FROM ideas WHERE id=?", (d["idea_id"],)).fetchone()
                items.append((d, dict(rv), idea))
    if not items:
        raise HTTPException(400, "Add results for at least one posted draft this week first.")
    lines = []
    for n, (d, rv, idea) in enumerate(items, 1):
        eng = engagement(rv)
        aud = AUDIENCES.get((idea["audience"] if idea else "") or "", ("not set",))[0]
        typ = POST_TYPES.get((idea["post_type"] if idea else "") or "", ("not set",))[0]
        lines.append(f"POST {n} | audience: {aud} | post type: {typ} | language: {d['lang']} | angle: {d['angle']} | simulated score: {d['score']}\n"
                     f"numbers: impressions={rv['impressions']} reactions={rv['reactions']} comments={rv['comments']} "
                     f"reposts={rv['reposts']} profile_views={rv['profile_views']} dms={rv.get('dms')} signups={rv.get('signups')} "
                     f"engagement_per_100_impressions={None if eng is None else round(eng, 2)}\n"
                     f"founder's note on who engaged: {rv['icp_note'] or '(none)'}\n"
                     f"text:\n{(d['edited_text'] or d['polished_text'] or d['text'])[:1200]}")
    try:
        res = gw_complete(REVIEW_SYSTEM, "\n\n----\n\n".join(lines), max_tokens=1800, temperature=0.3, tag="review")
    except CapReached:
        raise HTTPException(429, "The daily LLM gateway cap was reached. Try again tomorrow.")
    except RuntimeError as e:
        raise HTTPException(502, f"Analysis failed: {e}")
    data = parse_json(res["text"])
    if not isinstance(data, dict) or not data.get("summary"):
        raise HTTPException(502, "The analysis came back unreadable; try again.")
    result = {"summary": str(data.get("summary"))[:700],
              "worked": [str(x)[:200] for x in (data.get("worked") or [])[:3]],
              "didnt": [str(x)[:200] for x in (data.get("didnt") or [])[:3]],
              "icp_signal": str(data.get("icp_signal") or "")[:300],
              "notes": [str(x).strip()[:200] for x in (data.get("notes") or [])[:4] if str(x).strip()],
              "posts_analyzed": len(items)}
    with db() as con:
        con.execute("DELETE FROM insights WHERE user_id=? AND week_start=?", (uid(user), ws))
        con.execute("INSERT INTO insights(user_id,week_start,result,created_at) VALUES(?,?,?,?)",
                    (uid(user), ws, json.dumps(result), now()))
        existing = {r["note"].strip().lower() for r in con.execute("SELECT note FROM learnings WHERE user_id=?", (uid(user),))}
        for note in result["notes"]:
            if note.lower() not in existing:
                con.execute("INSERT INTO learnings(user_id,note,source_week,active,created_at) VALUES(?,?,?,1,?)",
                            (uid(user), note, ws, now()))
        # at most MAX_ACTIVE_LEARNINGS stay active; newest win, older ones are switched off (not deleted)
        _trim_learnings(con, uid(user), "review")
    return {"insight": result, "cost_usd": round(res["cost"], 5)}


@router.put("/learnings/{learning_id}")
def toggle_learning(learning_id: int, body: LearningIn, user=Depends(get_verified_user)):
    with db() as con:
        r = con.execute("SELECT id, kind FROM learnings WHERE id=? AND user_id=?", (learning_id, uid(user))).fetchone()
        if not r:
            raise HTTPException(404, "learning not found")
        con.execute("UPDATE learnings SET active=? WHERE id=?", (1 if body.active else 0, learning_id))
        if body.active:
            _trim_learnings(con, uid(user), r["kind"])
    return {"ok": True}


@router.delete("/learnings/{learning_id}")
def delete_learning(learning_id: int, user=Depends(get_verified_user)):
    with db() as con:
        n = con.execute("DELETE FROM learnings WHERE id=? AND user_id=?", (learning_id, uid(user))).rowcount
    if not n:
        raise HTTPException(404, "learning not found")
    return {"ok": True}


# =========================================================================== polish correction (founder feedback loop)
# The founder tells the editor what is off ("too fake", "out of context", a fact that is missing). The server sends the CURRENT text
# (including the founder's own manual edits), a diff of what the founder changed since the AI's last version, and the instruction.
# Every correction and every manual edit is stored as a revision; a reusable "lesson" is distilled into the drafter's memory and
# the last few real before/after edits are shown to the writer in every later brief.
MAX_REVISIONS_PER_DRAFT = 40
MAX_ACTIVE_CORRECTIONS = 10
MANUAL_MERGE_WINDOW_S = 900


class RefineIn(BaseModel):
    instruction: str
    text: str


def _sentences(t):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", t or "") if s.strip()]


def diff_pairs(before, after, limit=6):
    """Sentence-level changes between two versions: [{'kind','before','after'}]."""
    import difflib
    a, b = _sentences(before), _sentences(after)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        out.append({"kind": {"replace": "changed", "delete": "removed", "insert": "added"}[op],
                    "before": " ".join(a[i1:i2]), "after": " ".join(b[j1:j2])})
        if len(out) >= limit:
            break
    return out


def diff_summary(before, after):
    pairs = diff_pairs(before, after)
    if not pairs:
        return "(none: the founder did not change your last version by hand)"
    lines = []
    for p in pairs:
        if p["kind"] == "changed":
            lines.append(f'- changed: "{p["before"][:260]}" -> "{p["after"][:260]}"')
        elif p["kind"] == "added":
            lines.append(f'- added: "{p["after"][:260]}"')
        else:
            lines.append(f'- removed: "{p["before"][:260]}"')
    return "\n".join(lines)


REFINE_SYSTEM = (
    "You are the founder's personal editor. You revise ONE LinkedIn post by following the founder's correction.\n"
    "Rules:\n"
    "1. Apply the founder's instruction faithfully. The instruction may be terse or shorthand (like a quick aside); read it generously.\n"
    "2. If the instruction states a fact about what really happened or what the context is, treat it as TRUE and weave it in naturally. "
    "Do not add any OTHER facts, numbers, customers, quotes or events beyond the post, the raw idea and the instruction.\n"
    "3. Keep every change the founder made by hand (listed under FOUNDER EDITS) exactly as they wrote it, unless the instruction says to change it.\n"
    "4. Fix what makes drafts feel fake: hype, dramatic rhetorical hooks, tidy 'insight' lines, clichés, over-polished phrasing, "
    "lists of three adjectives. Prefer plain first-person words, specific details from the raw idea, short sentences, a little roughness.\n"
    "5. Keep roughly the same length unless told otherwise. No hashtags or links unless the post already has them. Keep the post's "
    "language (English stays English; German stays German with informal du).\n"
    "6. The founder may point at lines as L1, L2, ... (see NUMBERED LINES; blank lines are not numbered). Change exactly the lines "
    "they name the way they ask, and leave the other lines as they are unless the instruction is about the whole post. "
    "Never put line numbers in your output.\n"
    'Return ONLY JSON: {"text":"the full revised post","lesson":"ONE general, reusable writing rule this correction teaches '
    '(imperative, max 20 words, e.g. \\"Never open with a rhetorical question.\\") or an empty string if the correction was only about this post\'s facts"}')


LINE_REF_RE = re.compile(r"^\s*L\d{1,3}\s*[:.|)\]-]\s?")


def numbered_lines(text):
    """L1, L2, ... over NON-EMPTY lines (the panel's gutter uses the same rule, so references match)."""
    out, n = [], 0
    for line in (text or "").split("\n"):
        if line.strip():
            n += 1
            out.append(f"L{n}: {line}")
    return "\n".join(out)


def strip_line_refs(text):
    """Safety net: if the editor echoed 'L3: ...' prefixes on every line, remove them."""
    lines = [l for l in text.split("\n") if l.strip()]
    if lines and all(LINE_REF_RE.match(l) for l in lines):
        return "\n".join(LINE_REF_RE.sub("", l) if l.strip() else l for l in text.split("\n"))
    return text


def correction_lessons_block(con, user_id):
    rows = con.execute("SELECT note FROM learnings WHERE user_id=? AND kind='correction' AND active=1 ORDER BY id DESC LIMIT ?",
                       (user_id, MAX_ACTIVE_CORRECTIONS)).fetchall()
    if not rows:
        return ""
    return ("\n\nMY WRITING CORRECTIONS (I fix these every time; apply them from the first draft)\n"
            + "\n".join("- " + r["note"] for r in reversed(rows)))


def edit_examples_block(con, user_id):
    """Real before/after edits by the founder, newest first, so the writer can match the direction."""
    rows = con.execute("SELECT kind, instruction, before_text, after_text FROM revisions WHERE user_id=? AND undone=0 "
                       "ORDER BY id DESC LIMIT 6", (user_id,)).fetchall()
    chunks = []
    for r in rows:
        pairs = [p for p in diff_pairs(r["before_text"], r["after_text"], 2) if p["kind"] == "changed"]
        if not pairs:
            continue
        head = f'- I told the editor: "{(r["instruction"] or "")[:160]}"' if r["kind"] == "refine" and r["instruction"] else "- I edited a draft by hand"
        for p in pairs[:1]:
            head += f'\n  was: "{p["before"][:200]}"\n  now: "{p["after"][:200]}"'
        chunks.append(head)
        if len(chunks) >= 3:
            break
    if not chunks:
        return ""
    return "\n\nHOW I REWRITE AI DRAFTS (my real edits; move in this direction)\n" + "\n".join(chunks)


def learnings_block(con, user_id):
    out = ""
    rows = con.execute("SELECT note FROM learnings WHERE user_id=? AND active=1 AND kind='review' ORDER BY id DESC LIMIT ?",
                       (user_id, MAX_ACTIVE_LEARNINGS)).fetchall()
    if rows:
        out += ("\n\nWHAT HAS WORKED / NOT WORKED FOR ME (from my own weekly performance reviews; follow these)\n"
                + "\n".join("- " + r["note"] for r in reversed(rows)))
    out += correction_lessons_block(con, user_id)
    out += edit_examples_block(con, user_id)
    return out[:3400]


def _trim_learnings(con, user_id, kind):
    limit = MAX_ACTIVE_CORRECTIONS if kind == "correction" else MAX_ACTIVE_LEARNINGS
    con.execute("UPDATE learnings SET active=0 WHERE user_id=? AND kind=? AND active=1 AND id NOT IN "
                "(SELECT id FROM learnings WHERE user_id=? AND kind=? AND active=1 ORDER BY id DESC LIMIT ?)",
                (user_id, kind, user_id, kind, limit))


def _ai_version(d):
    return d["ai_text"] or d["polished_text"] or d["text"]


def _record_manual_edit(con, d, user_id, new_text):
    """Remember hand edits (merged when made in quick succession) so the AI can see what the founder changed."""
    import difflib
    prev = (d["edited_text"] or _ai_version(d)).strip()
    if prev == new_text or difflib.SequenceMatcher(None, prev, new_text).ratio() >= 0.995:
        return
    last = con.execute("SELECT id, kind, created_at, undone FROM revisions WHERE draft_id=? ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
    if last and last["kind"] == "manual" and not last["undone"] and now() - last["created_at"] <= MANUAL_MERGE_WINDOW_S:
        con.execute("UPDATE revisions SET after_text=?, created_at=? WHERE id=?", (new_text, now(), last["id"]))
    else:
        con.execute("INSERT INTO revisions(draft_id,user_id,kind,instruction,before_text,after_text,created_at) VALUES(?,?,?,?,?,?,?)",
                    (d["id"], user_id, "manual", None, prev, new_text, now()))


@router.post("/drafts/{draft_id}/refine")
def refine_draft(draft_id: int, body: RefineIn, user=Depends(get_verified_user)):
    instruction = body.instruction.strip()
    text = body.text.strip()
    if not (3 <= len(instruction) <= 1500):
        raise HTTPException(400, "tell the AI what to fix (3-1500 characters)")
    if not (1 <= len(text) <= 6000):
        raise HTTPException(400, "the draft text must be 1-6000 characters")
    with db() as con:
        d = own_draft(con, draft_id, uid(user))
        n = con.execute("SELECT COUNT(*) c FROM revisions WHERE draft_id=?", (draft_id,)).fetchone()["c"]
        if n >= MAX_REVISIONS_PER_DRAFT:
            raise HTTPException(400, "this draft has reached its revision limit; start a new run instead")
        run = con.execute("SELECT idea_text, notes, engine FROM runs WHERE id=?", (d["run_id"],)).fetchone()
        prof = get_profile_row(con, uid(user))
        context = (prof["brief"] + learnings_block(con, uid(user)))[:3600]
        idea_text = run["idea_text"] if run else ""
        notes_block = notes_section(run["notes"]) if run else ""
        ai = _ai_version(d)
        # make sure the founder's unsaved hand edits are on record even if they never pressed "Save edit"
        if text != (d["edited_text"] or ai).strip():
            _record_manual_edit(con, d, uid(user), text)
    prompt = (f"FOUNDER CONTEXT (voice, audience, past corrections):\n{context}\n\n"
              f"RAW IDEA THE POST IS ABOUT:\n{idea_text}\n\n"
              f"{notes_block}"
              f"FOUNDER EDITS (made by hand since your last version; keep them):\n{diff_summary(ai, text)}\n\n"
              f"CURRENT POST (this is what the founder sees now, including their edits):\n{text}\n\n"
              f"NUMBERED LINES (same post; how the founder refers to lines):\n{numbered_lines(text)}\n\n"
              f"FOUNDER'S INSTRUCTION:\n{instruction}\n\nReturn the JSON now.")
    try:
        res = gw_complete(REFINE_SYSTEM, prompt, max_tokens=1800, temperature=0.5, tag="refine",
                          model=V3_MODEL if run and run["engine"] == "v3" else None)
    except CapReached:
        raise HTTPException(429, "The daily LLM gateway cap was reached. Try again tomorrow.")
    except RuntimeError as e:
        raise HTTPException(502, f"The editor could not be reached: {e}")
    data = parse_json(res["text"])
    if isinstance(data, dict) and data.get("text"):
        new_text, lesson = clean_post(strip_line_refs(str(data["text"]))), str(data.get("lesson") or "").strip().strip('"')[:220]
    else:  # model ignored the JSON format: use the raw reply as the post, no lesson
        new_text, lesson = clean_post(strip_line_refs(res["text"])), ""
    if len(new_text) < 100:
        raise HTTPException(502, "The editor returned an unusable reply; try again with a clearer instruction.")
    with db() as con:
        learning_id = None
        if 8 <= len(lesson) <= 220:
            have = {r["note"].strip().lower(): r["id"] for r in con.execute("SELECT id, note FROM learnings WHERE user_id=? AND kind='correction'", (uid(user),))}
            if lesson.lower() in have:
                learning_id = have[lesson.lower()]
                con.execute("UPDATE learnings SET active=1 WHERE id=?", (learning_id,))
            else:
                learning_id = con.execute("INSERT INTO learnings(user_id,note,source_week,active,created_at,kind) VALUES(?,?,?,1,?,'correction')",
                                          (uid(user), lesson, None, now())).lastrowid
            _trim_learnings(con, uid(user), "correction")
        else:
            lesson = ""
        rid = con.execute("INSERT INTO revisions(draft_id,user_id,kind,instruction,before_text,after_text,lesson,learning_id,created_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?)", (draft_id, uid(user), "refine", instruction, text, new_text, lesson or None, learning_id, now())).lastrowid
        con.execute("UPDATE drafts SET edited_text=?, ai_text=? WHERE id=?", (new_text, new_text, draft_id))
    return {"text": new_text, "lesson": lesson, "learning_id": learning_id, "revision_id": rid,
            "changed": new_text.strip() != text.strip(), "cost_usd": round(res["cost"], 5)}


@router.get("/drafts/{draft_id}/revisions")
def list_revisions(draft_id: int, user=Depends(get_verified_user)):
    with db() as con:
        own_draft(con, draft_id, uid(user))
        rows = con.execute("SELECT * FROM revisions WHERE draft_id=? ORDER BY id DESC LIMIT 20", (draft_id,)).fetchall()
        out = []
        for r in rows:
            out.append({"id": r["id"], "kind": r["kind"], "instruction": r["instruction"], "lesson": r["lesson"],
                        "learning_id": r["learning_id"], "undone": bool(r["undone"]), "created_at": r["created_at"],
                        "changes": diff_pairs(r["before_text"], r["after_text"], 3)})
        return {"revisions": out}


@router.post("/revisions/{revision_id}/undo")
def undo_revision(revision_id: int, user=Depends(get_verified_user)):
    with db() as con:
        r = con.execute("SELECT * FROM revisions WHERE id=? AND user_id=?", (revision_id, uid(user))).fetchone()
        if not r:
            raise HTTPException(404, "revision not found")
        if r["undone"]:
            raise HTTPException(400, "already undone")
        con.execute("UPDATE drafts SET edited_text=?, ai_text=? WHERE id=?",
                    (r["before_text"], r["before_text"] if r["kind"] == "refine" else None, r["draft_id"]))
        con.execute("UPDATE revisions SET undone=1 WHERE id=?", (revision_id,))
        if r["learning_id"]:  # undoing a correction switches its lesson off (kept, so it can be re-enabled in the Review tab)
            con.execute("UPDATE learnings SET active=0 WHERE id=? AND user_id=?", (r["learning_id"], uid(user)))
    return {"text": r["before_text"]}

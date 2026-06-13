#!/usr/bin/env python3
"""
freelance-finder — stdlib-only (no pip installs).
Pulls jobs/gigs from data-focused sources, scores each against the candidate's
CV (profile.md) with DeepSeek, and prints the TOP matches RANKED by fit.

Always returns a ranked list (not just pass/fail) so you always get the best
available options, even on a quiet day.

Sources (all free / no auth):
  - Remotive      (data category JSON API)
  - Jobicy        (data-science industry JSON API)
  - Arbeitnow     (Europe/Germany job board API — good for Berlin)
  - r/forhire     (Reddit RSS, [Hiring] freelance gigs — bonus)

Run by the OpenClaw agent via exec. Reads DEEPSEEK_API_KEY from env.
Profile (the CV) lives in profile.md next to this file — edit it to tune fit.
"""

import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

def _load_env_fallback():
    """If DEEPSEEK_API_KEY isn't in the environment, try nearby .env files.
    Keeps the script runnable both standalone and via a parent process."""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(here, "..", "..", ".env"),
              os.path.join(here, "..", "..", "..", ".env"),
              "/opt/hackamaps_bot/.env"):
        try:
            with open(c, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
            if os.environ.get("DEEPSEEK_API_KEY"):
                return
        except FileNotFoundError:
            continue


_load_env_fallback()
DEEPSEEK_KEY   = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
UA             = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
PER_SOURCE     = 10    # max relevant items kept per source
MAX_SCORE      = 16    # cap how many gigs we score (bounds runtime)
SHOW_TOP       = 8     # how many ranked matches to print
MIN_SHOW       = 4     # don't show gigs scoring below this (obvious non-fits)

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

# Data/automation focus — only score gigs that look relevant.
KEYWORDS = (
    "data", "sql", "power bi", "powerbi", "tableau", "dashboard", "analytic",
    "analyst", "analytics engineer", "reporting", "etl", "elt", "warehouse",
    "bigquery", "snowflake", "dbt", "looker", "python", "automat", "script",
    "powershell", "pipeline", "scada", "ignition", "reliability", "industrial",
    "energy", "grid", "bi ", "business intelligence", "engineer",
)


def load_profile():
    try:
        with open(os.path.join(SKILL_DIR, "profile.md"), encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            return t
    except FileNotFoundError:
        pass
    return "Data & automation engineer: Python, SQL, Power BI, Tableau, SCADA, web."


PROFILE = load_profile()

SYSTEM = (
    "You rank how well a job/gig fits a specific candidate's CV. Score 1-10. "
    "Weight fit to THEIR core strengths: data analysis, analytics/BI engineering, "
    "SQL, Power BI/Tableau dashboards, Python automation, SCADA/industrial "
    "automation, reporting. "
    "9-10: bullseye (data analyst / analytics engineer / BI / data engineer / "
    "automation roles matching their stack). "
    "7-8: strong adjacent fit (general data/python/eng role they could do). "
    "5-6: partial fit or a stretch. 1-4: wrong field (sales, design, copywriting, "
    "pure senior specialist they don't match). "
    "Remote or Berlin/Germany is a plus but do NOT punish full-time vs contract — "
    "rank on skills fit first. "
    'Respond ONLY with JSON: {"score": <1-10>, "reason": "<one line>", '
    '"pitch": "<2-3 sentence first-person pitch tailored to this role>"}'
)


def _get(url, timeout=15):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout
    ).read()


def relevant(text):
    t = text.lower()
    return any(k in t for k in KEYWORDS)


def _clean(htmltext):
    return html.unescape(re.sub(r"<[^>]+>", " ", htmltext or "")).strip()


def from_remotive():
    out = []
    try:
        jobs = json.loads(_get("https://remotive.com/api/remote-jobs?category=data&limit=40")).get("jobs", [])
        for j in jobs:
            title = f"{j.get('company_name','?')}: {j.get('title','')}"
            loc = j.get("candidate_required_location", "")
            body = f"{loc} | {j.get('job_type','')} | {j.get('salary','')}\n" + _clean(j.get("description", ""))
            if relevant(title + " " + body):
                out.append({"title": title, "url": j.get("url", ""), "body": body[:1100], "source": "Remotive", "loc": loc})
            if len(out) >= PER_SOURCE:
                break
    except Exception as e:
        print(f"  (Remotive failed: {e})")
    return out


def from_jobicy():
    out = []
    try:
        jobs = json.loads(_get("https://jobicy.com/api/v2/remote-jobs?count=40&industry=data-science")).get("jobs", [])
        for j in jobs:
            title = f"{j.get('companyName','?')}: {j.get('jobTitle','')}"
            loc = j.get("jobGeo", "")
            sal = f"${j.get('annualSalaryMin','')}-{j.get('annualSalaryMax','')}" if j.get("annualSalaryMin") else ""
            body = f"{loc} | {sal}\n" + _clean(j.get("jobExcerpt") or j.get("jobDescription", ""))
            if relevant(title + " " + body):
                out.append({"title": title, "url": j.get("url", ""), "body": body[:1100], "source": "Jobicy", "loc": loc})
            if len(out) >= PER_SOURCE:
                break
    except Exception as e:
        print(f"  (Jobicy failed: {e})")
    return out


def from_arbeitnow():
    out = []
    try:
        jobs = json.loads(_get("https://www.arbeitnow.com/api/job-board-api")).get("data", [])
        for j in jobs:
            title = f"{j.get('company_name','?')}: {j.get('title','')}"
            loc = j.get("location", "") + (" | remote" if j.get("remote") else "")
            tags = ", ".join(j.get("tags", [])[:6])
            body = f"{loc} | {tags}\n" + _clean(j.get("description", ""))
            if relevant(title + " " + tags + " " + body):
                out.append({"title": title, "url": j.get("url", ""), "body": body[:1100], "source": "Arbeitnow", "loc": loc})
            if len(out) >= PER_SOURCE:
                break
    except Exception as e:
        print(f"  (Arbeitnow failed: {e})")
    return out


def from_forhire():
    out = []
    try:
        xml = _get("https://www.reddit.com/r/forhire/new/.rss?limit=50")
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for e in ET.fromstring(xml).findall("a:entry", ns):
            title = (e.findtext("a:title", "", ns) or "").strip()
            if "[hiring]" not in title.lower():
                continue
            link = e.find("a:link", ns)
            body = _clean(e.findtext("a:content", "", ns))
            if relevant(title + " " + body):
                out.append({"title": title, "url": link.get("href", "") if link is not None else "",
                            "body": body[:1100], "source": "r/forhire", "loc": ""})
            if len(out) >= 5:
                break
    except Exception as e:
        print(f"  (r/forhire failed: {e})")
    return out


def score(gig):
    payload = {
        "model": DEEPSEEK_MODEL, "max_tokens": 400,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"MY CV:\n{PROFILE}\n\nJOB:\n{gig['title']}\n{gig['body']}"},
        ],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
    )
    for _ in range(2):
        try:
            content = json.loads(urllib.request.urlopen(req, timeout=60).read())["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception:
            continue
    return None


def main():
    if not DEEPSEEK_KEY:
        print("ERROR: DEEPSEEK_API_KEY not set.")
        sys.exit(1)

    raw = from_remotive() + from_jobicy() + from_arbeitnow() + from_forhire()
    gigs, seen = [], set()
    for g in raw:
        key = g["title"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            gigs.append(g)
    print(f"Pulled {len(gigs)} relevant jobs (Remotive, Jobicy, Arbeitnow, r/forhire).\n")

    scored = []
    for g in gigs[:MAX_SCORE]:
        r = score(g)
        if not r:
            continue
        try:
            s = int(r.get("score", 0))
        except (TypeError, ValueError):
            continue
        scored.append((s, g, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [x for x in scored if x[0] >= MIN_SHOW][:SHOW_TOP]

    if not ranked:
        print("No relevant jobs in this batch. Try again later or widen the profile.")
        return

    print(f"=== TOP {len(ranked)} MATCHES (ranked by fit) ===")
    for i, (s, g, r) in enumerate(ranked, 1):
        star = " 🥇" if s >= 8 else (" ✅" if s >= 6 else "")
        loc = f" | {g['loc']}" if g.get("loc") else ""
        print(f"\n#{i}  [{s}/10]{star} ({g['source']}{loc}) {g['title']}")
        print(g["url"])
        print(f"Why: {r.get('reason','')}")
        print(f"Pitch: {r.get('pitch','')}")


if __name__ == "__main__":
    main()

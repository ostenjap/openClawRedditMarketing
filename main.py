"""
Hackamaps Lead Bot
Monitors r/hackathon for posts from people looking for hackathons,
scores them with DeepSeek V4 Flash, and forwards high-quality leads to Telegram.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta

import praw
import requests
from openai import OpenAI, RateLimitError, APIError
from dotenv import load_dotenv

load_dotenv()

# --- Config ---
REDDIT_USER_AGENT    = os.environ.get("REDDIT_USER_AGENT", "hackamaps_bot/1.0 by wiecen")
DEEPSEEK_API_KEY     = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_MODEL       = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
TELEGRAM_TOKEN       = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]
SCORE_THRESHOLD      = int(os.environ.get("SCORE_THRESHOLD", 8))
POST_LIMIT           = int(os.environ.get("POST_LIMIT", 15))
LOOKBACK_HOURS       = int(os.environ.get("LOOKBACK_HOURS", 24))

DB_PATH = "seen_posts.db"

SYSTEM_PROMPT = """You are a lead scorer for hackamaps.com — a free, worldwide directory of hackathons shown on an interactive map. People use it to discover in-person and online hackathons globally.

Analyse the Reddit post below and decide: does this person have a problem or question that hackamaps.com directly solves?

High-scoring posts (8–10) look like:
- Someone asking where to find hackathons (local, online, global)
- Someone saying they want to participate in a hackathon but don't know how to find one
- A newcomer asking how the hackathon scene works and where events are listed
- Someone mentioning they missed a hackathon and wish they'd known about it earlier

Low-scoring posts (1–4) look like:
- Organisers promoting their own specific event
- General discussion about hackathon experiences, winners, teams
- Sponsorship or job posts
- Posts not about finding hackathons at all

Respond ONLY with valid JSON — no markdown, no explanation, no extra text:
{
  "score": <integer 1-10>,
  "reason": "<one sentence explaining the score>",
  "draft_reply": "<a genuinely helpful, conversational Reddit reply that mentions hackamaps.com naturally without any query parameters/arguments — not spammy, under 80 words>"
}"""


# --- Database ---

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            post_id TEXT PRIMARY KEY,
            processed_at TEXT,
            score INTEGER,
            draft_reply TEXT,
            post_url TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    # Migration: check if columns exist, if not add them
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(seen)")
    columns = [col[1] for col in cursor.fetchall()]
    if "score" not in columns:
        conn.execute("ALTER TABLE seen ADD COLUMN score INTEGER")
    if "draft_reply" not in columns:
        conn.execute("ALTER TABLE seen ADD COLUMN draft_reply TEXT")
    if "post_url" not in columns:
        conn.execute("ALTER TABLE seen ADD COLUMN post_url TEXT")
    if "status" not in columns:
        conn.execute("ALTER TABLE seen ADD COLUMN status TEXT DEFAULT 'pending'")
    conn.commit()
    return conn


def is_seen(conn, post_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen WHERE post_id = ?", (post_id,)).fetchone()
    return row is not None


def mark_seen(conn, post_id: str, score: int = 0, draft_reply: str = "", post_url: str = "", status: str = "pending"):
    conn.execute(
        """INSERT OR REPLACE INTO seen (post_id, processed_at, score, draft_reply, post_url, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (post_id, datetime.now(timezone.utc).isoformat(), score, draft_reply, post_url, status),
    )
    conn.commit()


import html
import urllib.parse
import xml.etree.ElementTree as ET
import re

# --- Reddit ---

SUBREDDITS = [
    "hackathon", "webdev", "learnprogramming", "cscareerquestions",
    "SideProject", "EntrepreneurRideAlong", "csMajors"
]

KEYWORDS = [
    "looking for a hackathon",
    "find hackathons",
    "hackathon team",
    "upcoming hackathons"
]

def fetch_rss_feed(session, url: str, source_label: str, cutoff) -> list[dict]:
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = session.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            print(f"  [error] RSS fetch failed for {source_label} (Status: {r.status_code})")
            return []
            
        root = ET.fromstring(r.text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        for entry in root.findall('atom:entry', ns):
            post_id_elem = entry.find('atom:id', ns)
            if post_id_elem is None or not post_id_elem.text:
                continue
            post_id = post_id_elem.text.split('_')[-1]
            
            published_elem = entry.find('atom:published', ns)
            if published_elem is None or not published_elem.text:
                continue
            
            # Parse the Atom time format (e.g. 2026-06-06T10:36:08+00:00)
            # Python's fromisoformat handles +00:00
            created = datetime.fromisoformat(published_elem.text)
            if created < cutoff:
                continue
                
            title_elem = entry.find('atom:title', ns)
            title = title_elem.text if title_elem is not None else ""
            
            author_elem = entry.find('atom:author', ns)
            author = "[deleted]"
            if author_elem is not None:
                name_elem = author_elem.find('atom:name', ns)
                if name_elem is not None and name_elem.text:
                    author = name_elem.text.replace('/u/', '')
            
            link_elem = entry.find('atom:link', ns)
            url_path = link_elem.get('href') if link_elem is not None else ""
            
            content_elem = entry.find('atom:content', ns)
            body_html = content_elem.text if content_elem is not None else ""
            
            # Strip HTML tags and decode HTML entities
            body_clean = re.sub(r'<[^>]+>', '', body_html) if body_html else ""
            body_clean = html.unescape(body_clean).strip()
            
            # Remove trailing "[link] [comments]" metadata from the RSS body text
            body_clean = re.sub(r'\s*\[link\]\s*\[comments\]\s*$', '', body_clean, flags=re.IGNORECASE)
            
            results.append({
                "id": post_id,
                "title": title,
                "body": body_clean[:1500],
                "url": url_path,
                "author": author,
                "created": created.isoformat(),
                "source": source_label
            })
            
    except Exception as e:
        print(f"  [error] RSS parser error for {source_label}: {e}")
        
    return results

def fetch_new_posts() -> list[dict]:
    """Fetch new posts from subreddits and keyword searches via public RSS feeds."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    session = requests.Session()
    
    posts = []
    seen_ids = set()

    # Fetch from subreddits
    for sub in SUBREDDITS:
        print(f"  Fetching r/{sub} RSS...")
        url = f"https://www.reddit.com/r/{sub}/new/.rss"
        results = fetch_rss_feed(session, url, f"r/{sub}", cutoff)
        for post in results:
            if post["id"] not in seen_ids:
                seen_ids.add(post["id"])
                posts.append(post)

    # Fetch from keyword searches
    for kw in KEYWORDS:
        print(f"  Searching '{kw}' RSS...")
        encoded_kw = urllib.parse.quote(kw)
        url = f"https://www.reddit.com/search.rss?q={encoded_kw}&sort=new"
        results = fetch_rss_feed(session, url, f"search: {kw}", cutoff)
        for post in results:
            if post["id"] not in seen_ids:
                seen_ids.add(post["id"])
                posts.append(post)

    return posts


# --- DeepSeek ---

def score_post(client: OpenAI, post: dict) -> dict | None:
    user_content = f"Title: {post['title']}\n\nBody: {post['body'] or '(no body text)'}"
    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            max_tokens=512,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [warn] JSON parse failed for {post['id']}: {e}")
        return None
    except RateLimitError:
        print("  [warn] DeepSeek rate limit hit — sleeping 60s")
        time.sleep(60)
        return None
    except APIError as e:
        print(f"  [error] DeepSeek API error: {e}")
        return None


# --- Telegram ---

def send_telegram(token: str, chat_id: str, text: str, reply_markup: dict | None = None) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, data=payload, timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"  [error] Telegram send failed: {e}")
        return False


def format_telegram_message(post: dict, result: dict) -> str:
    score = result["score"]
    reason = result.get("reason", "")
    draft = result.get("draft_reply", "")
    
    # Inject UTM parameters dynamically into hackamaps.com link in the draft
    utm_url = f"hackamaps.com?utm_source=reddit&utm_campaign=scout&utm_content={post['id']}"
    draft = draft.replace("hackamaps.com", utm_url)
    
    return (
        f"🎯 <b>New Lead — Score {score}/10</b>\n\n"
        f"<b>Post:</b> {post['title']}\n"
        f"<b>Source:</b> {post.get('source', 'Unknown')}\n"
        f"<b>Author:</b> u/{post['author']}\n"
        f"<b>URL:</b> {post['url']}\n\n"
        f"<b>Why:</b> {reason}\n\n"
        f"<b>Draft reply:</b>\n{draft}"
    )


# --- Main ---

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Hackamaps bot starting...")

    conn = init_db()

    ai = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    posts = fetch_new_posts()
    print(f"  Fetched {len(posts)} posts from Reddit")

    leads_sent = 0
    for post in posts:
        if is_seen(conn, post["id"]):
            print(f"  Skip (seen): {post['id']}")
            continue

        print(f"  Scoring: [{post['id']}] {post['title'][:60]}...")
        result = score_post(ai, post)

        if result is None:
            # Still mark as seen/skipped to avoid repeatedly retrying broken API hits
            mark_seen(conn, post["id"], score=0, draft_reply="", post_url=post["url"], status="error")
            continue

        score = result.get("score", 0)
        draft = result.get("draft_reply", "")
        print(f"    → Score {score}/10 | {result.get('reason', '')}")

        if score >= SCORE_THRESHOLD:
            # Store lead in DB as pending approval
            mark_seen(conn, post["id"], score, draft, post["url"], "pending")
            msg = format_telegram_message(post, result)
            ok = send_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, msg)
            if ok:
                leads_sent += 1
                print(f"    ✓ Sent to Telegram")
        else:
            # Store lead in DB as skipped (low score)
            mark_seen(conn, post["id"], score, draft, post["url"], "skipped")

    print(f"  Done. {leads_sent} lead(s) sent to Telegram.\n")
    conn.close()


if __name__ == "__main__":
    main()

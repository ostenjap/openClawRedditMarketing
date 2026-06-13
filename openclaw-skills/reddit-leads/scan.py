#!/usr/bin/env python3
"""
reddit-leads scan — stdlib-only (no pip installs needed).
Fetches recent r/hackathon posts via RSS, scores each for hackamaps.com
lead intent with DeepSeek, and prints the strong leads.

Run by the OpenClaw agent via the exec tool. Reads DEEPSEEK_API_KEY from env.
"""

import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEEPSEEK_KEY   = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
SUBREDDIT      = "hackathon"
MAX_POSTS      = 8       # keep snappy for a chat interaction
THRESHOLD      = 7
UA             = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

SYSTEM = (
    "You score Reddit posts as leads for hackamaps.com, a worldwide directory of "
    "hackathons on an interactive map. High score (8-10): someone asking where to "
    "find hackathons / how to discover events. Low score (1-4): organisers promoting "
    "one event, general chat, team-finding for a known event. Respond ONLY with JSON: "
    '{"score": <1-10>, "draft_reply": "<helpful reply mentioning hackamaps.com, <80 words>"}'
)


def fetch_posts():
    url = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss?limit={MAX_POSTS}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    xml = urllib.request.urlopen(req, timeout=10).read()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml)
    posts = []
    for e in root.findall("a:entry", ns):
        title = (e.findtext("a:title", "", ns) or "").strip()
        link_el = e.find("a:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        content = e.findtext("a:content", "", ns) or ""
        body = html.unescape(re.sub(r"<[^>]+>", " ", content))
        body = re.sub(r"\s*\[link\]\s*\[comments\]\s*$", "", body).strip()
        posts.append({"title": title, "url": link, "body": body[:1200]})
    return posts


def score(post):
    payload = {
        "model": DEEPSEEK_MODEL,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Title: {post['title']}\n\nBody: {post['body'] or '(none)'}"},
        ],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=60).read()
    content = json.loads(resp)["choices"][0]["message"]["content"]
    return json.loads(content)


def main():
    if not DEEPSEEK_KEY:
        print("ERROR: DEEPSEEK_API_KEY not set in environment.")
        sys.exit(1)

    try:
        posts = fetch_posts()
    except Exception as e:
        print(f"ERROR fetching Reddit: {e}")
        sys.exit(1)

    print(f"Scanned r/{SUBREDDIT}: {len(posts)} recent posts.\n")
    leads = []
    for p in posts:
        try:
            r = score(p)
            s = int(r.get("score", 0))
        except Exception as e:
            print(f"  (skip '{p['title'][:40]}': {e})")
            continue
        if s >= THRESHOLD:
            leads.append((s, p, r))

    if not leads:
        print("No strong leads in the latest posts. (Try again later.)")
        return

    leads.sort(key=lambda x: x[0], reverse=True)
    print(f"=== {len(leads)} LEAD(S) ===")
    for s, p, r in leads:
        print(f"\n[{s}/10] {p['title']}\n{p['url']}\nDraft: {r.get('draft_reply','')}")


if __name__ == "__main__":
    main()

"""
Reply Radar — find tweets worth replying to, draft on-voice replies.
Part of the OpenClaw skill suite. Personal-brand growth engine.

Flow:  search X (scraper) -> DeepSeek scores reply-worthiness + drafts reply
       -> top N sent to Telegram -> you approve, post the reply manually.

Reply game = ~80% of 0->1k follower growth. This kills the "what do I even
reply to" friction by surfacing the best targets + a ready draft each run.

DATA SOURCE: free scraper (snscrape). Fragile by nature — if it returns
nothing, the skill reports honestly instead of pretending. You can also pipe
tweets in manually via reply_radar.py --manual "tweet text | author | url".

NOTE: scraping X is against X ToS and may get the server IP rate-limited.
Replies are drafted only — never auto-posted. Human stays in the loop.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_MODEL   = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Niche search queries — tweets in these spaces where a sharp reply gets seen.
QUERIES = [
    "looking for a hackathon",
    "building an AI agent",
    "indie hacker shipping",
    "e/acc",
]

MAX_PER_QUERY = 15      # tweets to pull per query
MIN_FAVES     = 5       # skip low-engagement tweets (no audience to reach)
SEND_TOP      = 5       # how many drafts to push to Telegram
REPLY_THRESHOLD = 7     # only surface tweets scoring >= this

ai = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

SCORER_PROMPT = """You help @MaxQBasedLord grow on X via a sharp reply game. He's a based indie builder: e/acc, engineering, build-in-public, stoic. Building hackamaps.com (hackathon directory) + autonomous AI agents.

Given a tweet, decide if it's worth him replying to. A GOOD target:
- Is in his niche (building, AI agents, indie hacking, hackathons, e/acc, tech optimism)
- Has an audience (engagement) so his reply gets seen
- Gives him a natural opening to add a genuinely sharp/helpful/funny take
- Is NOT a giveaway, engagement-bait, crypto shill, or pure politics

If it's a good target, draft a reply in his voice: punchy, high-signal, no
hashtags, max one emoji, adds real value or a sharp take. Under 240 chars.
Never salesy, never "great post!". Earn the attention.

Respond ONLY with valid JSON:
{
  "score": <1-10 reply-worthiness>,
  "reason": "<one sentence>",
  "draft_reply": "<the reply, or empty if score < 7>"
}"""


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=10)
    except Exception as e:
        print(f"[telegram error] {e}")


def fetch_tweets_snscrape(query: str) -> list[dict]:
    """Attempt to fetch tweets via snscrape CLI. Returns [] on any failure."""
    tweets = []
    try:
        cmd = [
            "snscrape", "--jsonl", "--max-results", str(MAX_PER_QUERY),
            "twitter-search", f"{query} min_faves:{MIN_FAVES} lang:en",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            print(f"[snscrape fail] '{query}': {proc.stderr[-200:].strip()}")
            return []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            tweets.append({
                "text": t.get("rawContent") or t.get("content", ""),
                "author": (t.get("user") or {}).get("username", "?"),
                "url": t.get("url", ""),
                "faves": t.get("likeCount", 0),
            })
    except FileNotFoundError:
        print("[snscrape not installed]")
    except Exception as e:
        print(f"[snscrape error] '{query}': {e}")
    return tweets


def score_tweet(tw: dict) -> dict | None:
    try:
        resp = ai.chat.completions.create(
            model=DEEPSEEK_MODEL, max_tokens=400,
            messages=[
                {"role": "system", "content": SCORER_PROMPT},
                {"role": "user", "content": f"Tweet by @{tw['author']} ({tw['faves']} likes):\n{tw['text']}"},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"[score error] {e}")
        return None


def process(tweets: list[dict]):
    scored = []
    for tw in tweets:
        if not tw.get("text"):
            continue
        result = score_tweet(tw)
        if result and result.get("score", 0) >= REPLY_THRESHOLD:
            scored.append((result["score"], tw, result))
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        send_telegram("📡 <b>Reply Radar:</b> no strong reply targets this run.")
        return

    blocks = ["🎯 <b>Reply targets</b> — approve & post the reply manually:\n"]
    for score, tw, result in scored[:SEND_TOP]:
        blocks.append(
            f"\n<b>Score {score}/10</b> · @{tw['author']} · {tw['faves']}❤️\n"
            f"<b>Tweet:</b> {tw['text'][:200]}\n"
            f"<b>Link:</b> {tw['url']}\n"
            f"<b>Your reply:</b>\n<pre>{result.get('draft_reply','')}</pre>"
        )
    send_telegram("\n".join(blocks))
    print(f"  Sent {min(len(scored), SEND_TOP)} reply targets.")


def main():
    # Manual mode: reply_radar.py --manual "text | author | url"
    if len(sys.argv) > 2 and sys.argv[1] == "--manual":
        raw = sys.argv[2]
        parts = [p.strip() for p in raw.split("|")]
        tw = {"text": parts[0],
              "author": parts[1] if len(parts) > 1 else "?",
              "url": parts[2] if len(parts) > 2 else "",
              "faves": 0}
        process([tw])
        return

    print("Reply Radar scanning...")
    all_tweets, seen = [], set()
    for q in QUERIES:
        print(f"  searching: {q}")
        for tw in fetch_tweets_snscrape(q):
            if tw["url"] and tw["url"] not in seen:
                seen.add(tw["url"])
                all_tweets.append(tw)

    print(f"  fetched {len(all_tweets)} unique tweets")
    if not all_tweets:
        send_telegram(
            "⚠️ <b>Reply Radar:</b> scraper returned 0 tweets (X likely blocked "
            "the server IP). Use manual mode: paste a tweet and I'll draft a reply."
        )
        return
    process(all_tweets)


if __name__ == "__main__":
    main()

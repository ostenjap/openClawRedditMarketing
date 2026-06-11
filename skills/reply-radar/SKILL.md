# Skill: reply-radar

Drafts sharp, on-voice replies to tweets — the engine behind the reply game
(~80% of 0→1k follower growth). Drafts only; you approve and post manually.

## Reality check on data sourcing
Auto-searching X for targets requires the X API Basic tier ($200/mo). Free
scrapers (snscrape/nitter) are **dead** in 2026 — snscrape doesn't run on
Python 3.14 and X has locked scraper search. So this skill runs in **manual
mode**, which is reliable and ToS-safe:

**You're scrolling X anyway → paste any tweet to your Telegram bot → it drafts
a reply in your voice.** Zero friction, no monthly cost, no ban risk.

## How to use
Just talk to the Telegram bot:
- Paste a tweet's text, or
- Say "reply to this: <tweet>", or share an x.com link with the text

The bot extracts it, scores reply-worthiness, and drafts a sharp reply.

CLI (what the bot calls under the hood):
```bash
./venv/bin/python skills/reply-radar/reply_radar.py --manual "tweet text | author | url"
```

Auto-scan mode (`reply_radar.py` with no args) is wired for snscrape but will
report "0 tweets" until a working data source (X API Basic) is plugged in.

## Upgrade path
When the account is big enough to justify $200/mo, add `X_BEARER_TOKEN` to `.env`
and swap `fetch_tweets_snscrape()` for an X API v2 recent-search call. The
scoring + drafting half already works unchanged.

## Files
- `reply_radar.py` — scorer + drafter (manual mode + auto-scan scaffold)
- `SKILL.md` — this file

## Config (from project .env)
`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`

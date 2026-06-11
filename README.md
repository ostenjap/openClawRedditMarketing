# 🗺️ Hackamaps Lead Bot

An autonomous traffic-generation agent that monitors Reddit for people looking
for hackathons, scores each post with an LLM, and forwards high-quality leads —
with a ready-to-send draft reply — straight to your Telegram.

Built to drive organic discovery for [hackamaps.com](https://hackamaps.com), a
worldwide directory of hackathons on an interactive map. The code is generic
enough to repurpose for **any** product with a clear intent signal on Reddit.

---

## How it works

```
Reddit RSS  ──►  LLM scorer  ──►  Telegram
(sensor)         (brain)          (actuator)
```

1. **Sensor** — pulls new posts from multiple subreddits + keyword searches via
   Reddit's public RSS feeds (no API key, no auth, datacenter-IP friendly).
2. **Brain** — sends each post to an LLM (DeepSeek V4 Flash by default) with a
   strict prompt that returns JSON: a `score` (1-10) and a natural `draft_reply`.
3. **Actuator** — any post scoring above your threshold is pushed to Telegram
   with the draft reply, ready for one-tap human approval.

A local SQLite DB (`seen_posts.db`) dedupes posts so nothing is processed twice.

## Features

- 📡 Multi-subreddit + keyword-search monitoring via RSS (no Reddit API key)
- 🤖 LLM intent scoring with structured JSON output
- 📱 Telegram alerts with copy-paste-ready draft replies
- 💬 Telegram command bot (`/run`, `/status`, `/logs`) — trigger runs from your phone
- 🗃️ SQLite dedupe so each post is scored once
- ⏰ Runs on a simple cron schedule + systemd for the listener

## Quick start

```bash
git clone https://github.com/YOUR_USERNAME/hackamaps-bot.git
cd hackamaps-bot

python3 -m venv venv
source venv/bin/activate           # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in your keys
python main.py
```

## Configuration

All config lives in `.env` (see `.env.example`):

| Variable | Description |
|---|---|
| `DEEPSEEK_API_KEY` | Your DeepSeek API key |
| `DEEPSEEK_MODEL` | Model name (default `deepseek-v4-flash`) |
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat id from @userinfobot |
| `SCORE_THRESHOLD` | Min score (1-10) for a post to alert |
| `POST_LIMIT` | Posts pulled per feed |
| `LOOKBACK_HOURS` | Ignore posts older than this |

Edit the `SUBREDDITS` and `KEYWORDS` lists at the top of `main.py` to retarget
the bot at your own niche.

## Deployment

**Cron** (the scraper, every 2 hours):

```cron
0 */2 * * * cd /opt/hackamaps_bot && ./venv/bin/python main.py >> bot.log 2>&1
```

**systemd** (the Telegram command listener — runs 24/7, auto-restarts):

```ini
[Unit]
Description=Hackamaps Telegram Command Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/hackamaps_bot
ExecStart=/opt/hackamaps_bot/venv/bin/python /opt/hackamaps_bot/telegram_listener.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Swapping the LLM

The brain uses the OpenAI-compatible SDK, so any compatible endpoint works —
point `base_url` and the API key at OpenAI, DeepSeek, Gemini (via compat layer),
or a local model. Only `score_post()` in `main.py` needs touching.

## Responsible use

This tool is for **human-in-the-loop** lead discovery. It drafts replies; it does
not auto-post. Always read the post, respect each subreddit's self-promotion
rules, and only reply where you're genuinely helpful. Spamming will get you
banned and hurt your product.

## License

MIT — see [LICENSE](LICENSE).

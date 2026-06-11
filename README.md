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

## 🤖 OpenClaw Integration

This bot can be integrated directly into your **OpenClaw** multi-agent setup either as a dedicated agent or as a skill (radar tool) for an existing agent.

### Option A: Standalone OpenClaw Agent
To spawn a new OpenClaw agent dedicated to Reddit marketing:

1. **Create the Telegram Bot**: Use [@BotFather](https://t.me/BotFather) to create a new bot and copy the bot token.
2. **Update `openclaw.json`**:
   Add the agent configuration, binding, and Telegram credentials to your master `openclaw.json` file:
   ```json
   // 1. Under agents.list:
   {
     "id": "reddit_marketer",
     "name": "RedditMarketer",
     "workspace": "/home/node/.openclaw/workspace-reddit_marketer",
     "model": { "primary": "google/gemini-2.5-flash" }
   }

   // 2. Under bindings:
   { 
     "agentId": "reddit_marketer", 
     "match": { "channel": "telegram", "accountId": "reddit_marketer" } 
   }

   // 3. Under channels.telegram.accounts:
   "reddit_marketer": {
     "botToken": "YOUR_TELEGRAM_BOT_TOKEN_HERE",
     "dmPolicy": "pairing"
   }
   ```
3. **Deploy Workspace Files**:
   Create the workspace directory on the VPS and sync `main.py`, `telegram_listener.py`, and `requirements.txt`:
   ```bash
   mkdir -p /home/node/.openclaw/workspace-reddit_marketer/skills
   ```
4. **Deploy Agent Persona (`SOUL.md`)**:
   Create a `SOUL.md` in `/home/node/.openclaw/workspace-reddit_marketer/` explaining the agent's persona, deepseek scoring, and goals.
5. **Fix Permissions & Restart**:
   ```bash
   chown -R 1000:1000 /home/node/.openclaw/workspace-reddit_marketer/
   docker compose restart openclaw-gateway
   ```
6. **Pair Bot**: Message the bot on Telegram and approve the pairing code.

---

### Option B: Integrate as a Skill for an Existing Agent
To add this capability to an existing agent (e.g. `Axel` or `Planck`):

1. **Create Skill Folder**: Inside the agent's workspace directory, create:
   `skills/reddit-marketing/`
2. **Deploy Code**: Save the scanning script (`main.py`) in the agent's workspace.
3. **Write `SKILL.md`**: Create `skills/reddit-marketing/SKILL.md` containing instructions for the agent on when to run the script and how to process the output.

---

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

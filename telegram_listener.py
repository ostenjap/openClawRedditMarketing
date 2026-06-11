"""
Hackamaps Telegram Command Bot
Listens for commands AND natural language, controls the lead bot +
personal-brand skill remotely. Free-text is routed through DeepSeek as an
intent layer so you can just talk to it normally.

Slash commands (fast path):
  /run /tweets /status /logs /help
Natural language (smart path):
  "run the bot" · "any leads?" · "draft me some tweets" · "how's it going?"
"""

import json
import os
import subprocess
import time
import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_MODEL   = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
BOT_DIR          = "/opt/hackamaps_bot"
LOG_PATH         = f"{BOT_DIR}/bot.log"

ai = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# Simple single-user conversation state (this bot only serves one chat).
STATE = {"mode": None}
SKIP_WORDS = {"skip", "no", "none", "nothing", "evergreen", "cancel", "-"}

ROUTER_PROMPT = """You are the brain of a Telegram control bot for "hackamaps" — a hackathon directory startup. The operator talks to you in plain language. Decide what they want.

Available actions:
- "run_lead_bot": scrape Reddit and score new leads (when they say things like "run the bot", "check reddit", "find leads", "go", "scan now")
- "draft_tweets": generate 3 tweet drafts for their personal brand (when they say "draft tweets", "give me tweets", "write me a post", "tweet ideas")
- "draft_reply": draft a reply to a specific tweet (when they PASTE a tweet, share an x.com/twitter link, or say "reply to this", "what should I say to this"). Put the tweet's text (and author/url if present) into tweet_text.
- "get_status": show the last lead-bot run summary (when they ask "any leads?", "how did it go?", "status", "what happened")
- "get_logs": show recent raw logs (when they ask for "logs", "errors", "what broke")
- "none": they're just chatting, asking a question, or want advice — answer them directly

Respond ONLY with valid JSON:
{
  "action": "<one of: run_lead_bot, draft_tweets, draft_reply, get_status, get_logs, none>",
  "tweet_text": "<only for draft_reply: the tweet text to reply to, formatted as 'text | author | url' if you can extract those, else just the text>",
  "reply": "<a short, friendly, on-brand message to send back. If action is none, this is your full answer. If an action runs, this is a one-line 'on it' style ack.>"
}

Keep replies concise and casual — you're a sharp assistant, not corporate."""


# The 3 persistent buttons shown at the bottom of the chat.
BTN_REDDIT = "🔍 Reddit Leads"
BTN_TWEETS = "🐦 Draft Tweets"
BTN_REPLY  = "💬 Reply to Tweet"

MENU_KEYBOARD = json.dumps({
    "keyboard": [[BTN_REDDIT, BTN_TWEETS], [BTN_REPLY]],
    "resize_keyboard": True,
    "is_persistent": True,
})


def send(text: str, keyboard: bool = False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        data["reply_markup"] = MENU_KEYBOARD
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"[send error] {e}")


def get_updates(offset):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 30, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(url, params=params, timeout=35)
        return r.json().get("result", [])
    except Exception as e:
        print(f"[poll error] {e}")
        return []


# --- Actions ---

def run_lead_bot():
    """Fire-and-forget: launch the scan in the background so the bot stays
    responsive. main.py pings Telegram with a summary + any leads when done."""
    subprocess.Popen(
        [f"{BOT_DIR}/venv/bin/python", f"{BOT_DIR}/main.py"],
        cwd=BOT_DIR,
        stdout=open(f"{BOT_DIR}/bot.log", "a"),
        stderr=subprocess.STDOUT,
    )


def start_tweet_flow():
    """Ask for today's focus before drafting."""
    STATE["mode"] = "await_tweet_ctx"
    send(
        "🐦 What should today's tweets focus on?\n"
        "Tell me what you shipped, learned, or thought today — "
        "or say <b>skip</b> for evergreen drafts."
    )


def draft_tweets(context: str = ""):
    cmd = [f"{BOT_DIR}/venv/bin/python",
           f"{BOT_DIR}/skills/personal-brand/tweet_drafter.py"]
    if context:
        cmd += ["--context", context]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=BOT_DIR, timeout=120)
    if result.returncode != 0:
        send(f"⚠️ Drafter error:\n<pre>{result.stderr[-1500:]}</pre>")


def get_status():
    try:
        with open(LOG_PATH) as f:
            last = "".join(f.readlines()[-15:]).strip() or "Empty log."
    except FileNotFoundError:
        last = "No log file yet — cron hasn't run."
    send(f"📊 <b>Last run:</b>\n\n<pre>{last}</pre>")


def get_logs():
    try:
        with open(LOG_PATH) as f:
            last = "".join(f.readlines()[-20:]).strip() or "Empty log."
    except FileNotFoundError:
        last = "No log file yet."
    send(f"📋 <b>Bot logs:</b>\n\n<pre>{last}</pre>")


def draft_reply(tweet_text: str):
    if not tweet_text.strip():
        send("Paste the tweet you want to reply to and I'll draft something sharp.")
        return
    subprocess.run(
        [f"{BOT_DIR}/venv/bin/python",
         f"{BOT_DIR}/skills/reply-radar/reply_radar.py", "--manual", tweet_text],
        cwd=BOT_DIR, timeout=120,
    )


ACTIONS = {
    "run_lead_bot": run_lead_bot,
    "draft_tweets": start_tweet_flow,   # asks for today's focus first
    "get_status": get_status,
    "get_logs": get_logs,
}


# --- Routing ---

def route_natural_language(text: str):
    """Send free text to DeepSeek, get intent + reply, execute."""
    try:
        resp = ai.chat.completions.create(
            model=DEEPSEEK_MODEL,
            max_tokens=400,
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        action = data.get("action", "none")
        reply = data.get("reply", "").strip()
        tweet_text = data.get("tweet_text", "").strip()
    except Exception as e:
        print(f"[router error] {e}")
        send("⚠️ My brain hiccuped — try a slash command (/help).")
        return

    if reply:
        send(reply)
    if action == "draft_reply":
        draft_reply(tweet_text)
    elif action in ACTIONS:
        ACTIONS[action]()


def handle(text: str):
    cmd = text.strip().split()[0].lower()

    # A slash command always cancels any pending conversational flow.
    if cmd.startswith("/"):
        STATE["mode"] = None

    # If we're waiting for today's tweet focus, capture this message as context.
    if STATE["mode"] == "await_tweet_ctx":
        STATE["mode"] = None
        ctx = text.strip()
        if ctx.lower() in SKIP_WORDS:
            send("👍 Going evergreen. Drafting...")
            draft_tweets("")
        else:
            send("✍️ Got it — weaving that in. Drafting...")
            draft_tweets(ctx)
        return

    # If we're waiting for a tweet to reply to, capture it.
    if STATE["mode"] == "await_reply_tweet":
        STATE["mode"] = None
        if text.strip().lower() in SKIP_WORDS:
            send("👍 Cancelled.")
            return
        send("✍️ Drafting a reply...")
        draft_reply(text.strip())
        return

    # --- The 3 menu buttons (Telegram sends the button label as text) ---
    label = text.strip()
    if label == BTN_REDDIT:
        send("⏳ Scanning Reddit (~10s) — I'll ping you with results.")
        run_lead_bot()
        return
    if label == BTN_TWEETS:
        start_tweet_flow()
        return
    if label == BTN_REPLY:
        STATE["mode"] = "await_reply_tweet"
        send("💬 Paste the tweet you want to reply to (text or x.com link).")
        return

    if cmd == "/run":
        send("⏳ Scanning Reddit (~10s) — I'll ping you with results.")
        run_lead_bot()
    elif cmd == "/tweets":
        STATE["mode"] = None  # reset any stale flow
        start_tweet_flow()
    elif cmd == "/menu":
        send("Here are your buttons 👇", keyboard=True)
    elif cmd == "/status":
        get_status()
    elif cmd == "/logs":
        get_logs()
    elif cmd == "/help":
        send(
            "🤖 <b>Hackamaps Bot</b>\n\n"
            "Tap a button below, talk to me normally, or use:\n"
            "/run — run the lead bot\n"
            "/tweets — draft 3 tweets\n"
            "/menu — show the buttons\n"
            "/status — last run summary\n"
            "/logs — recent logs",
            keyboard=True,
        )
    elif cmd.startswith("/"):
        send("❓ Unknown command. Type /help — or just talk to me normally.")
    else:
        # Natural language — route through the LLM brain
        route_natural_language(text)


def main():
    print("Telegram listener starting...")
    send("🟢 <b>Hackamaps bot online.</b> Tap a button or just talk to me.", keyboard=True)
    offset = None

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")

                if chat_id != str(TELEGRAM_CHAT_ID):
                    print(f"[ignored] message from unknown chat {chat_id}")
                    continue

                if text.strip():
                    print(f"[msg] {text}")
                    handle(text)

        except KeyboardInterrupt:
            send("🔴 Bot listener stopped.")
            break
        except Exception as e:
            print(f"[loop error] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()

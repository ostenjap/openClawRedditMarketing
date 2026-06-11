"""
Hackamaps Telegram Command Bot
Listens for commands and controls the lead bot remotely.

Commands:
  /run    ??? trigger the bot immediately
  /status ??? show last run summary
  /logs   ??? show last 20 lines of bot.log
  /help   ??? list commands
"""

import os
import subprocess
import time
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
BOT_DIR          = "/opt/hackamaps_bot"
LOG_PATH         = f"{BOT_DIR}/bot.log"


def send(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
    except Exception as e:
        print(f"[send error] {e}")


def get_updates(offset: int | None) -> list:
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


def handle(text: str):
    cmd = text.strip().split()[0].lower()

    if cmd == "/run":
        send("?????? Running the bot now...")
        result = subprocess.run(
            [f"{BOT_DIR}/venv/bin/python", f"{BOT_DIR}/main.py"],
            capture_output=True, text=True, cwd=BOT_DIR, timeout=120
        )
        output = (result.stdout or "No output").strip()
        # Trim to last 3000 chars to fit Telegram limit
        if len(output) > 3000:
            output = "..." + output[-3000:]
        send(f"??? <b>Run complete!</b>\n\n<pre>{output}</pre>")

    elif cmd == "/status":
        try:
            with open(LOG_PATH) as f:
                lines = f.readlines()
            last = "".join(lines[-15:]).strip() or "Empty log."
        except FileNotFoundError:
            last = "No log file yet ??? cron hasn't run."
        send(f"???? <b>Last run:</b>\n\n<pre>{last}</pre>")

    elif cmd == "/logs":
        try:
            with open(LOG_PATH) as f:
                lines = f.readlines()
            last = "".join(lines[-20:]).strip() or "Empty log."
        except FileNotFoundError:
            last = "No log file yet."
        send(f"???? <b>Bot logs:</b>\n\n<pre>{last}</pre>")

    elif cmd == "/help":
        send(
            "???? <b>Hackamaps Bot</b>\n\n"
            "/run ??? trigger the bot now\n"
            "/status ??? last run summary\n"
            "/logs ??? recent log output\n"
            "/help ??? this message"
        )

    else:
        send("??? Unknown command. Type /help for options.")


def main():
    print("Telegram listener starting...")
    send("???? <b>Hackamaps bot is online!</b> Type /help for commands.")
    offset = None

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")

                # Only respond to your chat
                if chat_id != str(TELEGRAM_CHAT_ID):
                    print(f"[ignored] message from unknown chat {chat_id}")
                    continue

                if text.startswith("/"):
                    print(f"[cmd] {text}")
                    handle(text)

        except KeyboardInterrupt:
            send("???? Bot listener stopped.")
            break
        except Exception as e:
            print(f"[loop error] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()


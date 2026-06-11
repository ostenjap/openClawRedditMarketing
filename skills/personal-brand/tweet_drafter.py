"""
Personal Brand — Daily Tweet Drafter
Generates 3 tweet drafts each morning in the user's voice and sends them
to Telegram for approval. Part of the OpenClaw skill suite.

Voice: @MaxQBasedLord — e/acc, engineering, build-in-public, stoicism.
Flow:  rotate pillars -> DeepSeek drafts -> Telegram (human approves -> posts manually)

Optional: drop fresh material in context.md (what you shipped/thought today)
and it gets woven into the build-in-public drafts for authenticity.
"""

import json
import os
import random
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

import requests
from openai import OpenAI, APIError
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_MODEL   = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SKILL_DIR    = Path(__file__).resolve().parent
CONTEXT_FILE = SKILL_DIR / "context.md"   # optional fresh material you append to

# --- Voice & strategy ---

PILLARS = {
    "build-in-public": "Real progress on hackamaps.com (a worldwide hackathon directory on a map) or the autonomous agents/bots you build. Concrete: a feature shipped, a bug beaten, a metric, a screenshot-worthy moment. Show the climb.",
    "e/acc": "Tech-optimism / effective accelerationism. Why building beats doom, why cheap compute + one person is leverage never seen before, accelerationist angle on AI agents. Tribal, energising, forward.",
    "engineering": "A concrete lesson from real work — scraping, LLM cost optimisation, cheap VPS deployment, agent architecture, the boring-tech-ships ethos. Teach one sharp thing.",
    "stoicism": "Discipline, amor fati, building through resistance, mortality as motivation, the work as its own reward. Quote-tweetable, grounded, not preachy.",
}

# The signature move: blended posts that hit 2+ pillars at once.
BLEND_HINT = (
    "At least ONE of the three drafts must BLEND two pillars (e.g. an engineering "
    "war story framed through stoicism, or a build-in-public win framed as e/acc). "
    "Blended posts are the account's signature — make it land."
)

SYSTEM_PROMPT = f"""You write tweets in the exact voice of @MaxQBasedLord.

WHO HE IS:
A based indie builder. Ships fast, thinks first-principles. Building hackamaps.com
(a global hackathon directory) and a fleet of autonomous AI agents. Worldview is
e/acc (tech-optimist accelerationist) with a stoic backbone. Engineer at heart.

VOICE RULES (non-negotiable):
- Punchy. Short sentences. High signal. No fluff, no throat-clearing.
- NO hashtags. NO emojis spam (one emoji max, only if it earns its place).
- NO corporate/LinkedIn tone. NO "thrilled to announce". Never salesy.
- Concrete > abstract. Real numbers, real specifics, real moments.
- Confident, a little contrarian, never cringe. Earned swagger, not bragging.
- Occasional lowercase for rhythm is fine. Write like a human who ships at 2am.
- Each tweet MUST be under 280 characters. Standalone — no thread numbering.

{BLEND_HINT}

OUTPUT FORMAT — respond ONLY with valid JSON, no markdown:
{{
  "drafts": [
    {{"pillar": "<pillar name(s)>", "text": "<the tweet>"}},
    {{"pillar": "...", "text": "..."}},
    {{"pillar": "...", "text": "..."}}
  ]
}}"""


def cli_context() -> str:
    """Inline context passed as: tweet_drafter.py --context "what I shipped today"."""
    if "--context" in sys.argv:
        i = sys.argv.index("--context")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip()
    return ""


def load_context() -> str:
    """Combine today's inline context (priority) with any standing context.md."""
    parts = []
    inline = cli_context()
    if inline:
        parts.append(inline)
    if CONTEXT_FILE.exists():
        txt = CONTEXT_FILE.read_text(encoding="utf-8").strip()
        if txt:
            parts.append(txt)
    return "\n".join(parts)


def build_user_prompt() -> str:
    # Pick 3 pillars for variety; weight build-in-public + e/acc (best for this niche)
    weighted = (
        ["build-in-public"] * 3 + ["e/acc"] * 3 +
        ["engineering"] * 2 + ["stoicism"] * 2
    )
    unique_pillars = sorted(set(weighted))
    chosen = random.sample(unique_pillars, 3) if len(unique_pillars) >= 3 else list(unique_pillars)
    # Ensure 3 by topping up
    while len(chosen) < 3:
        chosen.append(random.choice(list(PILLARS.keys())))

    lines = ["Draft 3 tweets, one for each of these pillars:\n"]
    for p in chosen:
        lines.append(f"- {p}: {PILLARS[p]}")

    ctx = load_context()
    if ctx:
        lines.append(
            "\nFRESH MATERIAL (what he actually shipped/thought recently — "
            "weave this into the build-in-public / engineering drafts for authenticity):\n"
            + ctx
        )
    else:
        lines.append(
            "\n(No fresh material today — draw on the evergreen reality: building "
            "hackamaps.com + autonomous Reddit/agent bots on a cheap VPS, DeepSeek "
            "for dirt-cheap LLM calls, human-in-the-loop automation.)"
        )
    return "\n".join(lines)


def generate_drafts() -> list[dict]:
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            max_tokens=900,
            temperature=0.9,  # higher = more voice/variety
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt()},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return data.get("drafts", [])
    except (APIError, json.JSONDecodeError) as e:
        print(f"[error] draft generation failed: {e}")
        return []


def tweet_intent_url(text: str) -> str:
    """X web-intent link — opens the composer pre-filled. One tap to post."""
    return "https://twitter.com/intent/tweet?text=" + urllib.parse.quote(text)


def send_telegram(text: str, button: dict | None = None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if button:
        data["reply_markup"] = json.dumps({"inline_keyboard": [[button]]})
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"[error] telegram send failed: {e}")


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Drafting daily tweets...")
    drafts = generate_drafts()
    if not drafts:
        send_telegram("⚠️ Tweet drafter ran but produced nothing. Check logs.")
        return

    send_telegram("🐦 <b>Today's tweet drafts</b>\nTap “Tweet this” to post — opens X pre-filled.")
    for i, d in enumerate(drafts, 1):
        pillar = d.get("pillar", "?")
        text = d.get("text", "").strip()
        chars = len(text)
        button = {"text": "🐦 Tweet this", "url": tweet_intent_url(text)}
        send_telegram(
            f"<b>{i}. [{pillar}]</b> <i>({chars} chars)</i>\n<pre>{text}</pre>",
            button=button,
        )
    print(f"  Sent {len(drafts)} drafts to Telegram.")


if __name__ == "__main__":
    main()

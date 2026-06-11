# Skill: personal-brand

Drafts daily tweets in the operator's voice (@MaxQBasedLord) and sends them to
Telegram for human approval. Goal: grow the personal account to 1,000 followers
via consistent, on-voice build-in-public content + a disciplined reply game.

## When to run
- Automatically every morning (~08:00 server time) via cron.
- On demand via the Telegram command `/tweets` (see telegram_listener).

## What it does
1. Rotates across 4 content pillars: build-in-public, e/acc, engineering, stoicism.
2. Optionally reads `context.md` for fresh material (what was shipped/thought that day).
3. Calls DeepSeek to draft 3 tweets — at least one BLENDS two pillars (the signature).
4. Sends the 3 numbered drafts to Telegram with character counts.
5. Operator picks one, edits if needed, posts manually. (Drafts only — never auto-posts.)

## Feeding it fresh material (optional but recommended)
Append a few bullet points to `context.md` whenever you ship or have a thought:
```
echo "- beat reddit 403s by switching to .rss feeds, lost 4h to it" >> context.md
```
Empty file = falls back to evergreen material. Clear it weekly.

## Files
- `tweet_drafter.py` — the engine
- `context.md` — optional daily material (gitignored; personal)
- `SKILL.md` — this file

## Config (from project .env)
`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`

## The 0 → 1,000 follower playbook (how this skill fits)
This skill solves **consistency** (the #1 reason accounts die). But drafting is
only half. The growth comes from:

1. **Post 1 build-in-public tweet daily** — this skill drafts it, you ship it.
2. **Reply game (the real engine):** 10–15 thoughtful replies/day to bigger
   accounts in your niche (e/acc, indie hackers, AI-agent builders). Be early,
   add a sharp take. ~80% of 0→1k growth is replies, not posts.
3. **One thread per week** — turn a real build into a "how I did X" thread.
   Threads are your reach spikes.
4. **Engage back** — reply to everyone who replies to you for the first 90 min
   after posting (the algo rewards early conversation density).

Cadence target: 1 post + 1 thread/week + 10 replies/day. At that rate, a
genuinely on-voice account in this niche realistically hits 1k in 8–12 weeks.

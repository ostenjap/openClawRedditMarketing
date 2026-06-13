---
name: reddit-leads
description: Find people on Reddit looking for hackathons and draft helpful replies that mention hackamaps.com. Use whenever the user asks to find leads, scan Reddit, check r/hackathon, or look for people who need hackamaps.
---

# reddit-leads

Scans recent r/hackathon posts, scores each for hackamaps.com lead intent with
DeepSeek, and returns the strong leads with a ready-to-send draft reply.

## When to use
Trigger this skill when the user says things like:
- "find me hackathon leads" / "scan Reddit" / "check r/hackathon"
- "any new leads?" / "who's looking for a hackathon?"

## How to run
Run the bundled stdlib-only Python script with the `exec` tool:

```bash
python3 ~/.openclaw/workspace/skills/reddit-leads/scan.py
```

It needs `DEEPSEEK_API_KEY` in the environment (already provided by the gateway).
No pip installs required — the script uses only the Python standard library.

## What to do with the output
The script prints each strong lead as:
`[score/10] <title>` + URL + a draft reply.

Relay the results to the user clearly: list each lead's score, title, link, and
the draft reply. If it prints "No strong leads", tell the user plainly and offer
to try again later. Never auto-post replies — the user approves and posts.

---
name: linkedin
description: "Samatha's LinkedIn workflow: research a topic, run the samatha pipeline (hooks → drafts → critics), pick/revise, log posts, leads and analytics, and report KPIs."
version: 1.0.0
author: Samatha setup 2026-10-10
platforms: [linux]
prerequisites:
  commands: [samatha]
metadata:
  hermes:
    tags: [linkedin, social-media, kpi, drafting]
---

# LinkedIn workflow (Samatha)

All heavy lifting is the `samatha` CLI (terminal tool). It is capped, logged and sends long results to Telegram by itself.
Never write LinkedIn posts yourself in chat. The pipeline does that, so voice, critics, KPIs and budget all apply.

## Map what the user says to a command

| User says | You run |
|---|---|
| a topic / idea / "write about X" / `/idea X` | optionally research (below), then `samatha idea "<topic>" --research /root/samatha/research/<slug>.json` |
| "1" / "2" / "3" / "take the second" | `samatha pick N` and send its output back verbatim |
| "edit 2: shorter, less salesy" | `samatha revise 2 "shorter, less salesy"` and send the output back |
| "posted <url>" (maybe with the final text) | save any pasted final text to `/root/samatha/inbox/final.txt`, then `samatha posted --url <url> --text-file /root/samatha/inbox/final.txt` (omit `--text-file` if no text was pasted) |
| "lead Anna from the UGC post" / "got a DM from …" | `samatha lead "<name>" [--post <url or post #>] [--note "..."]` |
| numbers for one post ("1.2k impressions, 14 comments …") | `samatha metrics <post url|#> --impressions N --reactions N --comments N --reposts N [--saves N]` |
| sends an .xlsx/.csv analytics export | `samatha import-analytics <file path>` then `samatha week` |
| sends a LinkedIn data-export zip / Shares.csv | `samatha import-posts <file path>` then `samatha tag-imports` |
| `/kpi`, "how are we doing" | `samatha kpi` |
| `/week`, "weekly report" | `samatha week` |
| "what should I post", `/plan` | `samatha plan` |
| `/voice` | `samatha voice` |
| "status of the draft" | `samatha show` |
| "recalibrate the rubric" | `samatha calibrate`. Only after the user explicitly says to apply it: `samatha calibrate --apply` |

Send CLI output back as-is (it is already formatted). Don't paraphrase scores.

## Research (optional, before `idea`)
Do it only if the topic needs fresh facts (news, stats, a tool release). Use web search, max ~5 tool calls. Write JSON to
`/root/samatha/research/<slug>.json`: `{"topic":..., "key_stats":[max 3, each with source URL], "sources":[urls], "contrarian_angle":...}`.
If the topic is about the user's own week or experience, skip research. Their own facts are the source.
For a deep dive, tell the user it would be a `scout` crew card on Plank's kanban instead.

## Rules
- `samatha idea` runs in the background (2–4 min) and the result arrives as its own Telegram message. Tell the user
  "drafting, ~3 min", then stop. Do not poll in a loop. If they ask, use `samatha show`.
- Budget or gateway failure: say so plainly and give the fallback (LinkedIn Drafter tab in Open WebUI). Never retry more than once.
- Never publish anything, never ask for LinkedIn credentials, never scrape LinkedIn. Posting is manual.
- Never edit `kpi.yaml` targets or apply rubric changes without an explicit yes in this chat.
- VOICE.md edits: only when the user dictates a rule ("never say X", "I write Y"). Back up first (`cp VOICE.md VOICE.md.bak-<date>`).

## How to get the data out of LinkedIn (tell the user when asked)
- Weekly performance: LinkedIn → Me → Analytics (Creator/Content analytics) → Posts → Export (.xlsx, past 7 or 28 days). Send the file here.
- One post in detail: open the post → View analytics → Export.
- All past posts (voice examples): Settings → Data privacy → Get a copy of your data → Posts/Shares. Send the zip here.

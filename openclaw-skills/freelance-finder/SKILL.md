---
name: freelance-finder
description: Find remote freelance/contract gigs that match the user's skills across r/forhire, RemoteOK, and WeWorkRemotely, and draft a tailored pitch for each. Use when the user asks to find freelance work, gigs, contracts, jobs, or "who's hiring".
---

# freelance-finder

Scans multiple free job sources, scores each gig against the user's skill
profile with DeepSeek, and returns the strong matches with a ready-to-send pitch.

## When to use
Trigger when the user says things like:
- "find me freelance work" / "any gigs?" / "find contracts"
- "who's hiring for python/bots/automation?"

## How to run
Run the bundled stdlib-only script with the `exec` tool:

```bash
python3 ~/.openclaw/workspace/skills/freelance-finder/find_gigs.py
```

Needs `DEEPSEEK_API_KEY` in the environment (already provided by the gateway).
No pip installs required.

## Output
Each strong match prints as:
`[score/10] (source) <title>` + URL + why it fits + a tailored pitch.

Relay matches to the user clearly: score, source, title, link, and the pitch.
If it prints "No strong matches", say so and offer to try later.

## Output
Prints the TOP matches RANKED by fit (best first), each with score, source,
location, title, the real URL, why it fits, and a tailored first-person pitch.
🥇 = strong fit (8+), ✅ = solid (6-7). Relay the ranked list to the user as-is.

## Tuning
The candidate's CV lives in `profile.md` next to the script — edit it to tune
what scores high. Sources: Remotive (data category), Jobicy (data-science),
Arbeitnow (Europe/Germany — good for Berlin), r/forhire (freelance bonus).

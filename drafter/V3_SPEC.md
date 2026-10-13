# LinkedIn Drafter V3 — spec

Agreed with the founder on 2026-10-13, after a coach reviewed a V2 post. The coach's verdict: it got reach but few likes, because it was written for nobody in particular, never said what Bunny Search is, buried its best line, ended on an abstract question, stayed vague about early users, and repeated a stale date.

## Who a post is for
- **One audience per post**, chosen in a picker next to the idea box. V3 suggests one from the idea and the founder can override it.
  - DTC brands (DACH/EU, find UGC creators themselves)
  - Agencies
  - Berlin SMEs (CRM leads)
  - Founders (build-in-public)
- **Post type** picker: Build in public / Buyer insight / Engineer credibility. V3 suggests a type, and a rotation hint shows the types of recent posted drafts.

## Languages
- English + German, both written natively from the same plan (same core line, same facts). German uses **du**.

## Facts V3 treats as true
- Today's date is injected into every run, so no stale targets.
- Status: live with payments since October 2026, $0 MRR, a handful of warm leads, missed the September target.
- One-liner, worked naturally into every post: "Bunny Search finds UGC creators on Instagram and TikTok by niche and city, pulled fresh when you search instead of from a stale $300/month database."
- These go into the saved profile, replacing the stale default brief.

## Pipeline (Sonnet, `anthropic/claude-sonnet-5` via llm-gateway)
1. **Plan:** core line, audience, type, hook, ending, plus up to 3 questions about missing facts (e.g. "What did the early user actually say?").
2. **Founder answers** or skips each question. Nothing gets invented.
3. **Write 3 English drafts**, each with a different hook.
4. **Check** all 3 against the standards, reading as the chosen audience. Any draft that fails a check is rewritten once.
5. **Pick the best**, then write the German version natively and check it.
6. **First comment** (EN + DE) carrying the Bunny Search link.

Roughly 7 Sonnet calls, about 10–15 cents and about a minute per run.

## Standards checklist
A "Standards" dropdown with all four checks on by default. Each can be unticked for a single post. Every draft shows ✓/✗ per check.
1. **Hook = best line:** the strongest line is in the first two lines.
2. **Says what Bunny Search is:** the one-liner appears once (in natural words), the post speaks to the chosen audience's problem, and it never reads like an ad.
3. **Easy closing question:** the question has a short factual answer: yes/no, one of two plain options, a number or a tool. Reflective questions fail.
4. **True, current, specific:** dates fit today. Every concrete detail must trace back to the founder's source facts (idea, notes, answers, brief); the checker sees those facts and fails anything invented. At least one real detail is used.

A fix only replaces the original if it passes at least the same checks (so a fix can't buy a pass by inventing a quote).

Found in the first real Sonnet runs (2026-10-13): with both questions skipped, the fixer invented a German user quote and the writer added colour details ("rewrote the function four times"). That's why the checker now sees the sources and why fixes have to be "not worse".

## UI
- "Draft it" runs V3. "Draft with V2" stays as a secondary button for a few weeks. The V1 button is removed; its code is kept and old V1 runs stay readable.
- Notes for the agent, Polish correction and L1/L2 line numbers carry over to V3 (both languages).

## Measuring
- The Review tab adds **DMs** and **Signups** fields. The weekly analysis splits results by audience and post type. Likes are not the goal.

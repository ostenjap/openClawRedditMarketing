# R&D notes

Written by the user in the "R&D" sidebar tab on ui.techno-wanderlust.com: reflections and ideas
on what to improve overall (the Proxious UI, agents, crons, workflows, ...).

- One note per file: `notes/<YYYYMMDD-HHMMSS-xxxx>.md`, front matter `title`, `created`, `updated` (UTC), then Markdown.
- Deleted notes are moved to `notes/.trash/` (recoverable).
- Served by `rnd-notes.service` -> `/root/rnd-notes/rnd_notes_api.py` (127.0.0.1:8804, Caddy `/rnd-api/*`).
  Panel JS: `/root/openwebui-fork/crons-tab/rnd-panel.js`.

When reading these to plan improvements: treat them as the user's notes, and edit a file only if the user asks
(the panel refuses a save whose `updated` changed underneath it, so an outside edit shows up as a conflict in an open editor).

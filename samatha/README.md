# Samatha — LinkedIn social media manager (2nd Telegram bot next to Plank)

Local copies of what runs on the Hetzner box. The server is the source of truth for data; this folder is the source for code.

| Here | On the server |
|---|---|
| `samatha.py` | `/root/samatha/samatha.py` (symlink `/usr/local/bin/samatha`), data `/root/samatha/samatha.db` |
| `SOUL.md` | `/root/.hermes/profiles/samatha/SOUL.md` |
| `skill/linkedin/*` | `/root/.hermes/profiles/samatha/skills/linkedin/` (VOICE.md, kpi.yaml, rubric.yaml, samatha.yaml, examples/) |
| `hermes-gateway-samatha.service` | `/etc/systemd/system/` (refuses to start without its own, non-Plank token) |
| `deploy/patch_mission_collect.py` | applied to `/root/openwebui-fork/mission_collect.py` (backup `.bak-samatha-20261010`) |
| `tests/fake_e2e.sh` | `bash fake_e2e.sh /root/samatha/stage`: offline test, fake LLM, temp DB, $0 |

## Go live (one-time, you)
1. Telegram → @BotFather → `/newbot` → name `Samatha`, username e.g. `samatha_<something>_bot`. Copy the token.
2. On the server: `nano /root/.hermes/profiles/samatha/.env` → `TELEGRAM_BOT_TOKEN=<token>` (don't paste it into a chat).
3. `systemctl enable --now hermes-gateway-samatha && hermes gateway list` → samatha ✓.
4. DM the new bot: `/kpi`, then a topic.

## Changes made elsewhere (all backed up)
- llm-gateway: `anthropic/claude-sonnet-5` allowed; global daily cap $1.50 → $3.00 (`llm_gateway.py.bak-samatha-20261010`). Samatha's own cap is $1.50/day, $0.35 per idea.
- root crontab: Mon 07:00 `samatha remind`, daily 16:00 `samatha alerts --notify` (`/root/crontab.bak-samatha-20261010`).
- Mission panel: Samatha card in Crew (KPI line) + 2 automations.
- LinkedIn Drafter (Open WebUI) untouched = fallback.

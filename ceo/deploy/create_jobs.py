"""Create the two CEO Hermes cron jobs (idempotent by name). Run with the Hermes venv python:
   /usr/local/lib/hermes-agent/venv/bin/python create_jobs.py /root/ceo/deploy
Both jobs fire at the two possible UTC hours (the box has no TZ-aware cron); `ceo gate` wakes the agent only at the
Berlin hour and skips the LLM entirely otherwise ({"wakeAgent": false}, no cost, no message)."""
import sys
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/hermes-agent")
from cron.jobs import create_job, list_jobs  # noqa: E402

src = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/ceo/deploy")
DELIVER = "telegram:744825544"
JOBS = [
    dict(name="ceo-operate", schedule="0 6,7,11,12 * * 1-5", prompt=(src / "operate_prompt.txt").read_text().strip(),
         script="ceo_operate.sh", skills=["ceo"], deliver=DELIVER),  # model: Plank's default
    dict(name="ceo-meeting", schedule="0 6,7 * * 1", prompt=(src / "meeting_prompt.txt").read_text().strip(),
         script="ceo_meeting.sh", skills=["ceo"], deliver=DELIVER,
         model="anthropic/claude-opus-5.5", provider="openrouter"),
]
have = {j.get("name"): j for j in list_jobs(include_disabled=True)}
for spec in JOBS:
    if spec["name"] in have:
        print(f"{spec['name']}: exists ({have[spec['name']]['id']}), left unchanged")
        continue
    j = create_job(**spec)
    print(f"{spec['name']}: created {j['id']} model={j.get('model') or 'default'} next={j.get('next_run_at')}")

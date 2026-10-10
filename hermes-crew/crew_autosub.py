#!/usr/local/lib/hermes-agent/venv/bin/python
"""Crew guard: runs every minute from root's crontab; idempotent.

1. Auto-subscribe: make sure every crew card reports back to the user's Telegram.
   Cards created by Hermes' agent tools or the CLI get no notify subscription
   (only the /kanban slash command adds one), so their done/blocked events would
   never reach the user.
2. Default limits: cards that were created without them get a run-time cap and a
   retry cap, so a stuck worker can't run for hours (stale timeout is 4 h) or
   retry silently. Only fills NULLs, never overrides a value someone set on purpose.
"""
import sys
import time

sys.path.insert(0, "/usr/local/lib/hermes-agent")
from hermes_cli import kanban_db as kb

CREW = ("scout", "scribe", "ops", "analyst")
CHAT = "744825544"  # user's Telegram DM with Hermes
DEFAULT_MAX_RUNTIME = 1200  # seconds (20 min)
DEFAULT_MAX_RETRIES = 1  # 1 = block on the first failure, no silent retry

conn = kb.connect()
try:
    since = int(time.time()) - 2 * 86400
    marks = ",".join("?" * len(CREW))

    q = (
        "SELECT id FROM tasks WHERE assignee IN (%s) "
        "AND status NOT IN ('done','archived') AND created_at > ? "
        "AND id NOT IN (SELECT task_id FROM kanban_notify_subs "
        "WHERE platform='telegram' AND chat_id=?)" % marks
    )
    for (tid,) in conn.execute(q, (*CREW, since, CHAT)).fetchall():
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id=CHAT, notifier_profile="default")
        print(f"{time.strftime('%F %T')} subscribed {tid}")

    live = "assignee IN (%s) AND status NOT IN ('done','archived') AND created_at > ?" % marks
    for (tid,) in conn.execute(
        f"SELECT id FROM tasks WHERE {live} AND max_runtime_seconds IS NULL", (*CREW, since)
    ).fetchall():
        conn.execute(
            "UPDATE tasks SET max_runtime_seconds=? WHERE id=? AND max_runtime_seconds IS NULL",
            (DEFAULT_MAX_RUNTIME, tid),
        )
        print(f"{time.strftime('%F %T')} max_runtime={DEFAULT_MAX_RUNTIME}s set on {tid}")
    for (tid,) in conn.execute(
        f"SELECT id FROM tasks WHERE {live} AND max_retries IS NULL", (*CREW, since)
    ).fetchall():
        conn.execute(
            "UPDATE tasks SET max_retries=? WHERE id=? AND max_retries IS NULL",
            (DEFAULT_MAX_RETRIES, tid),
        )
        print(f"{time.strftime('%F %T')} max_retries={DEFAULT_MAX_RETRIES} set on {tid}")
finally:
    conn.close()

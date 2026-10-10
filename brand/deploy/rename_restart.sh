#!/usr/bin/env bash
# Proxious rename — the part that needs an Open WebUI restart (approved by the user 2026-10-10).
#  1. snapshot env.py + webui.db (online sqlite backup)   2. docker stop (DB is only edited while the app is down)
#  3. display-name override for the Hermes model: id stays 'hermes-agent' (old chats keep working), name 'Proxious'
#  4. docker cp the patched env.py (WEBUI_NAME 'Proxious', no ' (Open WebUI)' suffix — LICENSE 4(i), <= 50 users)
#  5. docker start, wait healthy, run checks   6. any failure -> restore env.py + db, start again, re-verify
set -uo pipefail
CT=/root/openwebui-fork/crons-tab
DB=/var/lib/docker/volumes/open-webui/_data/webui.db
SNAP=/root/openwebui-fork/snapshots/rename-20261010
BASE=http://127.0.0.1:3000
ADMIN_ID=${ADMIN_ID:?admin user id}
MODEL_ID=hermes-agent
mkdir -p "$SNAP"

log(){ echo "[$(date +%T)] $*"; }
code(){ curl -s -o /dev/null -w '%{http_code}' "$BASE$1"; }
wait_healthy(){ for i in $(seq 1 90); do [ "$(code /health)" = 200 ] && return 0; sleep 2; done; return 1; }
checks(){
  local ok=0
  [ "$(code /health)" = 200 ] || { log "✘ /health"; ok=1; }
  [ "$(code /)" = 200 ] || { log "✘ /"; ok=1; }
  [ "$(code /api/v1/hermiscrons)" = 401 ] || { log "✘ hermiscrons auth"; ok=1; }
  [ "$(code /crons-tab.js)" = 200 ] || { log "✘ crons-tab.js"; ok=1; }
  local name
  name=$(curl -s "$BASE/api/config" | python3 -c "import json,sys;print(json.load(sys.stdin).get('name'))" 2>/dev/null)
  [ "$name" = "Proxious" ] || { log "✘ app name is '$name'"; ok=1; }
  return $ok
}
rollback(){
  log "ROLLBACK"
  docker stop -t 20 open-webui >/dev/null
  docker cp "$SNAP/env.py" open-webui:/app/backend/open_webui/env.py
  cp "$SNAP/webui.db" "$DB"; rm -f "$DB-wal" "$DB-shm"
  docker start open-webui >/dev/null
  if wait_healthy; then log "rolled back, healthy"; else log "!! rollback: still unhealthy, check docker logs open-webui"; fi
  exit 1
}

log "snapshot"
docker cp open-webui:/app/backend/open_webui/env.py "$SNAP/env.py" || exit 3
python3 -c "import sqlite3;s=sqlite3.connect('$DB');d=sqlite3.connect('$SNAP/webui.db');s.backup(d);d.close();s.close()" || exit 3
log "stop (downtime starts)"
docker stop -t 20 open-webui >/dev/null || exit 3
log "model display name"
python3 - "$DB" "$ADMIN_ID" "$MODEL_ID" <<'EOF' || rollback
import json, sqlite3, sys, time
db, admin, mid = sys.argv[1:4]
c = sqlite3.connect(db)
now = int(time.time())
meta = {"profile_image_url": "/static/favicon.png",
        "description": "Proxious agent: same brain as the Telegram bot."}
if c.execute("select 1 from model where id=?", (mid,)).fetchone():
    c.execute("update model set name='Proxious', updated_at=? where id=?", (now, mid))
else:
    c.execute("insert into model(id,user_id,base_model_id,name,params,meta,updated_at,created_at,is_active) "
              "values(?,?,NULL,'Proxious','{}',?,?,?,1)", (mid, admin, json.dumps(meta), now, now))
c.commit(); c.close(); print("model row ok")
EOF
docker cp "$CT/patched_env.py" open-webui:/app/backend/open_webui/env.py || rollback
log "start"
docker start open-webui >/dev/null || rollback
wait_healthy || { log "✘ not healthy in 180 s"; rollback; }
sleep 3
checks || rollback
log "✔ all checks passed (downtime over)"

#!/usr/bin/env bash
# Deploy the sidebar panels (Crons + LinkedIn Drafter) — injection route, no frontend rebuild.
# Static files (index.html, crons-tab.js, drafter-panel.js) take effect instantly via docker cp, no restart.
# BACKEND python (main.py, routers) needs a restart: use safe_restart_openwebui.sh (snapshot + verify + auto-rollback).
#
# Deploy:      bash /root/openwebui-fork/crons-tab/deploy.sh
# Rollback:    bash /root/openwebui-fork/crons-tab/deploy.sh rollback     (restores pristine index.html, removes injectors)
# Verify-only: bash /root/openwebui-fork/crons-tab/deploy.sh verify
set -euo pipefail
CTX=/root/openwebui-fork/crons-tab
HOST=https://ui.techno-wanderlust.com
SCRIPTS=(crons-tab.js drafter-panel.js mission-panel.js crm-panel.js kanban-panel.js notifications-panel.js brand.js)
BRAND=$CTX/brand/static   # Proxious logo set (built by brand/build_brand.py in the repo); originals in $CTX/brand-orig-20261010/

case "${1:-deploy}" in

verify)
  echo "=== public checks (unauth) ==="
  for js in "${SCRIPTS[@]}"; do
    curl -s -o /dev/null -w "/$js -> HTTP %{http_code} (want 200)\n" "$HOST/$js"
  done
  page=$(curl -s "$HOST/")
  for js in "${SCRIPTS[@]}"; do
    echo "$page" | grep -q "$js" && echo "index.html          -> $js tag present ✓" || echo "index.html          -> $js tag MISSING ✗"
  done
  curl -s -o /dev/null -w '/crons (unauth)     -> HTTP %{http_code} (want 302 -> /#crons)\n' "$HOST/crons"
  curl -s -o /dev/null -w '/api/v1/linkedin-drafter/status (unauth) -> HTTP %{http_code} (want 401; 404 = backend not deployed yet)\n' "$HOST/api/v1/linkedin-drafter/status"
  ;;

rollback)
  echo "=== rollback: restore pristine index.html, remove injectors ==="
  docker cp "$CTX/index.html.orig" open-webui:/app/build/index.html
  for js in "${SCRIPTS[@]}"; do docker exec open-webui rm -f "/app/build/$js"; done
  echo "[ok] live container reverted. (Image tag openwebui-crons:0.11.4 was never overwritten.)"
  bash "$0" verify || true
  ;;

deploy)
  cd "$CTX"

  echo "=== 1. pristine index.html snapshot (rollback anchor) ==="
  if [ ! -f index.html.orig ]; then
    docker cp open-webui:/app/build/index.html index.html.orig
  fi
  echo "saved: $(wc -c < index.html.orig) bytes"

  echo "=== 2. patched index.html (idempotent insert before </body>) ==="
  cp index.html.orig index.html
  python3 - <<'PY'
p = '/root/openwebui-fork/crons-tab/index.html'
s = open(p).read()
lines = ['<script src="/crons-tab.js?v=20261010f" defer></script>', '<script src="/drafter-panel.js?v=20261010f" defer></script>', '<script src="/mission-panel.js?v=20261010f" defer></script>', '<script src="/crm-panel.js?v=20261010f" defer></script>', '<script src="/kanban-panel.js?v=20261010f" defer></script>', '<script src="/notifications-panel.js?v=20261010f" defer></script>', '<script src="/brand.js?v=20261010f" defer></script>']
assert '</body>' in s, '</body> anchor missing'
add = [l for l in lines if l.split('"')[1] not in s]
if add:
    s = s.replace('</body>', '\n'.join(add) + '\n</body>', 1)
# Proxious branding (LICENSE section 4(i): <= 50 users): browser tab shows the white "coin" icon (readable on light
# and dark tab strips) instead of the transparent favicon.png; add an Apple touch icon.
old_icon = '<link rel="icon" type="image/png" href="/static/favicon.png"'
if old_icon in s:
    s = s.replace(old_icon, '<link rel="icon" type="image/png" sizes="96x96" href="/static/favicon-96x96.png?v=20261010f"', 1)
s = s.replace('<title>Open WebUI</title>', '<title>Proxious</title>', 1)  # first paint; the app then uses WEBUI_NAME
if 'rel="apple-touch-icon"' not in s:
    s = s.replace('</head>', '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png?v=20261010f" />\n</head>', 1)
open(p, 'w').write(s)
print('script tags inserted:', len(add))
PY

  echo "=== 3. copy into live container (instant, no restart) ==="
  for js in "${SCRIPTS[@]}"; do docker cp "$CTX/$js" "open-webui:/app/build/$js"; done
  docker cp "$CTX/index.html" open-webui:/app/build/index.html
  if [ -d "$BRAND" ]; then   # both static dirs: the backend copies build/static over its own on startup
    for f in "$BRAND"/*; do
      docker cp "$f" "open-webui:/app/build/static/$(basename "$f")"
      docker cp "$f" "open-webui:/app/backend/open_webui/static/$(basename "$f")"
    done
    docker cp "$BRAND/favicon.png" open-webui:/app/build/favicon.png   # /favicon.png = default model avatar
    echo "brand assets: $(ls "$BRAND" | wc -l) files"
  fi

  echo "=== 4. durable image layer (for future container recreation) ==="
  docker build -q -t openwebui-crons:0.11.4-tab "$CTX" \
    && echo "built: openwebui-crons:0.11.4-tab (openwebui-crons:0.11.4 untouched)"

  echo "=== 5. verify ==="
  sleep 1
  bash "$0" verify
  echo
  echo "DONE — hard-refresh https://ui.techno-wanderlust.com (logged in) to see the panels."
  ;;

*)
  echo "usage: deploy.sh [deploy|verify|rollback]"
  ;;
esac

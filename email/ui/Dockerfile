# Durable layer for the /crons sidebar tab + dashboard fix.
# Build:  docker build -t openwebui-crons:0.11.4-tab /root/openwebui-fork/crons-tab/
# Rollback: keep using openwebui-crons:0.11.4 (untouched).
FROM openwebui-crons:0.11.4
COPY hermiscrons_router.py /app/backend/open_webui/routers/hermiscrons.py
COPY index.html /app/build/index.html
COPY crons-tab.js /app/build/crons-tab.js
COPY patched_main.py /app/backend/open_webui/main.py
COPY linkedin_drafter_router.py /app/backend/open_webui/routers/linkedin_drafter.py
COPY drafter-panel.js /app/build/drafter-panel.js
COPY mission-panel.js /app/build/mission-panel.js
COPY crm-panel.js /app/build/crm-panel.js
COPY kanban-panel.js /app/build/kanban-panel.js
COPY notifications-panel.js /app/build/notifications-panel.js
COPY rnd-panel.js /app/build/rnd-panel.js
COPY tiktok-panel.js /app/build/tiktok-panel.js
COPY email-panel.js /app/build/email-panel.js
COPY brand.js /app/build/brand.js
# Proxious logo set (LICENSE 4(i), <= 50 users). Both static dirs: the backend copies build/static over its own on start.
COPY brand/static/ /app/build/static/
COPY brand/static/ /app/backend/open_webui/static/
COPY brand/static/favicon.png /app/build/favicon.png
# Keep PyTorch out of startup (~450 MB): lazy optional imports in langchain (see lazy_torch_patch.py).
COPY lazy_torch_patch.py /tmp/lazy_torch_patch.py
RUN python3 /tmp/lazy_torch_patch.py /usr/local/lib/python3.11/site-packages && rm /tmp/lazy_torch_patch.py
# Proxious name (LICENSE 4(i), <= 50 users): default WEBUI_NAME + no suffix
COPY patched_env.py /app/backend/open_webui/env.py

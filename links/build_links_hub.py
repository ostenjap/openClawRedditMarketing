#!/usr/bin/env python3
"""Build /srv/links-hub/index.html — linktree-style hub for ui.techno-wanderlust.com/links.

EDIT ONLY `GROUPS` below to add/remove/rename links.
The page DESIGN (responsive card grid + favourites stars + search) lives in
links_template.html next to this file — do NOT inline a new HTML template here, or the
layout/favourites are lost. The template has two placeholders: __CARDS__ and __FOOTER__.
"""
import html as H
import os

OUT = "/srv/links-hub/index.html"
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "links_template.html")
C = "https://campaign.techno-wanderlust.com"
VERIFIED = "2026-10-08"

# (group, [(title, desc, url, locked)])
GROUPS = [
 ("🚀 Products", [
   ("Buyer Signals", "Purchase-intent radar · sign-in", f"{C}/signals/", True),
   ("Bunny KPI", "Bunny metrics dashboard · sign-in", f"{C}/kpi/", True),
 ]),
 ("📋 CRMs", [
   ("Berlin CRM V2", "Berlin outreach CRM · sign-in", f"{C}/berlin-v2/", True),
   ("BerlinSME-UGC", "SME pilot tracker · sign-in", f"{C}/berlin-sme/", True),
   ("Maps CRM", "Second CRM instance · sign-in", f"{C}/maps-crm/", True),
 ]),
 ("🛠 Outreach & Intelligence", [
   ("Vendor Outreach Cockpit", "EU recovery wearable — 10 tailored vendor emails", f"{C}/outreach/", False),
   ("LinkedIn Comment Board", "Daily radar comment targets, auto-updated", f"{C}/comments/targets.html", False),
   ("LinkedIn Comment Playbook", "Strategy + humanized comment drafts", f"{C}/comments/", False),
 ]),
 ("🤖 AI Workspace", [
   ("Open WebUI", "The AI assistant fork — chat, tools, memory", "https://ui.techno-wanderlust.com/", False),
 ]),
 ("🏠 Main", [
   ("techno-wanderlust.com", "Osten Jap — engineer & indie hacker", "https://www.techno-wanderlust.com/", False),
 ]),
 ("⚙ Internal ops", [
   ("Uptime Kuma", "Service monitoring dashboard · sign-in", "http://178.105.191.249:3001/", True),
 ]),
]


def card(title, desc, url, locked):
    return (f'<a class="card" href="{H.escape(url, quote=True)}" target="_blank" rel="noopener">'
            f'<span class="t">{H.escape(title)}{" 🔒" if locked else ""}</span>'
            f'<span class="d">{H.escape(desc)}</span>'
            f'<span class="chev">›</span></a>')


body = []
for g, items in GROUPS:
    body.append(f'<h2>{H.escape(g)}</h2>')
    body.extend(card(*i) for i in items)

n_links = sum(len(items) for _, items in GROUPS)
footer = (f"{n_links} properties · all links verified live {VERIFIED} · maintained by Hermes · "
          "n8n (127.0.0.1:5678) is local-only")

with open(TEMPLATE, encoding="utf-8") as f:
    page = f.read()
assert "__CARDS__" in page and "__FOOTER__" in page, "links_template.html is missing its placeholders"
page = page.replace("__CARDS__", "\n".join(body)).replace("__FOOTER__", H.escape(footer))

os.makedirs("/srv/links-hub", exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(page)
os.chmod(OUT, 0o644)
print("written:", OUT, os.path.getsize(OUT), "bytes,", n_links, "links")

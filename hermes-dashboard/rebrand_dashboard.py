#!/usr/bin/env python3
"""Rebrand the Hermes web dashboard (hermes.178-105-191-249.sslip.io) as Proxious. Idempotent; re-run after every
`hermes update` / web_dist rebuild (those restore the stock files).

Only user-visible text changes. Kept as-is: code identifiers (updateHermes, checkHermesUpdate, __HERMES_*), the
X-Hermes-Session-Token header, /api/hermes routes, the `hermes` CLI in help texts, "Nous Research" (the makers),
and the phrases that point at the real upstream project ("the Hermes team", "the Hermes index").

The patched bundle is written under a NEW filename and index.html points at it, so browsers can't keep a cached
stock copy. Originals are kept once in <web_dist>/.stock-<name> for rollback (`--rollback` restores them).
Usage: rebrand_dashboard.py [--web-dist DIR] [--favicon PNG] [--rollback]
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

NEW = "Proxious"
WORD = re.compile(r"(?<![A-Za-z0-9_$\-/.])Hermes(?![A-Za-z0-9_$\-])")
KEEP = ["the Hermes team", "the Hermes index", "X-Hermes-Session-Token"]
SUFFIX = ".px.js"

ap = argparse.ArgumentParser()
ap.add_argument("--web-dist", default="/usr/local/lib/hermes-agent/hermes_cli/web_dist")
ap.add_argument("--favicon", default="/root/openwebui-fork/crons-tab/brand/static/favicon-96x96.png")
ap.add_argument("--rollback", action="store_true")
a = ap.parse_args()
W = Path(a.web_dist)
index = W / "index.html"


def stock(p):
    return p.with_name(".stock-" + p.name)


FRESH = SUFFIX not in index.read_text() if index.exists() else False  # stock files (first run or after an update)


def keep_stock(p):
    s = stock(p)
    if FRESH or not s.exists():
        shutil.copy2(p, s)


if a.rollback:
    for name in ("index.html", "favicon.ico"):
        if stock(W / name).exists():
            shutil.copy2(stock(W / name), W / name)
    for p in (W / "assets").glob("*" + SUFFIX):
        p.unlink()
    print("rolled back to the stock dashboard files")
    sys.exit(0)

keep_stock(index)
html = stock(index).read_text()
m = re.search(r'src="/assets/(index-[^"]+?\.js)"', html)
if not m:
    sys.exit("can't find the bundle in index.html")
bundle = W / "assets" / m.group(1)
js = bundle.read_text(encoding="utf-8")

# 1) the sidebar wordmark: "Hermes" <br> "Agent"  ->  "Proxious"
js, n_logo = re.subn(r'children:\["Hermes",(\w+)\.jsx\("br",\{\}\),"Agent"\]', 'children:["Proxious"]', js)
# 2) "Hermes Agent" (titles, share texts) -> "Proxious"
js, n_full = re.subn(r"Hermes Agent", NEW, js)
# 3) every other standalone visible "Hermes" word, with the protected phrases parked first
parked = {}
for i, k in enumerate(KEEP):
    tok = f"\x00KEEP{i}\x00"
    parked[tok] = k
    js = js.replace(k, tok)
js, n_word = WORD.subn(NEW, js)
for tok, k in parked.items():
    js = js.replace(tok, k)

# sanity: code-facing names must be untouched
for must in ("updateHermes", "checkHermesUpdate", "__HERMES_", "X-Hermes-Session-Token", "/api/hermes"):
    orig_n = bundle.read_text(encoding="utf-8").count(must)
    if js.count(must) != orig_n:
        sys.exit(f"abort: {must!r} count changed ({orig_n} -> {js.count(must)})")

out = bundle.with_name(bundle.stem + SUFFIX)
out.write_text(js, encoding="utf-8")
html = html.replace(f"/assets/{bundle.name}", f"/assets/{out.name}")
html = re.sub(r"<title>[^<]*</title>", f"<title>{NEW} - Dashboard</title>", html)
html = html.replace('<link rel="icon" type="image/svg+xml" href="/favicon.ico" />',
                    '<link rel="icon" type="image/png" href="/favicon.ico" />')
index.write_text(html)

fav = W / "favicon.ico"
if Path(a.favicon).exists():
    keep_stock(fav)
    shutil.copy2(a.favicon, fav)
print(f"bundle {bundle.name} -> {out.name}: wordmark {n_logo}, 'Hermes Agent' {n_full}, 'Hermes' {n_word}; "
      f"title + favicon updated")

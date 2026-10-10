#!/usr/bin/env python3
"""Berlin CRM V2 — redesigned outreach CRM (BerlinSME-UGC v2, same data).

Run:  python3 app.py                      # binds 127.0.0.1:8794 (Caddy: /berlin-sme)
Data: data/leads.json (source of truth), CLI: python3 rr.py ...

Security model:
- secret code gate: login form -> HttpOnly SameSite cookie. The code is stored
  as a SHA-256 hash in .secret_hash and is never written to disk in clear.
- never a token in the URL (survives screenshots / history / referer leaks)
- 10 failed logins per minute per IP -> 15-min lockout, every attempt logged to
  data/access.log with IP + UA
- all responses carry CSP / nosniff / frame-deny / no-store
- all interpolation HTML-escaped client-side; status whitelist server-side
"""
import csv, hashlib, hmac, io, json, os, re, secrets, time, datetime, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
STORE = os.path.join(DATA, "leads.json")
HASH_FILE = os.path.join(BASE, ".secret_hash")
SESS_FILE = os.path.join(DATA, "sessions.json")
AUDIT = os.path.join(DATA, "access.log")
PORT = int(os.environ.get("BS_PORT", "8798"))
BASE_PATH = os.environ.get("BS_BASE", "/berlin-v2").rstrip("/")
COOKIE = "bs_s"
APPNAME = "Berlin CRM V2"
STATUSES = ["new", "contacted", "replied", "meeting", "won", "lost"]
CAPS = {"company": 120, "city": 80, "country": 40, "email": 200, "need": 600,
        "category": 200, "linkedin": 250, "source_url": 300, "note": 800}
SAFE_URL = re.compile(r"^https?://[^\s\"'<>]+$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s\.]+\.[^@\s]+$")

os.makedirs(DATA, exist_ok=True)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def audit(ip, event, ua=""):
    with open(AUDIT, "a") as fh:
        fh.write(f"{now()} {ip:<22} {event:<16} {ua[:80]}\n")


def code_hash():
    if os.path.exists(HASH_FILE):
        return open(HASH_FILE).read().strip()
    raise SystemExit(f"no {HASH_FILE} - run: python3 set_secret.py \"your-code\"")


def load():
    if not os.path.exists(STORE):
        return []
    try:
        return json.load(open(STORE, encoding="utf-8"))
    except Exception:
        return []


def save(leads):
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(leads, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, STORE)


def sessions():
    if os.path.exists(SESS_FILE):
        try:
            return json.load(open(SESS_FILE))
        except Exception:
            return {}
    return {}


def clean(s, key, default=""):
    if s is None:
        return default
    s = str(s).strip()
    if key in CAPS:
        s = s[:CAPS[key]]
    return s


def url_ok(v):
    v = str(v or "").strip()
    return v if SAFE_URL.match(v) else ""


_h = {}
def limited(ip, fail=False):
    t = int(time.time())
    ent = _h.setdefault(ip, [t, 0])
    if ent[0] != t:
        _h[ip] = [t, 1 if fail else 0]
        ent = _h[ip]
    ent[1] += 1
    return _h[ip][1] > (10 if fail else 240)


PAGE = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>__APPNAME__</title><style>
/* Palette = Open WebUI's neutral gray scale. When embedded in ui.techno-wanderlust.com the parent
   posts its REAL computed colours (light/dark/OLED) and these variables are overwritten. */
:root{
  --bg:#171717;--txt:#e3e3e3;--mut:#9b9b9b;--faint:#676767;--line:#262626;
  --hover:rgba(255,255,255,.045);--soft:rgba(255,255,255,.045);
  --ok:#34c759;--err:#f87171;--warn:#fbbf24;--errbg:rgba(248,113,113,.16);--warnbg:rgba(251,191,36,.16);
  --s-new:#38bdf8;--s-contacted:#60a5fa;--s-replied:#fbbf24;--s-meeting:#a78bfa;--s-won:#34c759;--s-lost:#f87171;
  --r:.75rem;color-scheme:dark;
}
:root.light{--bg:#fff;--txt:#333;--mut:#6b6b6b;--faint:#9b9b9b;--line:#ececec;--hover:rgba(0,0,0,.035);--soft:rgba(0,0,0,.035);color-scheme:light}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--txt);font:13px/1.25rem -apple-system,"Segoe UI",Roboto,Inter,system-ui,sans-serif;overflow:hidden}
button{font:inherit;color:inherit;background:transparent;border:0;padding:0;cursor:pointer}
a{color:inherit}
:focus-visible{outline:2px solid var(--faint);outline-offset:-2px;border-radius:.5rem}
.shell{display:flex;height:100%}
.main{flex:1;min-width:0;display:flex;flex-direction:column;container-type:inline-size}
/* toolbar */
.bar{flex:none;display:flex;align-items:center;gap:.5rem;padding:.5rem .75rem;flex-wrap:wrap}
.brand{font-size:.875rem;font-weight:500;margin-right:.25rem;white-space:nowrap}
.brand small{color:var(--mut);font-weight:400;margin-left:.375rem}
body.embed .brand{display:none}
.search{flex:1;min-width:9rem;max-width:26rem;display:flex;align-items:center;gap:.5rem;padding:0 .625rem;height:2rem;border-radius:.75rem;background:var(--soft)}
.search svg{width:.875rem;height:.875rem;flex:none;color:var(--mut)}
.search input{flex:1;min-width:0;border:0;outline:0;background:transparent;color:inherit;font:inherit;font-size:.875rem}
.search input::placeholder{color:var(--faint)}
.kbd{font-size:.6875rem;color:var(--faint);border:1px solid var(--line);border-radius:.375rem;padding:0 .3rem;line-height:1.1rem}
.spacer{flex:1}
.seg{display:flex;background:var(--soft);border-radius:.75rem;padding:2px}
.seg button{height:1.75rem;padding:0 .625rem;border-radius:.625rem;color:var(--mut);font-size:.8125rem}
.seg button[aria-pressed=true]{background:var(--bg);color:var(--txt);box-shadow:0 0 0 1px var(--line)}
.btn{height:2rem;padding:0 .75rem;border-radius:.75rem;font-size:.8125rem;display:inline-flex;align-items:center;gap:.375rem;white-space:nowrap}
a.btn{text-decoration:none}
.btn.primary{background:var(--txt);color:var(--bg);font-weight:500}
.btn.primary:hover{opacity:.88}
.btn.ghost{background:var(--soft)}.btn.ghost:hover{background:var(--hover);box-shadow:inset 0 0 0 1px var(--line)}
.btn.danger{color:var(--err)}.btn.danger:hover{background:var(--errbg)}
.iconbtn{width:2rem;height:2rem;border-radius:.75rem;display:inline-flex;align-items:center;justify-content:center;color:var(--mut)}
.iconbtn:hover{background:var(--hover);color:var(--txt)}
.iconbtn svg{width:1rem;height:1rem}
/* filters */
.filters{flex:none;display:flex;align-items:center;gap:.25rem;padding:0 .75rem .5rem;overflow-x:auto;scrollbar-width:none}
.filters::-webkit-scrollbar{display:none}
.chip{flex:none;height:1.75rem;display:inline-flex;align-items:center;gap:.375rem;padding:0 .625rem;border-radius:.75rem;color:var(--mut);font-size:.8125rem;white-space:nowrap;transition:background-color .15s,color .15s}
.chip:hover{background:var(--hover)}
.chip[aria-pressed=true]{background:var(--soft);color:var(--txt);box-shadow:inset 0 0 0 1px var(--line)}
.chip .n{color:var(--faint);font-variant-numeric:tabular-nums}
.chip[aria-pressed=true] .n{color:var(--mut)}
.dot{flex:none;width:.4375rem;height:.4375rem;border-radius:9999px;background:var(--faint)}
.sep{flex:none;width:1px;height:1rem;background:var(--line);margin:0 .375rem}
/* list */
.pane{flex:1;min-height:0;overflow:auto;padding:0 .5rem 1rem}
.lhead,.lrow{display:grid;grid-template-columns:minmax(0,2.4fr) minmax(0,1.3fr) 4.5rem 7.5rem 5rem minmax(0,2fr);gap:.75rem;align-items:center;padding:0 .5rem}
.lhead{position:sticky;top:0;z-index:2;background:var(--bg);height:2rem;font-size:.6875rem;letter-spacing:.04em;text-transform:uppercase;color:var(--faint);border-bottom:1px solid var(--line)}
.lhead button{text-align:left;text-transform:inherit;letter-spacing:inherit;color:inherit;display:inline-flex;gap:.25rem}
.lhead button:hover{color:var(--txt)}
.lrow{min-height:2.5rem;border-radius:.75rem;cursor:pointer;transition:background-color .12s}
.lrow:hover{background:var(--hover)}
.lrow.sel{background:var(--soft);box-shadow:inset 0 0 0 1px var(--line)}
.co{display:flex;align-items:center;gap:.625rem;min-width:0}
.av{flex:none;width:1.5rem;height:1.5rem;border-radius:9999px;background:var(--soft);display:flex;align-items:center;justify-content:center;font-size:.6875rem;font-weight:600;color:var(--mut)}
.nm{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cell{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mut)}
.warnmark{color:var(--warn);margin-left:.25rem}
.tier{display:inline-block;font-size:.6875rem;font-weight:600;border-radius:.375rem;padding:0 .375rem;line-height:1.1rem;background:var(--soft);color:var(--mut)}
.tier.a{color:#4ade80;background:rgba(74,222,128,.14)}.tier.b{color:var(--warn);background:var(--warnbg)}.tier.c{color:var(--err);background:var(--errbg)}
:root.light .tier.a{color:#15803d}:root.light .tier.b{color:#a16207}:root.light .tier.c{color:#b91c1c}
.pill{display:inline-flex;align-items:center;gap:.375rem;height:1.5rem;padding:0 .5rem;border-radius:.625rem;font-size:.75rem;background:var(--soft);color:var(--txt);white-space:nowrap}
.pill:hover{box-shadow:inset 0 0 0 1px var(--line)}
.pill .dot{background:var(--faint)}
.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.5rem;min-height:14rem;color:var(--mut);text-align:center}
.empty b{font-weight:400;color:var(--txt);font-size:.875rem}
/* narrow: two-line rows */
@container (max-width:760px){
  .lhead{display:none}
  .lrow{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"a s" "b b";row-gap:0;padding:.5rem}
  .lrow .c-co{grid-area:a}.lrow .c-st{grid-area:s}.lrow .c-meta{grid-area:b;display:flex;gap:.5rem;align-items:center;padding-left:2.125rem}
  .lrow .c-loc,.lrow .c-tier,.lrow .c-age,.lrow .c-mail{display:none}
}
.c-meta{display:none}
/* board */
.board{flex:1;min-height:0;display:flex;gap:.5rem;overflow-x:auto;padding:0 .75rem .75rem}
.col{flex:0 0 17rem;display:flex;flex-direction:column;min-height:0;border-radius:var(--r);background:var(--soft)}
.col.over{box-shadow:inset 0 0 0 1.5px var(--faint)}
.colh{flex:none;display:flex;align-items:center;gap:.5rem;padding:.5rem .75rem;font-size:.8125rem}
.colh .n{color:var(--faint);margin-left:auto;font-variant-numeric:tabular-nums}
.colb{flex:1;min-height:0;overflow-y:auto;padding:0 .375rem .375rem;display:flex;flex-direction:column;gap:.375rem}
.bcard{background:var(--bg);border:1px solid var(--line);border-radius:.75rem;padding:.5rem .625rem;cursor:grab}
.bcard:hover{border-color:var(--faint)}.bcard.sel{border-color:var(--mut)}
.bcard.drag{opacity:.4}
.bcard .t{display:flex;align-items:center;gap:.375rem}
.bcard .m{color:var(--mut);font-size:.75rem;margin-top:.125rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.colempty{color:var(--faint);font-size:.75rem;padding:.5rem .625rem}
/* drawer */
.drawer{flex:none;width:0;overflow:hidden;border-left:0 solid var(--line);transition:width .18s ease-out}
.drawer.open{width:26rem;border-left-width:1px}
.dinner{width:26rem;height:100%;overflow-y:auto;padding:.75rem 1rem 1.5rem}
.dtop{display:flex;align-items:center;gap:.625rem;margin-bottom:.75rem}
.dtop .av{width:2.25rem;height:2.25rem;font-size:.9375rem}
.dtop h2{margin:0;font-size:1rem;font-weight:500;line-height:1.3rem;flex:1;min-width:0;overflow-wrap:anywhere}
.stepper{display:flex;flex-wrap:wrap;gap:.25rem;margin-bottom:1rem}
.stepper button{height:1.75rem;padding:0 .625rem;border-radius:.625rem;background:var(--soft);color:var(--mut);display:inline-flex;align-items:center;gap:.375rem;font-size:.75rem}
.stepper button:hover{box-shadow:inset 0 0 0 1px var(--line);color:var(--txt)}
.stepper button[aria-pressed=true]{background:var(--txt);color:var(--bg)}
.stepper button[aria-pressed=true] .dot{box-shadow:0 0 0 1.5px var(--bg)}
.sec{margin:1rem 0 .375rem;font-size:.6875rem;letter-spacing:.05em;text-transform:uppercase;color:var(--faint)}
.kv{display:grid;grid-template-columns:5.5rem minmax(0,1fr);gap:.375rem .75rem;align-items:baseline}
.kv dt{color:var(--mut)}.kv dd{margin:0;overflow-wrap:anywhere}
.kv a{text-decoration:none;border-bottom:1px solid var(--line)}.kv a:hover{border-color:var(--mut)}
.brief{white-space:pre-line;color:var(--txt);background:var(--soft);border-radius:.75rem;padding:.625rem .75rem;overflow-wrap:anywhere}
.acts{display:flex;gap:.375rem;flex-wrap:wrap;margin:.25rem 0}
.tl{display:flex;flex-direction:column;gap:.5rem;margin-top:.5rem}
.tl div{border-left:2px solid var(--line);padding-left:.625rem}
.tl time{display:block;color:var(--faint);font-size:.6875rem}
.tl span{overflow-wrap:anywhere;white-space:pre-line}
textarea,input.f{width:100%;font:inherit;font-size:.875rem;color:var(--txt);background:var(--soft);border:1px solid transparent;border-radius:.75rem;padding:.5rem .75rem;outline:0;resize:vertical}
textarea:focus,input.f:focus{border-color:var(--line);background:transparent}
textarea::placeholder,input.f::placeholder{color:var(--faint)}
.foot{margin-top:1.5rem;padding-top:.75rem;border-top:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;color:var(--faint);font-size:.75rem}
/* mobile: drawer becomes full-screen */
@media (max-width:760px){
  .drawer{position:fixed;inset:0;z-index:30;background:var(--bg);width:100%;transform:translateX(100%);transition:transform .2s ease-out;visibility:hidden}
  .drawer.open{width:100%;transform:none;visibility:visible;border-left:0}
  .dinner{width:100%}
  .search{max-width:none}
  .kbd{display:none}
  .col{flex-basis:80vw}
}
/* menus, dialogs, toast */
.pop{position:fixed;z-index:60;min-width:11rem;background:var(--bg);border:1px solid var(--line);border-radius:.875rem;padding:.25rem;box-shadow:0 8px 24px rgba(0,0,0,.28)}
.pop button,.pop a{display:flex;align-items:center;gap:.5rem;width:100%;height:2rem;padding:0 .625rem;border-radius:.625rem;text-align:left;text-decoration:none;font-size:.8125rem}
.pop button:hover,.pop a:hover{background:var(--hover)}
.pop .k{margin-left:auto;color:var(--faint);font-size:.6875rem}
.ov{position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.5);display:none;align-items:center;justify-content:center;padding:1rem}
.ov.show{display:flex}
.dlg{width:min(100%,28rem);max-height:90vh;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:1rem;padding:1rem 1.125rem}
.dlg h3{margin:0 0 .75rem;font-size:.9375rem;font-weight:500}
.dlg label{display:block;margin:.625rem 0 .25rem;color:var(--mut);font-size:.75rem}
.dlg .row{display:flex;gap:.5rem}.dlg .row>div{flex:1}
.dlg .btns{display:flex;justify-content:flex-end;gap:.5rem;margin-top:1rem}
.sc{display:grid;grid-template-columns:auto 1fr;gap:.375rem 1rem;color:var(--mut)}
.sc b{font-weight:400;color:var(--txt)}
@media (max-width:760px){.ov{align-items:flex-end;padding:0}.dlg{border-radius:1rem 1rem 0 0;width:100%}}
#toast{position:fixed;left:50%;bottom:1rem;transform:translateX(-50%);z-index:70;display:none;align-items:center;gap:.75rem;background:var(--txt);color:var(--bg);border-radius:.875rem;padding:.5rem .5rem .5rem .875rem;font-size:.8125rem;box-shadow:0 8px 24px rgba(0,0,0,.3)}
#toast.show{display:flex}
#toast button{height:1.625rem;padding:0 .625rem;border-radius:.5rem;background:rgba(127,127,127,.28);font-weight:500}
.swipe{position:relative;border-radius:.75rem;overflow:hidden}
.swipe .lrow{position:relative;z-index:1;background:var(--bg);touch-action:pan-y}
.swipe.anim .lrow{transition:transform .18s ease-out}
.sbg{position:absolute;inset:0;display:flex;align-items:center;justify-content:space-between;padding:0 1rem;font-size:.8125rem;font-weight:500;color:#fff;opacity:0;transition:opacity .15s}
.sbg.vis{opacity:1}
</style></head><body>
<div class="shell">
 <div class="main">
  <div class="bar">
   <div class="brand">__APPNAME__<small id="cnt"></small></div>
   <label class="search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"/></svg>
    <input id="q" type="search" placeholder="Search company, email, need…" aria-label="Search leads" autocomplete="off"><span class="kbd">/</span></label>
   <div class="seg" role="group" aria-label="View"><button id="v-list" aria-pressed="true">List</button><button id="v-board" aria-pressed="false">Board</button></div>
   <span class="spacer"></span>
   <button class="btn primary" id="btn-add">＋ Add lead</button>
   <button class="iconbtn" id="btn-more" aria-label="More actions" aria-haspopup="menu"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg></button>
  </div>
  <div class="filters" id="filters" role="toolbar" aria-label="Filters"></div>
  <div id="content" style="display:flex;flex-direction:column;flex:1;min-height:0"></div>
 </div>
 <aside class="drawer" id="drawer" aria-label="Lead details"><div class="dinner" id="dinner"></div></aside>
</div>
<div class="ov" id="ov"><div class="dlg" id="dlg" role="dialog" aria-modal="true"></div></div>
<div id="toast" role="status" aria-live="polite"></div>
<script>
'use strict';
const ST=['new','contacted','replied','meeting','won','lost'];
const BP='__BP__';
const PARENT='https://ui.techno-wanderlust.com';
const EMB=window.self!==window.top;
let LEADS=[],view='list',statusF='all',smart=null,sel=null,openId=null,sort={k:'created',d:-1};
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const isUrl=u=>{u=String(u||'');return u.slice(0,7)==='http://'||u.slice(0,8)==='https://'};
const EMAILRE=/^[^@\s]+@[^@\s.]+\.[^@\s]+$/;
const col=s=>`var(--s-${s})`;
const store=(k,v)=>{try{v===undefined?localStorage.getItem(k):localStorage.setItem(k,v)}catch(_){}};
const stored=k=>{try{return localStorage.getItem(k)}catch(_){return null}};

/* ---- theme: follow the parent app's real colours when embedded ---------------------- */
const VARS=['bg','txt','mut','faint','line','hover','soft','ok','err','warn','errbg','warnbg'];
const SAFE=/^[#a-zA-Z0-9(),.%\/ \-]{1,80}$/;
function applyTheme(t){
 if(!t)return;
 document.documentElement.classList.toggle('light',t.dark===false);
 VARS.forEach(k=>{const v=t.vars&&t.vars[k];if(typeof v==='string'&&SAFE.test(v))document.documentElement.style.setProperty('--'+k,v)});
 store('crm.theme',JSON.stringify(t));
}
try{const s=JSON.parse(stored('crm.theme')||'null');if(s)applyTheme(s);else if(matchMedia('(prefers-color-scheme: light)').matches)document.documentElement.classList.add('light')}catch(_){}
if(EMB){document.body.classList.add('embed');
 addEventListener('message',e=>{if(e.origin!==PARENT||!e.data||e.data.type!=='ui-theme')return;applyTheme(e.data)});
 try{parent.postMessage({type:'crm-ready'},PARENT)}catch(_){}}

/* ---- helpers ------------------------------------------------------------------------ */
function tierOf(l){const t=(l.notes||[]).map(n=>n.text||'').join(' ').toLowerCase();
 return t.includes('tier a')?'a':t.includes('tier b')?'b':t.includes('tier c')?'c':''}
const warnOf=l=>(l.notes||[]).some(n=>n.text&&n.text.includes('⚠'));
function days(iso){const t=Date.parse(iso);return isNaN(t)?null:(Date.now()-t)/864e5}
function ago(iso){const d=days(iso);if(d==null)return '—';if(d<1)return 'today';if(d<2)return '1d';if(d<14)return Math.floor(d)+'d';if(d<60)return Math.floor(d/7)+'w';return Math.floor(d/30)+'mo'}
const touched=l=>l.updated||l.created;
const stalled=l=>(l.status==='contacted'||l.status==='replied')&&(days(touched(l))||0)>=5;
const SMART={todo:{label:'To contact',f:l=>l.status==='new'},stalled:{label:'Follow-up due',f:stalled},
 a:{label:'Tier A',f:l=>tierOf(l)==='a'},warn:{label:'⚠ Warnings',f:warnOf}};
const loc=l=>[l.city,l.country].filter(Boolean).join(', ');
async function api(u,o){const r=await fetch(u,{credentials:'same-origin',...o});
 if(r.status===401){location.reload();throw new Error('auth')}
 if(!r.ok)throw new Error(await r.text());return r.json()}
const post=(u,b)=>api(BP+u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});

/* ---- data selection ----------------------------------------------------------------- */
function visible(){
 const q=$('q').value.trim().toLowerCase();
 let l=LEADS.filter(x=>(statusF==='all'||x.status===statusF)&&(!smart||SMART[smart].f(x)));
 if(q)l=l.filter(x=>[x.company,x.email,x.need,x.category,x.city,x.country].join(' ').toLowerCase().includes(q));
 const K={company:x=>String(x.company||'').toLowerCase(),status:x=>ST.indexOf(x.status),created:x=>String(x.created||''),
  updated:x=>String(touched(x)||''),tier:x=>({a:1,b:2,c:3}[tierOf(x)]||9),loc:x=>loc(x).toLowerCase()}[sort.k];
 return l.sort((a,b)=>{const x=K(a),y=K(b);return (x<y?-1:x>y?1:0)*sort.d||String(b.created).localeCompare(String(a.created))});
}

/* ---- render ------------------------------------------------------------------------- */
function render(){
 $('cnt').textContent=LEADS.length+' leads';
 const cnt={};LEADS.forEach(x=>cnt[x.status]=(cnt[x.status]||0)+1);
 const chip=(a,on,inner)=>`<button class="chip" ${a} aria-pressed="${on}">${inner}</button>`;
 $('filters').innerHTML=
  chip('data-sf="all"',statusF==='all',`All <span class="n">${LEADS.length}</span>`)+
  ST.map(s=>chip(`data-sf="${s}"`,statusF===s,`<span class="dot" style="background:${col(s)}"></span>${s} <span class="n">${cnt[s]||0}</span>`)).join('')+
  '<span class="sep"></span>'+
  Object.keys(SMART).map(k=>chip(`data-sm="${k}"`,smart===k,`${SMART[k].label} <span class="n">${LEADS.filter(SMART[k].f).length}</span>`)).join('');
 const l=visible();
 $('content').innerHTML=view==='board'?boardHtml(l):listHtml(l);
 $('v-list').setAttribute('aria-pressed',view==='list');$('v-board').setAttribute('aria-pressed',view==='board');
 document.querySelectorAll('.sel').forEach(e=>e.classList.remove('sel'));
 if(sel!=null){const e=document.querySelector(`[data-id="${sel}"].lrow,[data-id="${sel}"].bcard`);if(e)e.classList.add('sel')}
}
const statusPill=(l)=>`<button class="pill" data-stmenu="${l.id}" aria-label="Status: ${esc(l.status)}. Change"><span class="dot" style="background:${col(l.status)}"></span>${esc(l.status)}</button>`;
const tierChip=l=>{const t=tierOf(l);return t?`<span class="tier ${t}">Tier ${t.toUpperCase()}</span>`:''};
function listHtml(l){
 if(!l.length)return `<div class="empty"><b>No leads match</b><span>Try a different search or clear the filters.</span><button class="btn ghost" id="clear">Clear filters</button></div>`;
 const H=(k,t)=>`<button data-sort="${k}">${t}${sort.k===k?(sort.d>0?' ↑':' ↓'):''}</button>`;
 return `<div class="pane"><div class="lhead"><div>${H('company','Company')}</div><div>${H('loc','Location')}</div><div>${H('tier','Tier')}</div><div>${H('status','Status')}</div><div>${H('updated','Touched')}</div><div>Email</div></div>`+
 l.map(x=>`<div class="swipe" data-id="${x.id}"><div class="sbg"><span class="l"></span><span class="r"></span></div>
  <div class="lrow" data-id="${x.id}" tabindex="0">
   <div class="co c-co"><span class="av">${esc((x.company||'?').charAt(0).toUpperCase())}</span><span class="nm">${esc(x.company)}${warnOf(x)?'<span class="warnmark" title="Has a warning note">⚠</span>':''}</span></div>
   <div class="cell c-loc">${esc(loc(x))}</div><div class="c-tier">${tierChip(x)}</div><div class="c-st">${statusPill(x)}</div>
   <div class="cell c-age" title="${esc(touched(x))}">${ago(touched(x))}</div><div class="cell c-mail">${esc(x.email)}</div>
   <div class="c-meta cell">${esc(loc(x))} ${tierChip(x)} <span>${ago(touched(x))}</span></div>
  </div></div>`).join('')+'</div>';
}
function boardHtml(l){
 return `<div class="board">`+ST.map(s=>{const c=l.filter(x=>x.status===s);
  return `<section class="col" data-col="${s}" aria-label="${s}"><div class="colh"><span class="dot" style="background:${col(s)}"></span>${s}<span class="n">${c.length}</span></div>
  <div class="colb">`+(c.map(x=>`<div class="bcard" draggable="true" data-id="${x.id}" tabindex="0"><div class="t"><span class="nm">${esc(x.company)}</span>${warnOf(x)?'<span class="warnmark">⚠</span>':''}<span style="margin-left:auto">${tierChip(x)}</span></div>
   <div class="m">${esc(loc(x))||'—'} · ${ago(touched(x))}</div></div>`).join('')||'<div class="colempty">Nothing here</div>')+`</div></section>`}).join('')+'</div>';
}

/* ---- drawer ------------------------------------------------------------------------- */
function renderDrawer(){
 const d=$('drawer'),inner=$('dinner'),x=LEADS.find(l=>l.id===openId);
 if(!x){d.classList.remove('open');return}
 const draft=(inner.querySelector('#nt')||{}).value||'';
 const mail=EMAILRE.test(String(x.email||''));
 const link=(u,t)=>isUrl(u)?`<a href="${esc(u)}" target="_blank" rel="noopener">${t}</a>`:'';
 inner.innerHTML=`<div class="dtop"><span class="av">${esc((x.company||'?').charAt(0).toUpperCase())}</span><h2>${esc(x.company)}${warnOf(x)?'<span class="warnmark">⚠</span>':''}</h2>
   <button class="iconbtn" data-close aria-label="Close details"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M6 18 18 6M6 6l12 12"/></svg></button></div>
  <div class="stepper" role="group" aria-label="Status">${ST.map((s,i)=>`<button data-set="${s}" aria-pressed="${x.status===s}" title="Press ${i+1}"><span class="dot" style="background:${col(s)}"></span>${s}</button>`).join('')}</div>
  <div class="acts">${mail?`<a class="btn primary" href="mailto:${esc(x.email)}?subject=${encodeURIComponent('UGC creators für '+x.company)}">✉ Email</a><button class="btn ghost" data-copy="${esc(x.email)}">Copy email</button>`:''}${link(x.linkedin,'').replace('<a ','<a class="btn ghost" ').replace('></a>','>in LinkedIn</a>')}${link(x.source_url,'').replace('<a ','<a class="btn ghost" ').replace('></a>','>↗ Source</a>')}</div>
  <div class="sec">Brief</div><div class="brief">${esc(x.need)||'<i style="color:var(--faint)">No need recorded</i>'}</div>
  <div class="sec">Details</div><dl class="kv"><dt>Email</dt><dd>${esc(x.email)}</dd><dt>Location</dt><dd>${esc(loc(x))||'—'}</dd><dt>Category</dt><dd>${esc(x.category)||'—'}</dd><dt>Tier</dt><dd>${tierChip(x)||'—'}</dd><dt>Added</dt><dd>${esc(String(x.created||'').slice(0,10))||'—'}</dd><dt>Last touched</dt><dd>${ago(touched(x))}</dd></dl>
  <div class="sec">Notes</div>
  <textarea id="nt" rows="2" maxlength="800" placeholder="Add a note…  (Ctrl+Enter to save)" aria-label="New note"></textarea>
  <div style="display:flex;justify-content:flex-end;margin-top:.375rem"><button class="btn ghost" data-note>Save note</button></div>
  <div class="tl">${[...(x.notes||[])].reverse().map(n=>`<div><time>${esc(String(n.ts||'').slice(0,16).replace('T',' '))}</time><span>${esc(n.text)}</span></div>`).join('')||'<span style="color:var(--faint)">No notes yet</span>'}</div>
  <div class="foot"><span>Keys: j/k next · 1–6 status · Esc close</span><button class="btn danger" data-del>Delete lead…</button></div>`;
 const nt=inner.querySelector('#nt');if(draft)nt.value=draft;
 d.classList.add('open');
}
function openLead(id){sel=id;openId=id;render();renderDrawer();$('dinner').scrollTop=0}
function closeDrawer(){openId=null;$('drawer').classList.remove('open');render()}

/* ---- actions ------------------------------------------------------------------------ */
let UNDO=null;
function toast(text,undo){const t=$('toast');t.innerHTML=`<span>${esc(text)}</span>${undo?'<button id="tundo">Undo</button>':''}`;t.classList.add('show');
 clearTimeout(UNDO&&UNDO.t);UNDO={undo,t:setTimeout(()=>{t.classList.remove('show');UNDO=null},6000)}}
async function setStatus(id,status){
 const l=LEADS.find(x=>x.id===id);if(!l||l.status===status)return;
 const old=l.status;l.status=status;render();if(openId===id)renderDrawer();
 try{await post('/api/status',{id,status});
  toast(`${l.company}: ${status}`,async()=>{l.status=old;render();if(openId===id)renderDrawer();
   try{await post('/api/status',{id,status:old})}catch(e){l.status=status;render();toast('Undo failed')}});
 }catch(e){l.status=old;render();if(openId===id)renderDrawer();toast('Status change failed: '+e.message)}
}
async function saveNote(){const t=($('nt')||{}).value;if(!t||!t.trim())return;
 try{await post('/api/note',{id:openId,text:t.trim()});$('nt').value='';await load();renderDrawer()}catch(e){toast('Note failed: '+e.message)}}
function dialog(html){$('dlg').innerHTML=html;$('ov').classList.add('show');const f=$('dlg').querySelector('input,textarea');if(f)setTimeout(()=>f.focus(),50)}
function closeDialog(){$('ov').classList.remove('show')}
function addDialog(){
 const fld=(i,l,x='')=>`<label for="f_${i}">${l}</label><input class="f" id="f_${i}" ${x}>`;
 dialog(`<h3>Add lead</h3><form id="addf" autocomplete="off">${fld('company','Company *','maxlength="120" required')}${fld('email','Email *','type="email" maxlength="200" required')}
  <div class="row"><div>${fld('city','City','maxlength="80"')}</div><div>${fld('country','Country','maxlength="40" placeholder="DE"')}</div></div>
  <label for="f_need">What are they looking for</label><textarea id="f_need" rows="3" maxlength="600"></textarea>
  ${fld('category','Category','maxlength="200"')}${fld('linkedin','LinkedIn URL','maxlength="250" inputmode="url"')}${fld('source_url','Source URL','maxlength="300" inputmode="url"')}
  <div class="btns"><button type="button" class="btn ghost" data-dclose>Cancel</button><button class="btn primary">Add lead</button></div></form>`);
 $('addf').onsubmit=async e=>{e.preventDefault();const g=i=>$('f_'+i).value.trim();
  try{await post('/api/add',{company:g('company'),email:g('email'),city:g('city'),country:g('country'),need:g('need'),category:g('category'),linkedin:g('linkedin'),source_url:g('source_url')});closeDialog();await load();toast('Lead added')}
  catch(err){toast('Add failed: '+err.message)}};
}
function delDialog(id){const l=LEADS.find(x=>x.id===id);if(!l)return;
 dialog(`<h3>Delete lead?</h3><p style="color:var(--mut);margin:0">“${esc(l.company)}” will be removed. This cannot be undone.</p>
  <div class="btns"><button class="btn ghost" data-dclose>Cancel</button><button class="btn danger" id="dgo" style="box-shadow:inset 0 0 0 1px var(--err)">Delete</button></div>`);
 $('dgo').onclick=async()=>{try{await post('/api/del',{id});closeDialog();closeDrawer();await load();toast('Lead deleted')}catch(e){toast('Delete failed: '+e.message)}};}
function keysDialog(){dialog(`<h3>Keyboard shortcuts</h3><div class="sc"><b>/</b>Search<b>j / k</b>Next / previous lead<b>Enter</b>Open details<b>1 – 6</b>Set status (new … lost)<b>b</b>Toggle List / Board<b>n</b>Add a lead<b>e</b>Email the selected lead<b>Esc</b>Close / clear<b>?</b>This help</div><div class="btns"><button class="btn ghost" data-dclose>Close</button></div>`)}
function popup(anchor,html){closePop();const r=anchor.getBoundingClientRect(),p=document.createElement('div');p.className='pop';p.id='pop';p.innerHTML=html;document.body.appendChild(p);
 const w=p.offsetWidth,h=p.offsetHeight;p.style.left=Math.max(4,Math.min(innerWidth-w-4,r.left))+'px';p.style.top=(r.bottom+h+8>innerHeight?Math.max(4,r.top-h-4):r.bottom+4)+'px'}
function closePop(){const p=$('pop');if(p)p.remove()}
function statusMenu(a,id){const l=LEADS.find(x=>x.id===id);
 popup(a,ST.map((s,i)=>`<button data-pset="${s}" data-pid="${id}"><span class="dot" style="background:${col(s)}"></span>${s}<span class="k">${l&&l.status===s?'✓':i+1}</span></button>`).join(''))}
function moreMenu(a){popup(a,`<a href="${BP}/export.csv" download>⬇ Export CSV</a><button data-keys>⌨ Shortcuts<span class="k">?</span></button><button data-logout>Log out</button>`)}

/* ---- events ------------------------------------------------------------------------- */
document.addEventListener('click',e=>{
 const t=e.target;
 const pop=t.closest('#pop');
 if(!pop)closePop();
 let b;
 if((b=t.closest('[data-pset]'))){closePop();setStatus(Number(b.dataset.pid),b.dataset.pset);return}
 if(t.closest('[data-keys]')){closePop();keysDialog();return}
 if(t.closest('[data-logout]')){fetch(BP+'/logout',{credentials:'same-origin'}).then(()=>location.reload());return}
 if(t.id==='tundo'&&UNDO){const f=UNDO.undo;clearTimeout(UNDO.t);$('toast').classList.remove('show');UNDO=null;f&&f();return}
 if(t.id==='ov'||t.closest('[data-dclose]')){closeDialog();return}
 if((b=t.closest('[data-sf]'))){statusF=b.dataset.sf;render();return}
 if((b=t.closest('[data-sm]'))){smart=smart===b.dataset.sm?null:b.dataset.sm;render();return}
 if((b=t.closest('[data-sort]'))){const k=b.dataset.sort;sort=sort.k===k?{k,d:-sort.d}:{k,d:k==='company'||k==='loc'||k==='status'||k==='tier'?1:-1};render();return}
 if(t.id==='clear'){statusF='all';smart=null;$('q').value='';render();return}
 if((b=t.closest('[data-stmenu]'))){e.stopPropagation();statusMenu(b,Number(b.dataset.stmenu));return}
 if(t.closest('#btn-add')){addDialog();return}
 if((b=t.closest('#btn-more'))){e.stopPropagation();moreMenu(b);return}
 if(t.closest('#v-list')){view='list';store('crm.view','list');render();return}
 if(t.closest('#v-board')){view='board';store('crm.view','board');render();return}
 if(t.closest('[data-close]')){closeDrawer();return}
 if((b=t.closest('[data-set]'))){setStatus(openId,b.dataset.set);return}
 if((b=t.closest('[data-copy]'))){navigator.clipboard&&navigator.clipboard.writeText(b.dataset.copy).then(()=>toast('Email copied'),()=>toast('Copy failed'));return}
 if(t.closest('[data-note]')){saveNote();return}
 if(t.closest('[data-del]')){delDialog(openId);return}
 if(t.closest('a'))return;
 if((b=t.closest('.lrow,.bcard'))){if(SWCLICK)return;openLead(Number(b.dataset.id))}
});
$('q').addEventListener('input',render);
document.addEventListener('keydown',e=>{
 const tag=(e.target.tagName||'').toLowerCase(),typing=tag==='input'||tag==='textarea'||tag==='select';
 if(e.key==='Escape'){if($('ov').classList.contains('show'))closeDialog();else if($('pop'))closePop();else if(typing){e.target.blur()}else if(openId!=null)closeDrawer();return}
 if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&e.target.id==='nt'){e.preventDefault();saveNote();return}
 if(typing||e.ctrlKey||e.metaKey||e.altKey||$('ov').classList.contains('show'))return;
 const ids=visible().map(x=>x.id),i=ids.indexOf(sel);
 if(e.key==='/'){e.preventDefault();$('q').focus();return}
 if(e.key==='?'){keysDialog();return}
 if(e.key==='n'){e.preventDefault();addDialog();return}
 if(e.key==='b'){view=view==='list'?'board':'list';store('crm.view',view);render();return}
 if(e.key==='j'||e.key==='ArrowDown'||e.key==='k'||e.key==='ArrowUp'){e.preventDefault();if(!ids.length)return;
  const n=ids[Math.max(0,Math.min(ids.length-1,(i<0?-1:i)+((e.key==='j'||e.key==='ArrowDown')?1:-1)))];
  sel=n;if(openId!=null){openId=n;renderDrawer()}render();const el=document.querySelector(`[data-id="${n}"].lrow,[data-id="${n}"].bcard`);if(el)el.scrollIntoView({block:'nearest'});return}
 if(e.key==='Enter'&&sel!=null){openLead(sel);return}
 if(/^[1-6]$/.test(e.key)&&sel!=null){setStatus(sel,ST[Number(e.key)-1]);return}
 if(e.key==='e'&&sel!=null){const l=LEADS.find(x=>x.id===sel);if(l&&EMAILRE.test(String(l.email||'')))location.href='mailto:'+l.email+'?subject='+encodeURIComponent('UGC creators für '+l.company)}
});
/* board drag & drop */
let dragId=null;
document.addEventListener('dragstart',e=>{const c=e.target.closest&&e.target.closest('.bcard');if(!c)return;dragId=Number(c.dataset.id);c.classList.add('drag');e.dataTransfer.effectAllowed='move';try{e.dataTransfer.setData('text/plain',String(dragId))}catch(_){}});
document.addEventListener('dragend',()=>{dragId=null;document.querySelectorAll('.drag,.over').forEach(x=>x.classList.remove('drag','over'))});
document.addEventListener('dragover',e=>{const c=e.target.closest&&e.target.closest('.col');if(!c||dragId==null)return;e.preventDefault();document.querySelectorAll('.col.over').forEach(x=>x!==c&&x.classList.remove('over'));c.classList.add('over')});
document.addEventListener('drop',e=>{const c=e.target.closest&&e.target.closest('.col');if(!c||dragId==null)return;e.preventDefault();const id=dragId;dragId=null;setStatus(id,c.dataset.col)});
/* swipe to change status (touch/pen only; mouse uses the status pill, board or keys 1-6) */
const FLOW=['lost','new','contacted','replied','meeting','won'];
const SW={on:false,id:null,x0:0,y0:0,dx:0,lock:null,row:null,wrap:null};let SWCLICK=false;
const TH=()=>Math.min(96,Math.round(innerWidth*.28));
const swT=(c,d)=>{let i=FLOW.indexOf(c);if(i<0)i=1;const j=i+d;return j<0||j>=FLOW.length?null:FLOW[j]};
function swReset(){if(SW.wrap){SW.wrap.classList.add('anim');const b=SW.wrap.querySelector('.sbg');if(b)b.classList.remove('vis')}if(SW.row)SW.row.style.transform=''}
document.addEventListener('pointerdown',e=>{if(e.pointerType==='mouse')return;if(e.target.closest('button,a,input,textarea,select'))return;
 const row=e.target.closest('.lrow');if(!row)return;const wrap=row.closest('.swipe');if(!wrap)return;
 Object.assign(SW,{on:true,id:e.pointerId,x0:e.clientX,y0:e.clientY,dx:0,lock:null,row,wrap});wrap.classList.remove('anim')});
document.addEventListener('pointermove',e=>{if(!SW.on||e.pointerId!==SW.id)return;const dx=e.clientX-SW.x0,dy=e.clientY-SW.y0;
 if(SW.lock===null){if(Math.abs(dx)<8&&Math.abs(dy)<8)return;SW.lock=Math.abs(dx)>Math.abs(dy)?'x':'y';if(SW.lock==='x'){try{SW.row.setPointerCapture(e.pointerId)}catch(_){}}}
 if(SW.lock!=='x')return;SW.dx=dx;const l=LEADS.find(x=>x.id===Number(SW.wrap.dataset.id));if(!l)return;
 const dir=dx>0?1:-1,tgt=swT(l.status,dir),b=SW.wrap.querySelector('.sbg'),armed=Math.abs(dx)>=TH()&&!!tgt;
 SW.row.style.transform='translateX('+Math.max(-150,Math.min(150,dx))+'px)';b.classList.add('vis');
 b.style.background=!tgt?'#555':(dir>0?(armed?'#1b5e2a':'#16401e'):(armed?'#92400e':'#5b2a1a'));
 b.querySelector('.l').textContent=dir>0&&tgt?(armed?'▶ '+tgt:tgt):'';b.querySelector('.r').textContent=dir<0&&tgt?(armed?tgt+' ◀':tgt):''});
function swEnd(e){if(!SW.on||(e&&e.pointerId!==SW.id))return;SW.on=false;const{wrap,row,dx,lock}=SW;if(lock!=='x'){swReset();return}
 SWCLICK=true;setTimeout(()=>SWCLICK=false,350);const l=LEADS.find(x=>x.id===Number(wrap.dataset.id)),tgt=l?swT(l.status,dx>0?1:-1):null;
 if(!l||!tgt||Math.abs(dx)<TH()){swReset();return}wrap.classList.add('anim');row.style.transform='';setStatus(l.id,tgt)}
document.addEventListener('pointerup',swEnd);document.addEventListener('pointercancel',swEnd);
async function load(){try{LEADS=await api(BP+'/api/leads');render();if(openId!=null)renderDrawer()}catch(e){toast('Load failed: '+e.message)}}
view=stored('crm.view')==='board'?'board':'list';
load();
</script></body></html>"""

LOGIN = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>__APPNAME__ — sign in</title><style>
:root{--bg:#171717;--txt:#e3e3e3;--mut:#9b9b9b;--faint:#676767;--line:#262626;--soft:rgba(255,255,255,.045);--err:#f87171;color-scheme:dark}
:root.light{--bg:#fff;--txt:#333;--mut:#6b6b6b;--faint:#9b9b9b;--line:#ececec;--soft:rgba(0,0,0,.035);color-scheme:light}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--txt);
 font:14px/1.25rem -apple-system,"Segoe UI",Roboto,Inter,system-ui,sans-serif;padding:1rem}
.box{width:min(100%,22rem)}
h1{font-size:1rem;font-weight:500;margin:0 0 .25rem}
p{color:var(--mut);margin:0 0 1rem}
input{width:100%;font:inherit;font-size:1rem;color:var(--txt);background:var(--soft);border:1px solid transparent;border-radius:.75rem;padding:.625rem .875rem;outline:0}
input:focus{border-color:var(--line);background:transparent}
button{width:100%;margin-top:.625rem;height:2.5rem;border:0;border-radius:.75rem;background:var(--txt);color:var(--bg);font:inherit;font-weight:500;cursor:pointer}
button:hover{opacity:.88}
#e{display:none;color:var(--err);margin-top:.625rem}
</style></head><body><div class="box"><h1>__APPNAME__ 🔒</h1>
<p>Private outreach CRM — Berlin SMEs looking for UGC. Enter the secret code.</p>
<form method="post" action="__BP__/login"><input name="code" type="password" autofocus autocomplete="current-password" aria-label="Secret code">
<button>Unlock</button></form><div id="e" role="alert">Wrong code — try again.</div></div>
<script>
if(location.search.includes('e=1'))document.getElementById('e').style.display='block';
(function(){var P='https://ui.techno-wanderlust.com',V=['bg','txt','mut','faint','line','soft','err'],S=/^[#a-zA-Z0-9(),.%\/ \-]{1,80}$/;
 function apply(t){if(!t)return;document.documentElement.classList.toggle('light',t.dark===false);
  V.forEach(function(k){var v=t.vars&&t.vars[k];if(typeof v==='string'&&S.test(v))document.documentElement.style.setProperty('--'+k,v)});
  try{localStorage.setItem('crm.theme',JSON.stringify(t))}catch(_){}}
 try{var s=JSON.parse(localStorage.getItem('crm.theme')||'null');if(s)apply(s);else if(matchMedia('(prefers-color-scheme: light)').matches)document.documentElement.classList.add('light')}catch(_){}
 if(window.self!==window.top){addEventListener('message',function(e){if(e.origin===P&&e.data&&e.data.type==='ui-theme')apply(e.data)});
  try{parent.postMessage({type:'crm-ready'},P)}catch(_){}}})();
</script>
</body></html>"""

HEADERS = {"Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                     "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                                     "frame-ancestors 'self' https://ui.techno-wanderlust.com; form-action 'self'",
           "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
           "Cache-Control": "no-store",
           "Permissions-Policy": "geolocation=(), microphone=(), camera=()"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def ip(self):
        return self.client_address[0]

    def do_HEAD(self):
        # HEAD must behave like GET minus the body, or proxies/health checks
        # that send HEAD get a 501 from BaseHTTPRequestHandler.
        self._bodyless = True
        self.do_GET()

    def send(self, code, body=b"", ctype="text/html; charset=utf-8", extra=None):
        if getattr(self, "_bodyless", False):
            body = b""
        self.send_response(code)
        for k, v in HEADERS.items():
            self.send_header(k, v)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def sid_ok(self):
        sid = self.cookie().get("bs_s")
        if not sid:
            return False
        s = sessions().get(sid)
        return bool(s) and s.get("exp", 0) > time.time()

    def cookie(self):
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k:
                out[k] = v
        return out

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 65536:
                return None
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def authed_or(self, fn):
        if not self.sid_ok():
            audit(self.ip(), "NO-SESSION", self.headers.get("User-Agent", ""))
            return self.send(401, b"unauthorized - sign in again", "text/plain")
        return fn()

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if BASE_PATH and p.startswith(BASE_PATH):
            p = p[len(BASE_PATH):] or "/"  # base-path aware
        if p == "/healthz":
            return self.send(200, b"ok", "text/plain")
        if p.startswith("/api/"):
            # API endpoints must never return the login HTML page with 200:
            # the JS treats 401 as 'sign in again', anything else as data.
            if not self.sid_ok():
                audit(self.ip(), "NO-SESSION", self.headers.get("User-Agent", ""))
                return self.send(401, b'{"error":"unauthorized"}', "application/json")
            if p == "/api/leads":
                return self.send(200, json.dumps(load(), ensure_ascii=False).encode(), "application/json")
            return self.send(404, b'{"error":"not found"}', "application/json")
        if not self.sid_ok():
            audit(self.ip(), "LOGIN-PAGE", self.headers.get("User-Agent", ""))
            return self.send(200, LOGIN.replace("__BP__", BASE_PATH)
                                   .replace("__APPNAME__", APPNAME).encode())
        if p == "/" or p == "/index.html":
            return self.send(200, PAGE.replace("__BP__", BASE_PATH)
                                  .replace("__APPNAME__", APPNAME).encode())
        if p == "/export.csv":
            leads = load()
            buf = io.StringIO()
            w = csv.writer(buf)
            fields = ["id", "company", "country", "city", "email", "need", "category",
                      "linkedin", "source_url", "status", "created", "updated"]
            w.writerow(fields)
            for l in leads:
                row = []
                for k in fields[:-1]:
                    v = str(l.get(k, "") or "")
                    row.append("'" + v if v[:1] in "=+-@" else v)
                w.writerow(row + [str(l.get("updated", ""))])
            data = buf.getvalue().encode("utf-8-sig")
            return self.send(200, data, "text/csv; charset=utf-8",
                             {"Content-Disposition": 'attachment; filename="berlin-crm-v2.csv"'})
        return self.send(404, b"not found", "text/plain")

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if BASE_PATH and p.startswith(BASE_PATH):
            p = p[len(BASE_PATH):] or "/"  # base-path aware
        if p == "/logout":
            c = self.cookie()
            s = sessions()
            s.pop(c.get("bs_s", ""), None)
            json.dump(s, open(SESS_FILE, "w"))
            return self.send(200, b"ok", "text/plain",
                             {"Set-Cookie": f"bs_s=; HttpOnly; SameSite=Strict; Path={BASE_PATH}; Max-Age=0"})
        if p == "/login":
            if limited(self.ip(), fail=True):
                audit(self.ip(), "LOCKOUT", self.headers.get("User-Agent", ""))
                return self.send(429, b"too many attempts - locked out for a bit", "text/plain")
            ctype = (self.headers.get("Content-Type") or "").lower()
            if "application/json" in ctype:
                body = self.read_json() or {}
                code = str(body.get("code", ""))
                is_form = False
            else:
                # native HTML form fallback (works even with JS disabled)
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    form = urllib.parse.parse_qs(self.rfile.read(min(n, 4096)).decode("utf-8", "replace"))
                except Exception:
                    form = {}
                code = (form.get("code", [""])[0] or "")[:200]
                is_form = True
            ok = bool(code) and hmac.compare_digest(
                hashlib.sha256(code.encode()).hexdigest(), code_hash())
            audit(self.ip(), "LOGIN-OK" if ok else "LOGIN-FAIL", self.headers.get("User-Agent", ""))
            if not ok:
                if is_form:
                    return self.send(303, b"", "text/plain", {"Location": BASE_PATH + "/?e=1"})
                return self.send(403, b"wrong code", "text/plain")
            s = sessions()
            sid = secrets.token_urlsafe(24)
            s[sid] = {"exp": time.time() + 60 * 60 * 24 * 30, "ip": self.ip()}
            json.dump(s, open(SESS_FILE, "w"))
            cookie = {"Set-Cookie": f"bs_s={sid}; HttpOnly; SameSite=Strict; Path={BASE_PATH}; Max-Age=2592000; Secure"}
            if is_form:
                return self.send(303, b"", "text/plain", {**cookie, "Location": BASE_PATH + "/"})
            return self.send(200, b'{"ok":1}', "application/json", cookie)
        if not self.sid_ok():
            return self.send(401, b"unauthorized - sign in again", "text/plain")
        if limited(self.ip()):
            return self.send(429, b"slow down", "text/plain")
        leads = load()
        d = self.read_json()
        if d is None:
            return self.send(413, b"payload too large", "text/plain")

        if p == "/api/add":
            company = clean(d.get("company"), "company")
            email = clean(d.get("email"), "email")
            if not company or not EMAIL.match(email):
                return self.send(400, b"company + valid email required", "text/plain")
            dup = next((l for l in leads if l.get("company", "").lower() == company.lower()
                        and l.get("email", "").lower() == email.lower()), None)
            if dup:
                return self.send(409, b"already exists", "text/plain")
            l = {"id": max([x.get("id", 0) for x in leads] + [0]) + 1,
                 "company": company, "email": email,
                 "country": clean(d.get("country"), "country"), "city": clean(d.get("city"), "city"),
                 "need": clean(d.get("need"), "need"), "category": clean(d.get("category"), "category"),
                 "linkedin": url_ok(d.get("linkedin")), "source_url": url_ok(d.get("source_url")),
                 "status": "new", "created": now(), "updated": now(),
                 "notes": [{"ts": now(), "text": "added via web"}]}
            leads.append(l)
            save(leads)
            return self.send(200, json.dumps({"ok": 1, "id": l["id"]}).encode(), "application/json")

        if p in ("/api/status", "/api/note", "/api/del"):
            try:
                lid = int(d.get("id", -1))
            except Exception:
                lid = -1
            idx = next((i for i, x in enumerate(leads) if x.get("id") == lid), None)
            if idx is None:
                return self.send(404, b"no lead", "text/plain")
            if p == "/api/status":
                st = str(d.get("status", "")).lower()
                if st not in STATUSES:
                    return self.send(400, b"bad status", "text/plain")
                leads[idx]["status"] = st
                if clean(d.get("note"), "note"):
                    leads[idx].setdefault("notes", []).append({"ts": now(), "text": clean(d.get("note"), "note")})
            elif p == "/api/note":
                txt = clean(d.get("text"), "note")
                if not txt:
                    return self.send(400, b"empty note", "text/plain")
                leads[idx].setdefault("notes", []).append({"ts": now(), "text": txt})
            if p == "/api/del":
                leads.pop(idx)
                save(leads)
                return self.send(200, b'{"ok":1}', "application/json")
            leads[idx]["updated"] = now()
            save(leads)
            return self.send(200, b'{"ok":1}', "application/json")
        return self.send(404, b"not found", "text/plain")


if __name__ == "__main__":
    code_hash()  # fail fast if not set up
    print(f"{APPNAME} on http://127.0.0.1:{PORT} (base path {BASE_PATH})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

/*
 * kanban-panel.js — "Kanban" sidebar panel (#kanban) for Open WebUI 0.11.4.
 * Live view of the Hermes crew board (scout / scribe / ops) with drag-and-drop, a new-card dialog,
 * a card drawer and a chat dock that talks to the Hermes agent.
 * Registers itself via window.__cvRegisterPanel('kanban', ...) exposed by crons-tab.js.
 * Data: /kanban-api/* (host service kanban-board.service via Caddy; admin session required).
 * Plain DOM, textContent only (never innerHTML with data). Colours come from the shared --cv-* vars.
 */
(function () {
	'use strict';
	if (window.__kanbanPanelLoaded) return;
	window.__kanbanPanelLoaded = true;

	var API = '/kanban-api';
	var POLL_MS = 5000;
	var CHAT_KEY = 'kanban-chat-v1';

	var CREW = [
		{ key: 'scout', role: 'Research & leads' },
		{ key: 'scribe', role: 'Drafts only' },
		{ key: 'ops', role: 'Server checks' }
	];
	var COLS = [
		{ key: 'parked', label: 'Parked', hint: 'Held back. Nothing runs until you queue it.', empty: 'Nothing parked' },
		{ key: 'ready', label: 'Queued', hint: 'Next up. A worker picks these up within about 30 seconds.', empty: 'Nothing queued' },
		{ key: 'running', label: 'Running', hint: 'A worker is on it right now. Running cards cannot be moved.', empty: 'No worker is busy' },
		{ key: 'blocked', label: 'On hold', hint: 'Stopped, or failed. Needs you.', empty: 'Nothing needs you' },
		{ key: 'done', label: 'Done', hint: 'Finished. Shows the latest 30.', empty: 'Nothing finished yet' }
	];
	var MOVES = {
		parked: ['ready', 'done', 'archive'],
		ready: ['parked', 'blocked', 'done', 'archive'],
		blocked: ['ready', 'parked', 'done', 'archive'],
		running: [],
		done: ['archive']
	};
	var TARGET = { parked: 'Parked', ready: 'Queued', blocked: 'On hold', done: 'Done', archive: 'Archive' };
	var STATUS_FOR = { parked: 'scheduled', ready: 'ready', blocked: 'blocked', done: 'done' };
	var SUGGEST = [
		'What is on the board right now?',
		'Why is anything on hold?',
		'Put scout on: find 10 Berlin cafés that need a website'
	];

	var ICON_PLUS = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.75" stroke="currentColor" stroke-linecap="round" aria-hidden="true" class="cv-ico"><path d="M12 5v14M5 12h14"></path></svg>';
	var ICON_CHAT = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="cv-ico"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.9 8.9 0 0 1-3.6-.8L3 20l1.1-4.6A8.3 8.3 0 0 1 3 11.5 8.5 8.5 0 0 1 12 3a8.5 8.5 0 0 1 9 8.5Z"></path></svg>';
	var ICON_DOTS = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" class="cv-ico"><circle cx="5.5" cy="12" r="1.6"></circle><circle cx="12" cy="12" r="1.6"></circle><circle cx="18.5" cy="12" r="1.6"></circle></svg>';
	var ICON_SEND = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.75" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="cv-ico"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"></path></svg>';
	var ICON_STOP = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" class="cv-ico"><rect x="6.5" y="6.5" width="11" height="11" rx="2"></rect></svg>';
	var ICON_BOX = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="cv-ico"><path d="M3.75 7.5h16.5v3H3.75zM5.25 10.5v8.25a1.5 1.5 0 0 0 1.5 1.5h10.5a1.5 1.5 0 0 0 1.5-1.5V10.5M10 14h4"></path></svg>';
	var ICON_TRASH = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="cv-ico"><path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l.8 12h9.4l.8-12M10 11v5M14 11v5"></path></svg>';

	var CSS =
		'.kb-view{--kb-card:#fff;--kb-inv:#fff;--kb-shadow:0 1px 2px rgb(0 0 0 / .06),0 0 0 1px var(--cv-line);' +
		'--kb-scout:hsl(212 88% 52%);--kb-scribe:hsl(268 74% 58%);--kb-ops:hsl(30 92% 48%);--kb-analyst:hsl(168 70% 38%)}' +
		'html.dark .kb-view{--kb-card:rgb(255 255 255 / .065);--kb-inv:var(--color-gray-900);--kb-shadow:0 0 0 1px rgb(255 255 255 / .07);' +
		'--kb-scout:hsl(212 92% 66%);--kb-scribe:hsl(268 85% 74%);--kb-ops:hsl(34 95% 60%);--kb-analyst:hsl(168 62% 52%)}' +
		'.kb-main{flex:1;min-height:0;display:flex;position:relative}' +
		'.kb-left{flex:1;min-width:0;display:flex;flex-direction:column}' +
		'.kb-btn{display:inline-flex;align-items:center;gap:.375rem;height:1.875rem;padding:0 .75rem!important;border-radius:.625rem;font-size:.8125rem;' +
		'background:var(--cv-hover);color:var(--cv-name)!important;transition:background-color .15s,opacity .15s}' +
		'.kb-btn:hover{background:var(--cv-mono-bg);filter:brightness(.97)}' +
		'.kb-btn.primary{background:var(--cv-name);color:var(--kb-inv)!important}.kb-btn.primary:hover{opacity:.88;filter:none}' +
		'.kb-btn:disabled{opacity:.45;cursor:default}' +
		'.kb-btn:focus-visible,.kb-card:focus-visible,.kb-more:focus-visible,.kb-opt:focus-visible{outline:2px solid var(--cv-faint);outline-offset:1px}' +
		'.kb-btn.on{background:var(--cv-mono-bg)}' +
		'.kb-pill{font-size:.75rem;line-height:1rem;padding:.0625rem .5rem;border-radius:9999px;white-space:nowrap}' +
		'.kb-pill.ok{background:var(--cv-ok-bg);color:var(--cv-ok-fg)}.kb-pill.warn{background:var(--cv-warn-bg);color:var(--cv-warn-fg)}.kb-pill.err{background:var(--cv-err-bg);color:var(--cv-err-fg)}' +
		'.kb-banner{margin:.25rem .625rem;padding:.5rem .75rem;border-radius:.75rem;background:var(--cv-warn-bg);color:var(--cv-warn-fg);font-size:.75rem}' +
		'.kb-banner.err{background:var(--cv-err-bg);color:var(--cv-err-fg)}' +
		/* crew strip */
		'.kb-crew{flex:none;display:flex;gap:.5rem;padding:.25rem .625rem .5rem;overflow-x:auto}' +
		'.kb-w{flex:none;display:flex;align-items:center;gap:.5rem;padding:.3125rem .625rem;border-radius:.75rem;background:var(--cv-mono-bg);font-size:.75rem;min-width:0;max-width:19rem}' +
		'.kb-w b{font-weight:500;color:var(--cv-name)}.kb-w span{color:var(--cv-meta);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
		'.kb-wd{flex:none;width:.5rem;height:.5rem;border-radius:9999px;background:var(--cv-faint);opacity:.6}' +
		'.kb-w.busy .kb-wd{background:var(--cv-ok);opacity:1;animation:kb-pulse 1.6s ease-in-out infinite}' +
		/* board */
		'.kb-board{flex:1;min-height:0;display:flex;gap:.625rem;padding:0 .625rem .625rem;overflow-x:auto;scroll-snap-type:x proximity}' +
		'.kb-col{flex:1 1 14rem;min-width:14rem;max-width:26rem;display:flex;flex-direction:column;border-radius:1rem;background:var(--cv-mono-bg);' +
		'transition:background-color .15s,box-shadow .15s,opacity .15s;scroll-snap-align:start}' +
		'.kb-col.ok{box-shadow:inset 0 0 0 1.5px var(--cv-faint)}' +
		'.kb-col.over{background:var(--cv-hover);box-shadow:inset 0 0 0 2px var(--cv-name)}' +
		'.kb-col.no{opacity:.45}' +
		'.kb-ch{flex:none;display:flex;align-items:center;gap:.5rem;padding:.625rem .75rem .375rem}' +
		'.kb-ch h2{margin:0;font-size:.8125rem;font-weight:600;color:var(--cv-name);letter-spacing:.005em}' +
		'.kb-cn{font-size:.6875rem;color:var(--cv-meta);padding:0 .375rem;border-radius:9999px;background:var(--cv-hover);min-width:1.25rem;text-align:center}' +
		'.kb-cd{flex:none;width:.5rem;height:.5rem;border-radius:9999px;background:var(--cv-faint)}' +
		'.kb-col[data-col=ready] .kb-cd{background:var(--kb-scout)}.kb-col[data-col=running] .kb-cd{background:var(--cv-ok);animation:kb-pulse 1.6s ease-in-out infinite}' +
		'.kb-col[data-col=blocked] .kb-cd{background:var(--cv-err)}.kb-col[data-col=done] .kb-cd{background:var(--cv-ok)}' +
		'.kb-ch .kb-add{margin-left:auto}' +
		'.kb-cb{flex:1;min-height:0;overflow-y:auto;padding:.125rem .5rem .5rem;display:flex;flex-direction:column;gap:.5rem}' +
		'.kb-empty{margin:.25rem 0;padding:1.25rem .5rem;text-align:center;font-size:.75rem;color:var(--cv-faint);border:1.5px dashed var(--cv-line);border-radius:.75rem}' +
		/* card */
		'.kb-card{position:relative;padding:.625rem .75rem .625rem .875rem;border-radius:.75rem;background:var(--kb-card);box-shadow:var(--kb-shadow);cursor:grab;' +
		'transition:transform .15s,box-shadow .15s,opacity .15s;outline:none;overflow:hidden;--kb-acc:var(--cv-faint)}' +
		'.kb-card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--kb-acc)}' +
		'.kb-card[data-who=scout]{--kb-acc:var(--kb-scout)}.kb-card[data-who=scribe]{--kb-acc:var(--kb-scribe)}.kb-card[data-who=ops]{--kb-acc:var(--kb-ops)}.kb-card[data-who=analyst]{--kb-acc:var(--kb-analyst)}' +
		'.kb-card:hover{transform:translateY(-1px);box-shadow:0 4px 14px rgb(0 0 0 / .1),0 0 0 1px var(--cv-line)}' +
		'.kb-card.lock{cursor:pointer}.kb-card.dragging{opacity:.4;transform:scale(.98)}' +
		'.kb-card.live::before{background:linear-gradient(180deg,var(--kb-acc),var(--cv-ok),var(--kb-acc));background-size:100% 300%;animation:kb-flow 2.4s linear infinite}' +
		'.kb-ct{display:flex;align-items:center;gap:.375rem;min-width:0}' +
		'.kb-who{font-size:.6875rem;font-weight:500;color:var(--kb-acc);letter-spacing:.01em}' +
		'.kb-age{margin-left:auto;font-size:.6875rem;color:var(--cv-faint);white-space:nowrap}' +
		'.kb-more{flex:none;width:1.5rem;height:1.5rem;display:flex;align-items:center;justify-content:center;border-radius:.5rem;color:var(--cv-meta);margin-right:-.25rem;opacity:.7}' +
		'.kb-more:hover{background:var(--cv-hover);opacity:1}' +
		'.kb-title{margin-top:.25rem;font-size:.8125rem;line-height:1.2rem;color:var(--cv-name);overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}' +
		'.kb-sum{margin-top:.25rem;font-size:.75rem;line-height:1.05rem;color:var(--cv-meta);overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}' +
		'.kb-sum.err{color:var(--cv-err-fg)}' +
		'.kb-cf{margin-top:.5rem;display:flex;flex-wrap:wrap;align-items:center;gap:.375rem;font-size:.6875rem;color:var(--cv-faint)}' +
		'.kb-id{font-family:var(--font-mono,ui-monospace,monospace)}' +
		'.kb-live{display:inline-flex;align-items:center;gap:.3125rem;color:var(--cv-ok-fg)}' +
		'.kb-live i{width:.4375rem;height:.4375rem;border-radius:9999px;background:var(--cv-ok);animation:kb-pulse 1.6s ease-in-out infinite}' +
		'.kb-bd{padding:0 .375rem;border-radius:.375rem;background:var(--cv-mono-bg);color:var(--cv-meta)}' +
		'.kb-bd.err{background:var(--cv-err-bg);color:var(--cv-err-fg)}.kb-bd.warn{background:var(--cv-warn-bg);color:var(--cv-warn-fg)}' +
		/* archive drop zone */
		'.kb-arch{position:absolute;left:50%;bottom:1.25rem;transform:translate(-50%,160%);z-index:5;padding:.625rem 1.5rem;border-radius:9999px;' +
		'background:var(--kb-card);box-shadow:0 8px 28px rgb(0 0 0 / .18),0 0 0 1.5px var(--cv-faint);font-size:.8125rem;color:var(--cv-meta);' +
		'transition:transform .2s,box-shadow .15s,color .15s;pointer-events:none}' +
		'.kb-view.dragging .kb-arch{transform:translate(-50%,0);pointer-events:auto}' +
		'.kb-arch.over{color:var(--cv-name);box-shadow:0 8px 28px rgb(0 0 0 / .22),0 0 0 2px var(--cv-name)}' +
		/* move menu */
		'.kb-menu{position:fixed;z-index:60;min-width:11rem;padding:.25rem;border-radius:.875rem;background:var(--kb-card);' +
		'box-shadow:0 10px 32px rgb(0 0 0 / .22),0 0 0 1px var(--cv-line);backdrop-filter:blur(8px)}' +
		'html.dark .kb-menu{background:var(--color-gray-850,#262626)}' +
		'.kb-menu h3{margin:.25rem .5rem;font-size:.6875rem;font-weight:500;color:var(--cv-faint)}' +
		'.kb-opt{display:block;width:100%;text-align:left;padding:.375rem .5rem!important;border-radius:.5rem;font-size:.8125rem;color:var(--cv-name)}' +
		'.kb-opt:hover:not(:disabled){background:var(--cv-hover)}.kb-opt:disabled{opacity:.35;cursor:default}' +
		/* modal */
		'.kb-ov{position:fixed;inset:0;z-index:55;display:flex;align-items:center;justify-content:center;padding:1rem;background:rgb(0 0 0 / .4);animation:kb-fade .15s}' +
		'.kb-dlg{width:min(34rem,100%);max-height:calc(100dvh - 2rem);overflow:auto;padding:1.125rem 1.25rem;border-radius:1.25rem;background:var(--kb-card);' +
		'box-shadow:0 20px 60px rgb(0 0 0 / .35);animation:kb-pop .18s}' +
		'html.dark .kb-dlg{background:var(--color-gray-900,#171717);box-shadow:0 20px 60px rgb(0 0 0 / .6),0 0 0 1px rgb(255 255 255 / .08)}' +
		'.kb-dlg h2{margin:0 0 .75rem;font-size:1.0625rem;font-weight:600;color:var(--cv-name)}' +
		'.kb-lbl{display:block;margin:.75rem 0 .3125rem;font-size:.75rem;color:var(--cv-meta)}' +
		'.kb-in{width:100%;padding:.5rem .625rem;border-radius:.75rem;border:1px solid var(--cv-line);background:transparent;color:var(--cv-name);font:inherit;font-size:.8125rem;outline:none;transition:border-color .15s}' +
		'.kb-in:focus{border-color:var(--cv-faint)}textarea.kb-in{resize:vertical;min-height:5.5rem;line-height:1.3rem}' +
		'.kb-seg{display:grid;grid-template-columns:repeat(3,1fr);gap:.375rem}' +
		'.kb-sg{padding:.5rem .625rem!important;border-radius:.75rem;text-align:left;border:1.5px solid var(--cv-line);transition:border-color .15s,background-color .15s}' +
		'.kb-sg b{display:flex;align-items:center;gap:.375rem;font-weight:500;color:var(--cv-name);font-size:.8125rem}.kb-sg span{display:block;font-size:.6875rem;color:var(--cv-meta);margin-top:.125rem}' +
		'.kb-sg:hover{background:var(--cv-hover)}.kb-sg[aria-checked=true]{border-color:var(--cv-name);background:var(--cv-hover)}' +
		'.kb-chk{display:flex;align-items:center;gap:.5rem;margin-top:.75rem;font-size:.8125rem;color:var(--cv-name);cursor:pointer}' +
		'.kb-note{margin-top:.75rem;font-size:.6875rem;line-height:1rem;color:var(--cv-faint)}' +
		'.kb-act{display:flex;justify-content:flex-end;gap:.5rem;margin-top:1rem}' +
		'.kb-err{margin-top:.625rem;font-size:.75rem;color:var(--cv-err-fg)}' +
		/* drawer */
		'.kb-dr-ov{position:fixed;inset:0;z-index:52;background:rgb(0 0 0 / .28);animation:kb-fade .15s}' +
		'.kb-dr{position:absolute;top:0;right:0;bottom:0;width:min(30rem,100%);overflow-y:auto;padding:1rem 1.25rem 2rem;background:var(--kb-card);' +
		'box-shadow:-12px 0 40px rgb(0 0 0 / .25);animation:kb-slide .2s}' +
		'html.dark .kb-dr{background:var(--color-gray-900,#171717)}' +
		'.kb-dr h2{margin:.5rem 0 .25rem;font-size:1.0625rem;line-height:1.5rem;font-weight:600;color:var(--cv-name);overflow-wrap:anywhere}' +
		'.kb-dr h3{margin:1.25rem 0 .375rem;font-size:.6875rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--cv-faint)}' +
		'.kb-dr-top{display:flex;align-items:center;gap:.5rem}' +
		'.kb-meta{display:flex;flex-wrap:wrap;gap:.375rem;align-items:center;font-size:.75rem;color:var(--cv-meta)}' +
		'.kb-box{padding:.625rem .75rem;border-radius:.75rem;background:var(--cv-mono-bg);font-size:.75rem;line-height:1.1rem;color:var(--cv-name);white-space:pre-wrap;overflow-wrap:anywhere;max-height:16rem;overflow:auto}' +
		'.kb-box.err{background:var(--cv-err-bg);color:var(--cv-err-fg)}' +
		'.kb-run{display:flex;gap:.5rem;padding:.375rem 0;border-bottom:1px solid var(--cv-line);font-size:.75rem}.kb-run:last-child{border:0}' +
		'.kb-run>span:first-child{flex:none;min-width:5.5rem;color:var(--cv-meta)}.kb-run>span:last-child{min-width:0;overflow-wrap:anywhere;color:var(--cv-name)}' +
		'.kb-cm{padding:.5rem .625rem;border-radius:.75rem;background:var(--cv-mono-bg);margin-bottom:.375rem;font-size:.75rem;line-height:1.1rem;color:var(--cv-name);overflow-wrap:anywhere;white-space:pre-wrap}' +
		'.kb-cm small{display:block;color:var(--cv-faint);font-size:.6875rem;margin-bottom:.125rem}' +
		'.kb-ev{display:flex;flex-wrap:wrap;gap:.25rem}' +
		'.kb-dr-act{display:flex;flex-wrap:wrap;gap:.375rem;margin-top:1rem}' +
		/* chat dock */
		'.kb-chat{flex:none;width:25rem;display:flex;flex-direction:column;border-left:1px solid var(--cv-line);min-height:0;background:transparent}' +
		'.kb-chat[hidden]{display:none}' +
		'.kb-chh{flex:none;display:flex;align-items:center;gap:.5rem;padding:.625rem .75rem .5rem}' +
		'.kb-chh b{font-size:.875rem;font-weight:600;color:var(--cv-name)}.kb-chh small{display:block;font-size:.6875rem;color:var(--cv-faint)}' +
		'.kb-msgs{flex:1;min-height:0;overflow-y:auto;padding:.25rem .75rem;display:flex;flex-direction:column;gap:.625rem}' +
		'.kb-m{max-width:92%;padding:.5rem .75rem;border-radius:1rem;font-size:.8125rem;line-height:1.25rem;overflow-wrap:anywhere;animation:kb-pop .16s}' +
		'.kb-m.u{align-self:flex-end;background:var(--cv-name);color:var(--kb-inv);border-bottom-right-radius:.3125rem;white-space:pre-wrap}' +
		'.kb-m.a{align-self:flex-start;background:var(--cv-mono-bg);color:var(--cv-name);border-bottom-left-radius:.3125rem}' +
		'.kb-m.err{background:var(--cv-err-bg);color:var(--cv-err-fg)}' +
		'.kb-m .kb-p{margin:0 0 .5rem}.kb-m .kb-p:last-child{margin:0}' +
		'.kb-code{margin:.375rem 0;padding:.5rem .625rem;border-radius:.5rem;background:rgb(0 0 0 / .07);font-family:var(--font-mono,ui-monospace,monospace);font-size:.6875rem;line-height:1rem;overflow:auto;white-space:pre}' +
		'html.dark .kb-code{background:rgb(255 255 255 / .07)}' +
		'.kb-ic{font-family:var(--font-mono,ui-monospace,monospace);font-size:.75rem;padding:.0625rem .25rem;border-radius:.25rem;background:rgb(0 0 0 / .07)}html.dark .kb-ic{background:rgb(255 255 255 / .09)}' +
		'.kb-think{font-size:.6875rem;color:var(--cv-faint);padding:0 .25rem}' +
		'.kb-dots{display:inline-flex;gap:.1875rem}.kb-dots i{width:.3125rem;height:.3125rem;border-radius:9999px;background:var(--cv-faint);animation:kb-bounce 1.2s infinite}' +
		'.kb-dots i:nth-child(2){animation-delay:.15s}.kb-dots i:nth-child(3){animation-delay:.3s}' +
		'.kb-sug{display:flex;flex-direction:column;gap:.375rem;margin-top:auto;padding:.5rem 0}' +
		'.kb-sug p{margin:0 0 .25rem;font-size:.75rem;color:var(--cv-meta)}' +
		'.kb-sug button{text-align:left;padding:.4375rem .625rem!important;border-radius:.75rem;font-size:.75rem;border:1px solid var(--cv-line);color:var(--cv-name);transition:background-color .15s}' +
		'.kb-sug button:hover{background:var(--cv-hover)}' +
		'.kb-cin{flex:none;display:flex;align-items:flex-end;gap:.375rem;margin:.5rem .75rem .75rem;padding:.375rem .375rem .375rem .75rem;border-radius:1.125rem;border:1px solid var(--cv-line);transition:border-color .15s}' +
		'.kb-cin:focus-within{border-color:var(--cv-faint)}' +
		'.kb-cin textarea{flex:1;min-width:0;resize:none;border:0;outline:0;background:transparent;color:var(--cv-name);font:inherit;font-size:.8125rem;line-height:1.25rem;max-height:7.5rem;padding:.25rem 0}' +
		'.kb-cin textarea::placeholder{color:var(--cv-faint)}' +
		'.kb-sendb{flex:none;width:1.875rem;height:1.875rem;border-radius:9999px;display:flex;align-items:center;justify-content:center;background:var(--cv-name);color:var(--kb-inv)!important;transition:opacity .15s}' +
		'.kb-sendb:disabled{opacity:.3;cursor:default}' +
		/* archive list */
		'.kb-arow{position:relative;padding:.5rem .75rem .5rem .875rem;border-radius:.75rem;background:var(--cv-mono-bg);margin-bottom:.375rem;cursor:pointer;overflow:hidden;transition:background-color .15s;--kb-acc:var(--cv-faint)}' +
		'.kb-arow::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--kb-acc)}' +
		'.kb-arow[data-who=scout]{--kb-acc:var(--kb-scout)}.kb-arow[data-who=scribe]{--kb-acc:var(--kb-scribe)}.kb-arow[data-who=ops]{--kb-acc:var(--kb-ops)}.kb-arow[data-who=analyst]{--kb-acc:var(--kb-analyst)}' +
		'.kb-arow:hover{background:var(--cv-hover)}.kb-arow:focus-visible{outline:2px solid var(--cv-faint);outline-offset:1px}' +
		'.kb-arow .kb-ct{gap:.5rem}.kb-arow .kb-btn{height:1.5rem;font-size:.75rem;padding:0 .5rem!important;background:var(--kb-card)}' +
		'.kb-arow .kb-age{margin-left:auto}' +
		'.kb-count{margin-left:.125rem;font-size:.6875rem;color:var(--cv-meta)}' +
		/* toasts */
		'.kb-toasts{position:fixed;left:50%;bottom:1.25rem;transform:translateX(-50%);z-index:70;display:flex;flex-direction:column;gap:.375rem;align-items:center;pointer-events:none}' +
		'.kb-toast{padding:.5rem .875rem;border-radius:.875rem;background:var(--cv-name);color:var(--kb-inv);font-size:.8125rem;box-shadow:0 8px 24px rgb(0 0 0 / .25);animation:kb-pop .18s}' +
		'.kb-toast.err{background:var(--cv-err-fg);color:#fff}' +
		'@keyframes kb-pulse{50%{opacity:.35}}@keyframes kb-flow{to{background-position:0 300%}}' +
		'@keyframes kb-fade{from{opacity:0}}@keyframes kb-pop{from{opacity:0;transform:scale(.96) translateY(4px)}}@keyframes kb-slide{from{transform:translateX(24px);opacity:0}}' +
		'@keyframes kb-bounce{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-4px)}}' +
		'@media (prefers-reduced-motion:reduce){.kb-view *,.kb-view *::before{animation:none!important;transition:none!important}}' +
		'@media (max-width:1099px){.kb-chat{position:absolute;top:0;right:0;bottom:0;z-index:30;width:min(26rem,100%);background:var(--kb-card);box-shadow:-12px 0 40px rgb(0 0 0 / .22)}' +
		'html.dark .kb-chat{background:var(--color-gray-900,#171717)}}' +
		'@media (max-width:767px){.kb-col{flex:0 0 84%;max-width:none}.kb-seg{grid-template-columns:1fr}.kb-sg span{display:none}.kb-btn .kb-bl{display:none}' +
		'.kb-card{cursor:pointer}.kb-more{opacity:1}}';

	var H = null;
	var root, els = {};
	var S = {
		data: null, sig: '', error: null, loaded: false,
		drag: null, menu: null, drawerId: null, detail: null, modal: null, chatOpen: false,
		poll: null, tick: null, busyLoad: false, pending: false, detailSeq: 0, draft: '', archOpen: false, archItems: null, archQ: '', fromArchive: false, archSeq: 0
	};
	var chat = { msgs: [], session: '', busy: false, abort: null, draft: '' };

	function e(tag, cls, text) { return H.el(tag, cls, text); }
	function html(node, svg) { node.innerHTML = svg; return node; } /* static trusted SVG constants only */

	/* ------------------------------------------------------------------ helpers */
	function headers(json) {
		var h = { Accept: 'application/json', 'X-Requested-With': 'kanban' };
		if (json) h['Content-Type'] = 'application/json';
		try { var t = localStorage.token; if (t) h.Authorization = 'Bearer ' + t; } catch (x) { /* storage blocked */ }
		return h;
	}

	function api(method, path, body) {
		return fetch(API + path, {
			method: method, credentials: 'same-origin', headers: headers(!!body),
			body: body ? JSON.stringify(body) : undefined
		}).then(function (r) {
			return r.json().catch(function () { return { ok: false, error: 'Unexpected response (' + r.status + ')' }; })
				.then(function (j) { j._status = r.status; return j; });
		});
	}

	function ago(ts) {
		if (!ts) return '';
		var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
		if (s < 45) return 'just now';
		if (s < 3600) return Math.round(s / 60) + 'm ago';
		if (s < 86400) return Math.round(s / 3600) + 'h ago';
		return Math.round(s / 86400) + 'd ago';
	}

	function clock(sec) {
		sec = Math.max(0, Math.round(sec));
		var m = Math.floor(sec / 60), s = sec % 60;
		return m + ':' + (s < 10 ? '0' : '') + s;
	}

	function stamp(ts) {
		if (!ts) return '—';
		try { return new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (x) { return '—'; }
	}

	function firstLine(t) { return String(t || '').split('\n').filter(function (l) { return l.trim(); })[0] || ''; }

	function toast(msg, err) {
		var t = e('div', 'kb-toast' + (err ? ' err' : ''), msg);
		els.toasts.appendChild(t);
		setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, err ? 5000 : 2600);
	}

	function findCard(id) {
		if (!S.data) return null;
		for (var k in S.data.columns) {
			var a = S.data.columns[k];
			for (var i = 0; i < a.length; i++) if (a[i].id === id) return { card: a[i], col: k, idx: i };
		}
		return null;
	}

	/* ------------------------------------------------------------------ data */
	function load(force) {
		if (S.busyLoad) return;
		S.busyLoad = true;
		els.refresh.classList.add('spin');
		api('GET', '/board').then(function (j) {
			if (j._status === 401 || j._status === 403) {
				S.error = { kind: 'auth', text: 'Sign in to Proxious as an admin to use the board.' };
			} else if (!j.ok) {
				S.error = { kind: 'down', text: j.error || 'The board did not answer.' };
			} else {
				S.error = null;
				S.data = j;
				S.loaded = true;
			}
		}).catch(function () {
			S.error = { kind: 'down', text: 'Can\'t reach the board service. Retrying…' };
		}).then(function () {
			S.busyLoad = false;
			els.refresh.classList.remove('spin');
			render(force);
			if (S.drawerId) loadDetail(true);
		});
	}

	function loadDetail(quiet) {
		var id = S.drawerId;
		if (!id) return;
		var seq = ++S.detailSeq;
		api('GET', '/card/' + id).then(function (j) {
			if (S.drawerId !== id || seq !== S.detailSeq) return; /* stale response */
			if (j.ok) { S.detail = j.card; renderDrawer(); }
			else if (!quiet) toast(j.error || 'Could not load the card', true);
		}).catch(function () { if (!quiet) toast('Could not load the card', true); });
	}

	/* ------------------------------------------------------------------ moves */
	function canMove(card, to) { return (MOVES[colOf(card)] || []).indexOf(to) >= 0; }
	function colOf(card) { return card.column; }

	function doMove(id, to) {
		var f = findCard(id);
		if (!f) return;
		if (!canMove(f.card, to)) {
			toast(f.card.column === 'running' ? 'A worker is running this card, so it can\'t be moved.' : 'That move isn\'t available for this card.', true);
			return;
		}
		/* optimistic */
		var snapshot = JSON.stringify(S.data.columns);
		var card = S.data.columns[f.col].splice(f.idx, 1)[0];
		if (to !== 'archive') {
			card.column = to; card.status = STATUS_FOR[to];
			S.data.columns[to].unshift(card);
		}
		S.sig = '';
		render(true);
		api('POST', '/move', { id: id, to: to }).then(function (j) {
			if (j.ok) toast(j.message || 'Moved');
			else {
				S.data.columns = JSON.parse(snapshot);
				S.sig = '';
				render(true);
				toast(j.message || j.error || 'Could not move the card', true);
			}
			load(true);
			if (S.drawerId === id) loadDetail(true);
		}).catch(function () {
			S.data.columns = JSON.parse(snapshot);
			S.sig = '';
			render(true);
			toast('Lost connection while moving the card', true);
		});
	}

	/* ------------------------------------------------------------------ board render */
	function cardEl(c) {
		var movable = (MOVES[c.column] || []).length > 0 && c.column !== 'running';
		var n = e('div', 'kb-card' + (movable ? '' : ' lock') + (c.column === 'running' ? ' live' : ''));
		n.setAttribute('data-id', c.id);
		n.setAttribute('data-who', c.assignee || '');
		n.setAttribute('tabindex', '0');
		n.setAttribute('role', 'button');
		n.setAttribute('aria-label', (c.assignee || 'unassigned') + ': ' + c.title);
		if (movable) n.setAttribute('draggable', 'true');

		var top = e('div', 'kb-ct');
		top.appendChild(e('span', 'kb-who', c.assignee || 'unassigned'));
		var age = e('span', 'kb-age');
		if (c.column === 'running' && c.started_at) { age.setAttribute('data-since', c.started_at); age.setAttribute('data-mode', 'clock'); age.textContent = clock(Date.now() / 1000 - c.started_at); }
		else { var t = c.column === 'done' ? c.completed_at : c.created_at; age.setAttribute('data-since', t || ''); age.setAttribute('data-mode', 'ago'); age.textContent = ago(t); }
		top.appendChild(age);
		var more = e('button', 'kb-more');
		more.type = 'button';
		more.setAttribute('aria-label', 'Card actions');
		html(more, ICON_DOTS);
		more.addEventListener('click', function (ev) { ev.stopPropagation(); openMenu(c.id, more); });
		more.addEventListener('mousedown', function (ev) { ev.stopPropagation(); });
		top.appendChild(more);
		n.appendChild(top);

		n.appendChild(e('div', 'kb-title', c.title));
		var sum = c.column === 'blocked' && c.last_error && !c.summary ? c.last_error : c.summary;
		if (sum && c.column !== 'ready' && c.column !== 'parked') n.appendChild(e('div', 'kb-sum' + (c.column === 'blocked' ? ' err' : ''), firstLine(sum)));

		var foot = e('div', 'kb-cf');
		if (c.column === 'running') { var lv = e('span', 'kb-live'); lv.appendChild(e('i')); lv.appendChild(document.createTextNode('Working')); foot.appendChild(lv); }
		if (c.failures > 0) foot.appendChild(e('span', 'kb-bd err', 'failed ×' + c.failures));
		if (c.status === 'todo') foot.appendChild(e('span', 'kb-bd warn', 'waiting on other cards'));
		foot.appendChild(e('span', 'kb-id', c.id));
		n.appendChild(foot);

		n.addEventListener('click', function () { openDrawer(c.id); });
		n.addEventListener('keydown', function (ev) {
			if (ev.target !== n) return;
			if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openDrawer(c.id); }
		});
		if (movable) {
			n.addEventListener('dragstart', function (ev) {
				S.drag = { id: c.id, from: c.column };
				ev.dataTransfer.effectAllowed = 'move';
				try { ev.dataTransfer.setData('text/plain', c.id); } catch (x) { /* ignore */ }
				setTimeout(function () { n.classList.add('dragging'); }, 0);
				root.classList.add('dragging');
				closeMenu();
				markTargets(true);
			});
			n.addEventListener('dragend', function () {
				n.classList.remove('dragging');
				root.classList.remove('dragging');
				markTargets(false);
				S.drag = null;
				if (S.pending) { S.pending = false; render(true); }
			});
		}
		return n;
	}

	function markTargets(on) {
		var cols = els.board.querySelectorAll('.kb-col');
		for (var i = 0; i < cols.length; i++) {
			var c = cols[i], key = c.getAttribute('data-col');
			c.classList.remove('ok', 'no', 'over');
			if (on && S.drag) c.classList.add(key === S.drag.from ? 'no' : (MOVES[S.drag.from].indexOf(key) >= 0 ? 'ok' : 'no'));
			if (on && S.drag && key === S.drag.from) c.classList.remove('no');
		}
		els.arch.classList.remove('over');
	}

	function colEl(def, cards) {
		var col = e('section', 'kb-col');
		col.setAttribute('data-col', def.key);
		col.setAttribute('aria-label', def.label);
		var h = e('div', 'kb-ch');
		h.title = def.hint;
		h.appendChild(e('span', 'kb-cd'));
		h.appendChild(e('h2', null, def.label));
		h.appendChild(e('span', 'kb-cn', String(cards.length)));
		if (def.key === 'parked' || def.key === 'ready') {
			var add = e('button', 'cv-iconbtn kb-add');
			add.type = 'button';
			add.setAttribute('aria-label', 'New card in ' + def.label);
			html(add, ICON_PLUS);
			add.addEventListener('click', function () { openModal(def.key === 'parked'); });
			h.appendChild(add);
		}
		col.appendChild(h);
		var body = e('div', 'kb-cb');
		if (!cards.length) body.appendChild(e('div', 'kb-empty', def.empty));
		cards.forEach(function (c) { body.appendChild(cardEl(c)); });
		col.appendChild(body);

		col.addEventListener('dragover', function (ev) {
			if (!S.drag || S.drag.from === def.key || MOVES[S.drag.from].indexOf(def.key) < 0) return;
			ev.preventDefault();
			ev.dataTransfer.dropEffect = 'move';
			col.classList.add('over');
		});
		col.addEventListener('dragleave', function (ev) { if (!col.contains(ev.relatedTarget)) col.classList.remove('over'); });
		col.addEventListener('drop', function (ev) {
			ev.preventDefault();
			col.classList.remove('over');
			var d = S.drag;
			if (d && d.from !== def.key) doMove(d.id, def.key);
		});
		return col;
	}

	function render(force) {
		var d = S.data;
		/* banner */
		els.banner.textContent = '';
		els.banner.hidden = !S.error;
		if (S.error) els.banner.className = 'kb-banner' + (S.error.kind === 'auth' ? '' : ' err'), els.banner.textContent = S.error.text;
		if (!d) {
			if (!S.error) { els.board.textContent = ''; els.board.appendChild(e('div', 'kb-empty', 'Loading the board…')); }
			return;
		}
		/* header pill + crew strip */
		if (els.archCount) els.archCount.textContent = d.archived ? String(d.archived) : '';
		var run = d.columns.running.length, hold = d.columns.blocked.length;
		els.pill.hidden = false;
		els.pill.className = 'kb-pill ' + (hold ? 'warn' : 'ok');
		els.pill.textContent = hold ? hold + ' on hold' : (run ? run + ' running' : 'all clear');
		els.crew.textContent = '';
		d.workers.forEach(function (w) {
			var chip = e('div', 'kb-w' + (w.busy ? ' busy' : ''));
			chip.appendChild(e('i', 'kb-wd'));
			chip.appendChild(e('b', null, w.name));
			chip.appendChild(e('span', null, w.busy ? w.title : 'idle'));
			els.crew.appendChild(chip);
		});
		els.crew.appendChild(e('div', 'kb-w', run + ' / ' + d.cap + ' workers busy'));

		var sig = JSON.stringify(d.columns);
		if (!force && sig === S.sig) return;
		if (S.drag || S.menu) { S.pending = true; return; }
		S.sig = sig;
		var scrolls = {};
		var old = els.board.querySelectorAll('.kb-col');
		for (var i = 0; i < old.length; i++) scrolls[old[i].getAttribute('data-col')] = old[i].querySelector('.kb-cb').scrollTop;
		var keepX = els.board.scrollLeft;
		els.board.textContent = '';
		COLS.forEach(function (def) {
			var col = colEl(def, d.columns[def.key] || []);
			els.board.appendChild(col);
			if (scrolls[def.key]) col.querySelector('.kb-cb').scrollTop = scrolls[def.key];
		});
		els.board.scrollLeft = keepX;
		var total = 0;
		for (var k in d.columns) total += d.columns[k].length;
		if (!total) toastOnce();
	}

	var shownEmpty = false;
	function toastOnce() { if (!shownEmpty) { shownEmpty = true; toast('The board is empty. Add a card with “New card”.'); } }

	function tickTimes() {
		var nodes = els.board ? els.board.querySelectorAll('.kb-age[data-since]') : [];
		for (var i = 0; i < nodes.length; i++) {
			var s = +nodes[i].getAttribute('data-since');
			if (!s) continue;
			nodes[i].textContent = nodes[i].getAttribute('data-mode') === 'clock' ? clock(Date.now() / 1000 - s) : ago(s);
		}
	}

	/* ------------------------------------------------------------------ menu */
	function closeMenu() {
		if (S.menu && S.menu.node.parentNode) S.menu.node.parentNode.removeChild(S.menu.node);
		var had = !!S.menu;
		S.menu = null;
		document.removeEventListener('mousedown', onDocDown, true);
		if (had && S.pending && !S.drag) { S.pending = false; render(true); }
	}
	function onDocDown(ev) { if (S.menu && !S.menu.node.contains(ev.target)) closeMenu(); }

	function openMenu(id, anchor) {
		closeMenu();
		var f = findCard(id);
		if (!f) return;
		var m = e('div', 'kb-menu');
		m.setAttribute('role', 'menu');
		m.appendChild(e('h3', null, 'Move to'));
		['ready', 'parked', 'blocked', 'done', 'archive'].forEach(function (to) {
			if (to === f.card.column) return;
			var b = e('button', 'kb-opt', TARGET[to]);
			b.type = 'button';
			b.setAttribute('role', 'menuitem');
			b.disabled = !canMove(f.card, to);
			if (b.disabled) b.title = f.card.column === 'running' ? 'A worker is running this card' : 'Not available from here';
			b.addEventListener('click', function () { closeMenu(); doMove(id, to); });
			m.appendChild(b);
		});
		var o = e('button', 'kb-opt', 'Open details');
		o.type = 'button'; o.setAttribute('role', 'menuitem');
		o.addEventListener('click', function () { closeMenu(); openDrawer(id); });
		m.appendChild(o);
		root.appendChild(m); /* inside the panel so the cv and kb theme vars apply */
		var r = anchor.getBoundingClientRect(), w = m.offsetWidth, h = m.offsetHeight;
		m.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)) + 'px';
		m.style.top = (r.bottom + h + 8 > window.innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + 'px';
		S.menu = { node: m };
		document.addEventListener('mousedown', onDocDown, true);
		var first = m.querySelector('.kb-opt:not(:disabled)');
		if (first) first.focus();
		m.addEventListener('keydown', function (ev) {
			var items = Array.prototype.slice.call(m.querySelectorAll('.kb-opt:not(:disabled)'));
			var i = items.indexOf(document.activeElement);
			if (ev.key === 'ArrowDown') { ev.preventDefault(); items[(i + 1) % items.length].focus(); }
			else if (ev.key === 'ArrowUp') { ev.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
		});
	}

	/* ------------------------------------------------------------------ new card dialog */
	function closeModal() {
		if (S.modal && S.modal.parentNode) S.modal.parentNode.removeChild(S.modal);
		S.modal = null;
	}

	function openModal(parked, prefill) {
		closeModal();
		var who = 'scout';
		var ov = e('div', 'kb-ov');
		var dlg = e('div', 'kb-dlg');
		dlg.setAttribute('role', 'dialog'); dlg.setAttribute('aria-modal', 'true'); dlg.setAttribute('aria-label', 'New card');
		dlg.appendChild(e('h2', null, 'New card'));

		dlg.appendChild(e('label', 'kb-lbl', 'Who should do it?'));
		var seg = e('div', 'kb-seg'); seg.setAttribute('role', 'radiogroup');
		var segBtns = [];
		CREW.forEach(function (w) {
			var b = e('button', 'kb-sg'); b.type = 'button';
			b.setAttribute('role', 'radio'); b.setAttribute('aria-checked', w.key === who ? 'true' : 'false');
			var t = e('b'); var dot = e('i', 'kb-wd'); dot.style.background = 'var(--kb-' + w.key + ')'; dot.style.opacity = '1';
			t.appendChild(dot); t.appendChild(document.createTextNode(w.key));
			b.appendChild(t); b.appendChild(e('span', null, w.role));
			b.addEventListener('click', function () { who = w.key; segBtns.forEach(function (x) { x.setAttribute('aria-checked', x === b ? 'true' : 'false'); }); });
			segBtns.push(b); seg.appendChild(b);
		});
		dlg.appendChild(seg);

		dlg.appendChild(e('label', 'kb-lbl', 'What needs doing?'));
		var title = e('input', 'kb-in'); title.type = 'text'; title.maxLength = 200; title.placeholder = 'One clear sentence'; title.value = (prefill && prefill.title) || '';
		dlg.appendChild(title);
		dlg.appendChild(e('label', 'kb-lbl', 'Details (optional)'));
		var body = e('textarea', 'kb-in'); body.maxLength = 8000; body.placeholder = 'Context, links, what “done” looks like…'; body.value = (prefill && prefill.body) || '';
		dlg.appendChild(body);

		var lab = e('label', 'kb-chk'); var chk = e('input'); chk.type = 'checkbox'; chk.checked = !!parked;
		lab.appendChild(chk); lab.appendChild(document.createTextNode('Park it. Don\'t start yet.'));
		dlg.appendChild(lab);
		dlg.appendChild(e('div', 'kb-note', 'Unparked cards start within about 30 seconds. Each run is capped at 20 minutes with one try, and you get a Telegram ping when it finishes.'));
		var err = e('div', 'kb-err'); err.hidden = true; dlg.appendChild(err);

		var act = e('div', 'kb-act');
		var cancel = e('button', 'kb-btn', 'Cancel'); cancel.type = 'button'; cancel.addEventListener('click', closeModal);
		var go = e('button', 'kb-btn primary', 'Create card'); go.type = 'button';
		function submit() {
			var tt = title.value.trim();
			if (!tt) { err.hidden = false; err.textContent = 'Give the card a title.'; title.focus(); return; }
			go.disabled = true; go.textContent = 'Creating…'; err.hidden = true;
			api('POST', '/create', { title: tt, body: body.value, assignee: who, park: chk.checked }).then(function (j) {
				if (j.ok) { closeModal(); toast(chk.checked ? 'Card created and parked' : 'Card created. A worker picks it up shortly.'); S.sig = ''; load(true); }
				else { go.disabled = false; go.textContent = 'Create card'; err.hidden = false; err.textContent = j.message || j.error || 'Could not create the card'; }
			}).catch(function () { go.disabled = false; go.textContent = 'Create card'; err.hidden = false; err.textContent = 'Lost connection. Try again.'; });
		}
		go.addEventListener('click', submit);
		dlg.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) submit(); });
		act.appendChild(cancel); act.appendChild(go); dlg.appendChild(act);
		ov.appendChild(dlg);
		ov.addEventListener('mousedown', function (ev) { if (ev.target === ov) closeModal(); });
		root.appendChild(ov);
		S.modal = ov;
		title.focus();
	}

	/* ------------------------------------------------------------------ drawer */
	function closeDrawer(stay) {
		var had = !!els.drawerOv;
		if (els.drawerOv && els.drawerOv.parentNode) els.drawerOv.parentNode.removeChild(els.drawerOv);
		els.drawerOv = null; S.drawerId = null; S.detail = null;
		if (had && !stay && S.fromArchive) { S.fromArchive = false; openArchive(); }
	}

	function openDrawer(id, fromArchive) {
		closeDrawer(true);
		S.fromArchive = !!fromArchive;
		S.drawerId = id;
		S.draft = '';
		var f = findCard(id);
		S.detail = f ? f.card : null;
		var ov = e('div', 'kb-dr-ov');
		ov.addEventListener('mousedown', function (ev) { if (ev.target === ov) closeDrawer(); });
		var dr = e('aside', 'kb-dr'); dr.setAttribute('role', 'dialog'); dr.setAttribute('aria-label', 'Card details');
		ov.appendChild(dr);
		root.appendChild(ov);
		els.drawerOv = ov; els.drawer = dr;
		renderDrawer();
		if (!S.detail) dr.appendChild(e('div', 'kb-note', 'Loading\u2026'));
		loadDetail(false);
	}

	function section(parent, label) { parent.appendChild(e('h3', null, label)); }

	function renderDrawer() {
		var c = S.detail, dr = els.drawer;
		if (!dr || !c) return;
		var keep = dr.querySelector('textarea'), hadFocus = keep && document.activeElement === keep, st = dr.scrollTop;
		dr.textContent = '';
		var top = e('div', 'kb-dr-top');
		var who = e('span', 'kb-who', c.assignee || 'unassigned'); who.style.color = 'var(--kb-' + c.assignee + ', var(--cv-meta))';
		top.appendChild(who);
		var colDef = COLS.filter(function (d) { return d.key === c.column; })[0];
		top.appendChild(e('span', 'kb-pill ' + (c.column === 'blocked' ? 'err' : c.column === 'done' || c.column === 'running' ? 'ok' : 'warn'), c.column === 'archived' ? 'Archived' : (colDef ? colDef.label : c.status)));
		top.appendChild(e('span', 'cv-spacer'));
		var x = e('button', 'cv-iconbtn'); x.type = 'button'; x.setAttribute('aria-label', 'Close'); x.textContent = '✕'; x.addEventListener('click', function () { closeDrawer(); });
		top.appendChild(x);
		dr.appendChild(top);
		dr.appendChild(e('h2', null, c.title));
		var meta = e('div', 'kb-meta');
		meta.appendChild(e('span', 'kb-id', c.id));
		meta.appendChild(e('span', null, 'created ' + stamp(c.created_at)));
		if (c.started_at) meta.appendChild(e('span', null, 'started ' + stamp(c.started_at)));
		if (c.completed_at) meta.appendChild(e('span', null, 'finished ' + stamp(c.completed_at)));
		dr.appendChild(meta);

		var act = e('div', 'kb-dr-act');
		[['ready', 'Queue'], ['blocked', 'Put on hold'], ['parked', 'Park'], ['done', 'Mark done'], ['archive', 'Archive']].forEach(function (p) {
			if (p[0] === c.column || !canMove(c, p[0])) return;
			var b = e('button', 'kb-btn', p[1]); b.type = 'button';
			b.addEventListener('click', function () { doMove(c.id, p[0]); if (p[0] === 'archive') closeDrawer(); });
			act.appendChild(b);
		});
		if (c.column === 'archived') {
			var rs = e('button', 'kb-btn primary', 'Restore'); rs.type = 'button';
			rs.addEventListener('click', function () { doRestore(c.id, function () { closeDrawer(); }); });
			act.appendChild(rs);
		}
		var disc = e('button', 'kb-btn', 'Discuss in chat'); disc.type = 'button';
		disc.addEventListener('click', function () { var t = c.title; S.fromArchive = false; closeDrawer(true); discuss(c.id, t); });
		act.appendChild(disc);
		dr.appendChild(act);
		if (c.column === 'running') dr.appendChild(e('div', 'kb-note', 'A worker is running this card. It can\'t be moved until it finishes (20-minute cap).'));

		if (c.last_error) { section(dr, 'Last error'); dr.appendChild(e('div', 'kb-box err', c.last_error)); }
		var res = c.result || c.summary;
		if (res) { section(dr, c.column === 'blocked' ? 'Why it stopped' : 'Result'); dr.appendChild(e('div', 'kb-box', res)); }
		if (c.body) { section(dr, 'Details'); dr.appendChild(e('div', 'kb-box', c.body)); }
		if (c.runs && c.runs.length) {
			section(dr, 'Runs');
			var rw = e('div');
			c.runs.slice().reverse().forEach(function (r) {
				var row = e('div', 'kb-run');
				var dur = r.ended && r.started ? clock(r.ended - r.started) : (r.started ? 'running' : '');
				row.appendChild(e('span', null, (r.outcome || '—') + (dur ? ' · ' + dur : '')));
				row.appendChild(e('span', null, r.summary || r.error || r.profile || ''));
				rw.appendChild(row);
			});
			dr.appendChild(rw);
		}
		section(dr, 'Comments');
		(c.comments || []).forEach(function (m) {
			var cm = e('div', 'kb-cm'); cm.appendChild(e('small', null, m.author + ' · ' + ago(m.at))); cm.appendChild(document.createTextNode(m.body)); dr.appendChild(cm);
		});
		var ta = e('textarea', 'kb-in'); ta.placeholder = 'Add a note for the worker or for yourself…'; ta.rows = 2; ta.value = S.draft; ta.maxLength = 4000; ta.style.minHeight = '3.25rem';
		ta.addEventListener('input', function () { S.draft = ta.value; });
		dr.appendChild(ta);
		var post = e('button', 'kb-btn', 'Add comment'); post.type = 'button'; post.style.marginTop = '.5rem';
		post.addEventListener('click', function () {
			var v = ta.value.trim(); if (!v) return;
			post.disabled = true;
			api('POST', '/comment', { id: c.id, body: v }).then(function (j) {
				post.disabled = false;
				if (j.ok) { S.draft = ''; ta.value = ''; loadDetail(true); } else toast(j.message || j.error || 'Could not add the comment', true);
			}).catch(function () { post.disabled = false; toast('Lost connection', true); });
		});
		dr.appendChild(post);
		if (c.events && c.events.length) {
			section(dr, 'Activity');
			var ev = e('div', 'kb-ev');
			c.events.slice(-12).forEach(function (x2) { ev.appendChild(e('span', 'kb-bd', x2.kind + ' · ' + ago(x2.at))); });
			dr.appendChild(ev);
		}
		dr.scrollTop = st;
		if (hadFocus) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
	}

	/* ------------------------------------------------------------------ archive */
	function closeArchive() {
		if (els.archOv && els.archOv.parentNode) els.archOv.parentNode.removeChild(els.archOv);
		els.archOv = null; S.archOpen = false;
	}

	function doRestore(id, done) {
		api('POST', '/restore', { id: id }).then(function (j) {
			if (j.ok) { toast(j.message || 'Restored'); S.sig = ''; load(true); loadArchive(); if (done) done(); }
			else toast(j.message || j.error || 'Could not restore the card', true);
		}).catch(function () { toast('Lost connection', true); });
	}

	function loadArchive() {
		var seq = ++S.archSeq;
		api('GET', '/archive').then(function (j) {
			if (seq !== S.archSeq) return;
			if (j.ok) S.archItems = j.cards; else { S.archItems = []; toast(j.error || 'Could not load the archive', true); }
			renderArchive();
		}).catch(function () { if (seq === S.archSeq) { S.archItems = S.archItems || []; renderArchive(); toast('Could not load the archive', true); } });
	}

	function renderArchive() {
		var box = els.archList;
		if (!box) return;
		box.textContent = '';
		if (S.archItems === null) { box.appendChild(e('div', 'kb-empty', 'Loading…')); return; }
		var q = S.archQ.trim().toLowerCase();
		var list = S.archItems.filter(function (c) {
			return !q || (c.title + ' ' + (c.assignee || '') + ' ' + (c.summary || '')).toLowerCase().indexOf(q) >= 0;
		});
		els.archSub.textContent = S.archItems.length + ' archived' + (q ? ' · ' + list.length + ' match' + (list.length === 1 ? '' : 'es') : '');
		if (!list.length) { box.appendChild(e('div', 'kb-empty', q ? 'No archived card matches that.' : 'Nothing archived yet. Drag a card to the archive zone to shelve it.')); return; }
		list.forEach(function (c) {
			var row = e('div', 'kb-arow');
			row.setAttribute('data-who', c.assignee || '');
			row.setAttribute('tabindex', '0'); row.setAttribute('role', 'button');
			row.setAttribute('aria-label', 'Archived card: ' + c.title);
			var top = e('div', 'kb-ct');
			top.appendChild(e('span', 'kb-who', c.assignee || 'unassigned'));
			top.appendChild(e('span', 'kb-age', 'archived ' + ago(c.archived_at)));
			var rb = e('button', 'kb-btn', 'Restore'); rb.type = 'button';
			rb.title = c.finished ? 'Back to Done' : 'Back to Parked';
			rb.addEventListener('click', function (ev) { ev.stopPropagation(); doRestore(c.id); });
			top.appendChild(rb);
			row.appendChild(top);
			row.appendChild(e('div', 'kb-title', c.title));
			if (c.summary) row.appendChild(e('div', 'kb-sum', firstLine(c.summary)));
			var open = function () { closeArchive(); openDrawer(c.id, true); };
			row.addEventListener('click', open);
			row.addEventListener('keydown', function (ev) { if (ev.target === row && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); open(); } });
			box.appendChild(row);
		});
	}

	function openArchive() {
		closeArchive();
		S.archOpen = true;
		var ov = e('div', 'kb-dr-ov');
		ov.addEventListener('mousedown', function (ev) { if (ev.target === ov) closeArchive(); });
		var pn = e('aside', 'kb-dr'); pn.setAttribute('role', 'dialog'); pn.setAttribute('aria-label', 'Archive');
		var top = e('div', 'kb-dr-top');
		top.appendChild(e('span', 'cv-spacer'));
		var x = e('button', 'cv-iconbtn'); x.type = 'button'; x.setAttribute('aria-label', 'Close'); x.textContent = '\u2715'; x.addEventListener('click', closeArchive);
		top.appendChild(x);
		pn.appendChild(top);
		pn.appendChild(e('h2', null, 'Archive'));
		els.archSub = e('div', 'kb-meta', '');
		pn.appendChild(els.archSub);
		pn.appendChild(e('div', 'kb-note', 'Shelved cards. Restore brings one back (to Done if it finished, otherwise to Parked). Nothing here is deleted.'));
		var si = e('input', 'kb-in'); si.type = 'search'; si.placeholder = 'Search archived cards\u2026'; si.value = S.archQ; si.style.margin = '.75rem 0';
		si.setAttribute('aria-label', 'Search archived cards');
		si.addEventListener('input', function () { S.archQ = si.value; renderArchive(); });
		pn.appendChild(si);
		els.archList = e('div');
		pn.appendChild(els.archList);
		ov.appendChild(pn);
		root.appendChild(ov);
		els.archOv = ov;
		renderArchive();
		loadArchive();
		si.focus();
	}

	/* ------------------------------------------------------------------ chat */
	function saveChat() {
		try { localStorage.setItem(CHAT_KEY, JSON.stringify({ session: chat.session, msgs: chat.msgs.slice(-40) })); } catch (x) { /* storage blocked */ }
	}
	function loadChat() {
		try {
			var j = JSON.parse(localStorage.getItem(CHAT_KEY) || 'null');
			if (j && j.session) { chat.session = j.session; chat.msgs = (j.msgs || []).filter(function (m) { return m && typeof m.text === 'string'; }); }
		} catch (x) { /* ignore */ }
		if (!chat.session) chat.session = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
	}

	function addInline(p, s) {
		s.split('\n').forEach(function (line, i) { if (i) p.appendChild(document.createElement('br')); p.appendChild(document.createTextNode(line)); });
	}
	function inline(p, t) {
		var re = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g, last = 0, m;
		while ((m = re.exec(t))) {
			if (m.index > last) addInline(p, t.slice(last, m.index));
			if (m[0].charAt(0) === '`') p.appendChild(e('code', 'kb-ic', m[0].slice(1, -1)));
			else p.appendChild(e('strong', null, m[0].slice(2, -2)));
			last = m.index + m[0].length;
		}
		if (last < t.length) addInline(p, t.slice(last));
	}
	function mdInto(node, text) {
		node.textContent = '';
		text.split('```').forEach(function (seg, i) {
			if (i % 2) { node.appendChild(e('pre', 'kb-code', seg.replace(/^[a-z0-9_+-]*\n/i, '').replace(/\n$/, ''))); return; }
			seg.split(/\n{2,}/).forEach(function (par) {
				if (!par.trim()) return;
				var p = e('p', 'kb-p'); inline(p, par.trim()); node.appendChild(p);
			});
		});
	}

	function renderChat() {
		var box = els.msgs;
		var stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
		box.textContent = '';
		if (!chat.msgs.length) {
			var sg = e('div', 'kb-sug');
			sg.appendChild(e('p', null, 'This is the same Proxious agent as on Telegram, in its own conversation. Try:'));
			SUGGEST.forEach(function (s) {
				var b = e('button', null, s); b.type = 'button';
				b.addEventListener('click', function () {
					if (s.indexOf(': ') > 0 && /^Put /.test(s)) { els.input.value = s; els.input.focus(); autosize(); updateSend(); }
					else sendChat(s);
				});
				sg.appendChild(b);
			});
			box.appendChild(sg);
		}
		chat.msgs.forEach(function (m) {
			var b = e('div', 'kb-m ' + (m.role === 'user' ? 'u' : 'a') + (m.err ? ' err' : ''));
			if (m.role === 'user') b.textContent = m.text;
			else if (m.text) mdInto(b, m.text);
			else { var d = e('span', 'kb-dots'); d.appendChild(e('i')); d.appendChild(e('i')); d.appendChild(e('i')); b.appendChild(d); }
			if (m.role !== 'user' && m.thinking && !m.text) { box.appendChild(e('div', 'kb-think', 'Thinking…')); }
			box.appendChild(b);
		});
		if (stick || chat.busy) box.scrollTop = box.scrollHeight;
	}

	function autosize() { var t = els.input; t.style.height = 'auto'; t.style.height = Math.min(120, t.scrollHeight) + 'px'; }
	function updateSend() {
		els.send.disabled = !chat.busy && !els.input.value.trim();
		els.send.innerHTML = chat.busy ? ICON_STOP : ICON_SEND; /* static SVG */
		els.send.setAttribute('aria-label', chat.busy ? 'Stop' : 'Send');
	}

	function sendChat(text) {
		text = (text || '').trim();
		if (!text || chat.busy) return;
		chat.msgs.push({ role: 'user', text: text });
		var reply = { role: 'assistant', text: '', thinking: false };
		chat.msgs.push(reply);
		chat.busy = true; els.input.value = ''; autosize(); updateSend(); renderChat();
		var ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
		chat.abort = ctl;
		var raf = 0;
		function paint() { if (raf) return; raf = requestAnimationFrame(function () { raf = 0; renderChat(); }); }
		function finish(errText) {
			chat.busy = false; chat.abort = null;
			if (errText) { reply.err = true; reply.text = reply.text ? reply.text + '\n\n' + errText : errText; }
			else if (!reply.text) { reply.text = '(no reply)'; }
			saveChat(); updateSend(); renderChat();
			load(true); /* the agent may have changed the board */
		}
		fetch(API + '/chat', {
			method: 'POST', credentials: 'same-origin', headers: headers(true), signal: ctl ? ctl.signal : undefined,
			body: JSON.stringify({ message: text, session: chat.session })
		}).then(function (r) {
			if (!r.ok) return r.json().catch(function () { return {}; }).then(function (j) { finish(j.error || ('Chat failed (' + r.status + ')')); });
			var rd = r.body.getReader(), dec = new TextDecoder(), buf = '';
			function pump() {
				return rd.read().then(function (x) {
					if (x.done) return finish();
					buf += dec.decode(x.value, { stream: true });
					var lines = buf.split('\n'); buf = lines.pop();
					lines.forEach(function (ln) {
						if (ln.indexOf('data:') !== 0) return;
						var d = ln.slice(5).trim();
						if (!d || d === '[DONE]') return;
						try {
							var j = JSON.parse(d), dl = j.choices && j.choices[0] && j.choices[0].delta;
							if (!dl) return;
							if (dl.content) { reply.text += dl.content; reply.thinking = false; paint(); }
							else if (dl.reasoning_content) { reply.thinking = true; paint(); }
						} catch (x2) { /* partial line */ }
					});
					return pump();
				});
			}
			return pump();
		}).catch(function (err) {
			if (err && err.name === 'AbortError') { reply.text = reply.text || '(stopped)'; chat.busy = false; chat.abort = null; saveChat(); updateSend(); renderChat(); return; }
			finish('Lost connection to Proxious.');
		});
	}

	function discuss(id, title) {
		setChat(true);
		els.input.value = 'About card ' + id + ' (“' + title + '”): ';
		autosize(); updateSend(); els.input.focus();
	}

	function setChat(on) {
		S.chatOpen = on;
		els.chat.hidden = !on;
		els.chatBtn.classList.toggle('on', on);
		els.chatBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
		if (on) { renderChat(); setTimeout(function () { els.input.focus(); }, 0); }
	}

	/* ------------------------------------------------------------------ build */
	function build(host) {
		H = host;
		H.ensureStyle();
		if (!document.getElementById('kanban-style')) {
			var st = document.createElement('style');
			st.id = 'kanban-style';
			st.textContent = CSS;
			document.head.appendChild(st);
		}
		loadChat();
		root = e('div', 'cv-view kb-view');
		root.id = 'kanban-view';
		root.setAttribute('role', 'region');
		root.setAttribute('aria-label', 'Kanban');
		root.hidden = true;

		var head = e('div', 'cv-head');
		head.appendChild(H.sidebarToggle());
		head.appendChild(e('span', 'cv-title', 'Kanban'));
		els.pill = e('span', 'kb-pill ok'); els.pill.hidden = true;
		head.appendChild(els.pill);
		head.appendChild(e('span', 'cv-spacer'));
		var arc = e('button', 'kb-btn'); arc.type = 'button';
		html(arc, ICON_BOX); arc.appendChild(e('span', 'kb-bl', 'Archive'));
		els.archCount = e('span', 'kb-count'); arc.appendChild(els.archCount);
		arc.setAttribute('aria-label', 'Archive'); arc.style.marginRight = '.375rem';
		arc.addEventListener('click', function () { if (S.archOpen) closeArchive(); else openArchive(); });
		head.appendChild(arc);
		var add = e('button', 'kb-btn primary'); add.type = 'button';
		html(add, ICON_PLUS); add.appendChild(e('span', 'kb-bl', 'New card'));
		add.setAttribute('aria-label', 'New card');
		add.addEventListener('click', function () { openModal(false); });
		head.appendChild(add);
		els.chatBtn = e('button', 'kb-btn'); els.chatBtn.type = 'button';
		html(els.chatBtn, ICON_CHAT); els.chatBtn.appendChild(e('span', 'kb-bl', 'Ask Proxious'));
		els.chatBtn.setAttribute('aria-label', 'Ask Proxious'); els.chatBtn.setAttribute('aria-pressed', 'false');
		els.chatBtn.style.marginLeft = '.375rem';
		els.chatBtn.addEventListener('click', function () { setChat(!S.chatOpen); });
		head.appendChild(els.chatBtn);
		els.refresh = e('button', 'cv-iconbtn'); els.refresh.type = 'button';
		els.refresh.setAttribute('aria-label', 'Refresh');
		els.refresh.innerHTML = H.icons.refresh; /* static trusted SVG from the host */
		els.refresh.addEventListener('click', function () { load(true); });
		head.appendChild(els.refresh);

		var main = e('div', 'kb-main');
		var left = e('div', 'kb-left');
		els.banner = e('div', 'kb-banner'); els.banner.hidden = true; els.banner.setAttribute('role', 'status');
		els.crew = e('div', 'kb-crew');
		els.board = e('div', 'kb-board');
		left.appendChild(els.banner); left.appendChild(els.crew); left.appendChild(els.board);
		els.arch = e('div', 'kb-arch', 'Drop here to archive');
		els.arch.addEventListener('dragover', function (ev) { if (S.drag && MOVES[S.drag.from].indexOf('archive') >= 0) { ev.preventDefault(); els.arch.classList.add('over'); } });
		els.arch.addEventListener('dragleave', function () { els.arch.classList.remove('over'); });
		els.arch.addEventListener('drop', function (ev) { ev.preventDefault(); els.arch.classList.remove('over'); if (S.drag) doMove(S.drag.id, 'archive'); });
		left.appendChild(els.arch);

		els.chat = e('aside', 'kb-chat'); els.chat.hidden = true; els.chat.setAttribute('aria-label', 'Chat with Proxious');
		var ch = e('div', 'kb-chh');
		var ttl = e('div'); ttl.appendChild(e('b', null, 'Proxious')); ttl.appendChild(e('small', null, 'Same agent as Telegram · separate conversation'));
		ch.appendChild(ttl); ch.appendChild(e('span', 'cv-spacer'));
		var clr = e('button', 'cv-iconbtn'); clr.type = 'button'; clr.setAttribute('aria-label', 'Clear chat'); clr.title = 'Clear chat'; html(clr, ICON_TRASH);
		clr.addEventListener('click', function () {
			if (chat.abort) chat.abort.abort();
			chat.msgs = []; chat.session = Math.random().toString(36).slice(2, 10) + Date.now().toString(36); chat.busy = false; saveChat(); updateSend(); renderChat();
		});
		ch.appendChild(clr);
		var cx = e('button', 'cv-iconbtn'); cx.type = 'button'; cx.setAttribute('aria-label', 'Close chat'); cx.textContent = '✕'; cx.addEventListener('click', function () { setChat(false); });
		ch.appendChild(cx);
		els.msgs = e('div', 'kb-msgs'); els.msgs.setAttribute('aria-live', 'polite');
		var cin = e('div', 'kb-cin');
		els.input = e('textarea'); els.input.rows = 1; els.input.placeholder = 'Message Proxious…'; els.input.setAttribute('aria-label', 'Message Proxious'); els.input.maxLength = 8000;
		els.input.addEventListener('input', function () { autosize(); updateSend(); });
		els.input.addEventListener('keydown', function (ev) {
			if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); sendChat(els.input.value); }
		});
		els.send = e('button', 'kb-sendb'); els.send.type = 'button';
		els.send.addEventListener('click', function () { if (chat.busy) { if (chat.abort) chat.abort.abort(); } else sendChat(els.input.value); });
		cin.appendChild(els.input); cin.appendChild(els.send);
		els.chat.appendChild(ch); els.chat.appendChild(els.msgs); els.chat.appendChild(cin);

		main.appendChild(left); main.appendChild(els.chat);
		els.toasts = e('div', 'kb-toasts'); els.toasts.setAttribute('aria-live', 'polite');
		root.appendChild(head); root.appendChild(main); root.appendChild(els.toasts);

		root.addEventListener('keydown', function (ev) {
			if (ev.key !== 'Escape') return;
			if (S.menu) closeMenu(); else if (S.modal) closeModal(); else if (S.drawerId) closeDrawer(); else if (S.archOpen) closeArchive(); else if (S.chatOpen && document.activeElement !== els.input) setChat(false);
		});
		updateSend();
		return root;
	}

	function onOpen() {
		renderChat();
		load(true);
		if (!S.poll) S.poll = setInterval(function () { if (!document.hidden) load(false); }, POLL_MS);
		if (!S.tick) S.tick = setInterval(tickTimes, 1000);
	}

	function onClose() {
		clearInterval(S.poll); clearInterval(S.tick); S.poll = S.tick = null;
		closeMenu(); closeModal(); S.fromArchive = false; closeDrawer(true); closeArchive();
	}

	function register() {
		if (!window.__cvRegisterPanel) return false;
		window.__cvRegisterPanel('kanban', { build: build, onOpen: onOpen, onClose: onClose });
		return true;
	}
	if (!register()) {
		var tries = 0;
		var t = setInterval(function () { if (register() || ++tries > 100) clearInterval(t); }, 100);
	}
})();

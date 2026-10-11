/*
 * mission-panel.js — "Mission" sidebar panel (#mission) for Open WebUI 0.11.4.
 * Registers itself via window.__cvRegisterPanel('mission', ...) exposed by crons-tab.js.
 * Data: GET /api/v1/hermiscrons/status  (snapshot written every 5 min by mission_collect.py).
 *       GET /kanban-api/notifications -> .budget (CEO weekly budget + OpenRouter key; admin only, shown under Health).
 * The only action: pause/play an automation (confirm dialog) -> POST /kanban-api/automation
 * (host service kanban-board.service, admin session required).
 * Plain DOM, textContent only (never innerHTML with data). Colours come from the shared --cv-* vars.
 */
(function () {
	'use strict';
	if (window.__missionPanelLoaded) return;
	window.__missionPanelLoaded = true;

	var API = '/api/v1/hermiscrons/status';
	var ACTION_API = '/kanban-api/automation';
	var BUDGET_API = '/kanban-api/notifications';
	var ICON_PAUSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
	var ICON_PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>';
	var POLL_MS = 30000;
	var STALE_SNAPSHOT_S = 15 * 60; /* collector runs every 5 min; >15 min means it stopped */

	var CSS =
		'.ms-body{max-width:72rem;margin:0 auto;padding:.25rem .25rem 2rem}' +
		'.ms-h{display:flex;align-items:center;gap:.5rem;margin:1.75rem .25rem .625rem;font-size:1.5rem;line-height:1.2;font-weight:700;letter-spacing:-.01em;color:var(--cv-name)}' +
		'.ms-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));gap:.5rem}' +
		'.ms-card{padding:.625rem .75rem;border-radius:.75rem;background:var(--cv-mono-bg);min-width:0}' +
		'.ms-card .k{font-size:.6875rem;color:var(--cv-meta)}' +
		'.ms-card .v{font-size:.9375rem;color:var(--cv-name);margin-top:.125rem}' +
		'.ms-card .s{font-size:.6875rem;color:var(--cv-meta);margin-top:.125rem;overflow-wrap:anywhere}' +
		'a.ms-card{display:block;text-decoration:none;transition:background-color .15s}a.ms-card:hover{background:var(--cv-hover)}' +
		'.ms-bar{height:.375rem;border-radius:9999px;background:var(--cv-line);margin-top:.5rem;overflow:hidden}' +
		'.ms-bar>i{display:block;height:100%;border-radius:9999px;background:var(--cv-ok)}' +
		'.ms-bar>i.warn{background:var(--cv-warn-fg)}.ms-bar>i.err{background:var(--cv-err)}' +
		'.ms-chips{display:flex;flex-wrap:wrap;gap:.375rem}' +
		'.ms-chip{display:inline-flex;align-items:center;gap:.375rem;padding:.125rem .5rem;border-radius:.625rem;background:var(--cv-mono-bg);font-size:.75rem;color:var(--cv-name)}' +
		'.ms-chip.over{background:var(--cv-warn-bg);color:var(--cv-warn-fg)}' +
		'.ms-dot{flex:none;width:.4375rem;height:.4375rem;border-radius:9999px;background:var(--cv-ok)}' +
		'.ms-dot.err{background:var(--cv-err)}.ms-dot.warn{background:var(--cv-warn-fg)}.ms-dot.off{background:transparent;box-shadow:inset 0 0 0 1.5px var(--cv-faint)}' +
		'.ms-row{display:flex;align-items:center;gap:.5rem;min-width:0;min-height:2rem;padding:.25rem .5rem;border-radius:.75rem}' +
		'.ms-row:hover{background:var(--cv-hover)}.ms-row.dim>:not(.ms-act){opacity:.55}' +
		'.ms-click{cursor:pointer}.ms-click:focus-visible{outline:2px solid var(--cv-faint);outline-offset:-2px}' +
		'.ms-chev{flex:none;width:.625rem;margin-right:-.25rem;color:var(--cv-faint);font-size:.875rem;line-height:1;transition:transform .15s}' +
		'.ms-click[aria-expanded=true] .ms-chev{transform:rotate(90deg)}' +
		'.ms-detail{margin:.125rem .5rem .5rem 1.375rem}' +
		'.ms-purpose{margin:0 0 .625rem;font-size:.8125rem;line-height:1.25rem;color:var(--cv-name)}' +
		'.ms-act{flex:none;width:1.75rem;height:1.75rem;display:flex;align-items:center;justify-content:center;border-radius:.5rem;color:var(--cv-meta);transition:background-color .15s,color .15s}' +
		'.ms-act:hover{background:var(--cv-mono-bg);color:var(--cv-name)}.ms-act:focus-visible{outline:2px solid var(--cv-faint);outline-offset:-2px}' +
		'.ms-act.play{color:var(--cv-ok-fg)}.ms-act:disabled{opacity:.4;cursor:default}' +
		'.ms-ov{position:fixed;inset:0;z-index:55;display:flex;align-items:center;justify-content:center;padding:1rem;background:rgb(0 0 0 / .4)}' +
		'.ms-dlg{width:min(26rem,100%);padding:1.125rem 1.25rem;border-radius:1.25rem;background:#fff;box-shadow:0 20px 60px rgb(0 0 0 / .35)}' +
		'html.dark .ms-dlg{background:var(--color-gray-900,#171717);box-shadow:0 20px 60px rgb(0 0 0 / .6),0 0 0 1px rgb(255 255 255 / .08)}' +
		'.ms-dlg h2{margin:0 0 .5rem;font-size:1.0625rem;font-weight:600;color:var(--cv-name)}' +
		'.ms-dlg p{margin:0;font-size:.8125rem;line-height:1.25rem;color:var(--cv-meta)}' +
		'.ms-dlg .ms-err2{margin-top:.625rem;font-size:.75rem;color:var(--cv-err-fg)}' +
		'.ms-dlg-act{display:flex;justify-content:flex-end;gap:.5rem;margin-top:1.125rem}' +
		'.ms-btn{display:inline-flex;align-items:center;height:1.875rem;padding:0 .75rem!important;border-radius:.625rem;font-size:.8125rem;color:var(--cv-name);background:var(--cv-mono-bg)}' +
		'.ms-btn:hover{filter:brightness(.97)}.ms-btn:disabled{opacity:.45;cursor:default}' +
		'.ms-btn.primary{background:var(--cv-name);color:#fff}html.dark .ms-btn.primary{color:var(--color-gray-900,#171717)}.ms-btn.primary:hover{opacity:.88;filter:none}' +
		'.ms-btn:focus-visible{outline:2px solid var(--cv-faint);outline-offset:1px}' +
		'.ms-grow{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--cv-name)}' +
		'.ms-meta{flex:none;font-size:.6875rem;color:var(--cv-faint);white-space:nowrap}' +
		'.ms-badge{flex:none;font-size:.75rem;line-height:1rem;padding:0 .375rem;border-radius:.375rem;background:var(--cv-mono-bg);color:var(--cv-meta)}' +
		'.ms-badge.warn{background:var(--cv-warn-bg);color:var(--cv-warn-fg)}.ms-badge.err{background:var(--cv-err-bg);color:var(--cv-err-fg)}' +
		'.ms-pill{flex:none;font-size:.75rem;line-height:1rem;padding:.0625rem .5rem;border-radius:9999px}' +
		'.ms-pill.ok{background:var(--cv-ok-bg);color:var(--cv-ok-fg)}.ms-pill.warn{background:var(--cv-warn-bg);color:var(--cv-warn-fg)}.ms-pill.err{background:var(--cv-err-bg);color:var(--cv-err-fg)}' +
		'.ms-banner{margin:.5rem .25rem;padding:.5rem .75rem;border-radius:.75rem;background:var(--cv-warn-bg);color:var(--cv-warn-fg);font-size:.75rem}' +
		'.ms-link{font-size:.75rem;color:var(--cv-meta);text-decoration:none;padding:.25rem .5rem;border-radius:.5rem}.ms-link:hover{background:var(--cv-hover)}' +
		'.ms-err{display:block;font-family:var(--font-mono,ui-monospace,monospace);font-size:.6875rem;color:var(--cv-meta);white-space:normal;overflow-wrap:anywhere}' +
		'.ms-card .t{display:flex;align-items:center;gap:.5rem}.ms-card .t .v{flex:1;margin:0}' +
		'.ms-sum{font-size:.75rem;color:var(--cv-meta);margin:.25rem .25rem .5rem}' +
		'.ms-who{flex:none;font-size:.6875rem;color:var(--cv-meta);min-width:3.25rem}' +
		'@media (max-width:767px){.ms-meta.hide-m{display:none}}';

	var H = null; /* host helpers from crons-tab.js */
	var root, els = {}, timer = null, tick = null, snap = null, fetchedAt = 0, loadError = null;
	var budget = null, budgetError = null;
	var open = {}; /* automation rows with their details expanded, kept across the 30 s re-render */

	function e(tag, cls, text) { return H.el(tag, cls, text); }

	function rel(iso) {
		if (!iso) return 'never';
		var t = new Date(iso).getTime();
		if (isNaN(t)) return '—';
		var s = Math.max(0, Math.round((Date.now() - t) / 1000));
		if (s < 60) return 'just now';
		if (s < 3600) return Math.round(s / 60) + 'm ago';
		if (s < 86400) return Math.round(s / 3600) + 'h ago';
		return Math.round(s / 86400) + 'd ago';
	}

	function section(title) {
		var h = e('h1', 'ms-h', title);
		els.body.appendChild(h);
		return h;
	}

	function card(k, v, s, href) {
		var c = e(href ? 'a' : 'div', 'ms-card');
		if (href) { c.href = href; c.target = '_blank'; c.rel = 'noopener'; }
		c.appendChild(e('div', 'k', k));
		c.appendChild(e('div', 'v', v));
		if (s) c.appendChild(e('div', 's', s));
		return c;
	}

	function bar(pct, level) {
		var b = e('div', 'ms-bar');
		var i = e('i', level === 'ok' ? '' : level);
		i.style.width = Math.max(2, Math.min(100, pct)) + '%';
		b.appendChild(i);
		return b;
	}

	/* ---- issue counting for the header pill ---- */
	function issues(d) {
		var n = 0, list = [];
		(d.health.services || []).forEach(function (s) { if (!s.ok) { n++; list.push(s.name + ' is down'); } });
		(d.health.containers || []).forEach(function (c) { if (!c.ok) { n++; list.push(c.name + ': ' + c.status); } });
		(d.health.gauges || []).forEach(function (g) { if (g.level !== 'ok') { n++; list.push(g.label + ': ' + g.text); } });
		(d.automations || []).forEach(function (a) {
			if (a.enabled && (a.stale || a.status !== 'ok')) { n++; list.push(a.name + (a.stale ? ' is overdue' : ' failed')); }
		});
		var cr = d.crew;
		if (cr && !cr.ok) { n++; list.push('Crew board unreadable'); }
		if (cr && cr.blocked) { n++; list.push(cr.blocked + ' crew card' + (cr.blocked === 1 ? '' : 's') + ' blocked — needs you'); }
		var ai = d.ai || {};
		if (!ai.ok || (ai.cap_calls && ai.calls / ai.cap_calls > 0.8) || (ai.cap_cost && ai.cost / ai.cap_cost > 0.8)) { n++; list.push('AI gateway'); }
		return { n: n, list: list };
	}

	function money(v) { return v == null ? '–' : '$' + Number(v).toFixed(2); }

	function renderBudget() {
		section('Budget');
		var g = e('div', 'ms-grid');
		if (!budget) {
			g.appendChild(card('Budget', budgetError ? 'Unavailable' : 'Loading…', budgetError || ''));
			els.body.appendChild(g);
			return;
		}
		var bd = budget, frac = bd.total ? bd.spent / bd.total : 0;
		var c1 = card('This week (' + bd.week + ')', money(bd.spent) + ' of $' + bd.total,
			bd.projected != null ? 'On pace for ' + money(bd.projected) : 'Too early to project');
		c1.appendChild(bar(frac * 100, frac >= 1 ? 'err' : frac >= 0.8 ? 'warn' : 'ok'));
		g.appendChild(c1);
		if (bd.key_remaining != null) {
			var rem = Number(bd.key_remaining), lim = Number(bd.key_limit) || 0;
			var c2 = card('OpenRouter key', money(rem) + ' left of $' + lim,
				'Resets ' + (bd.key_reset || '–') + '. Hard ceiling for every agent, Plank included.');
			c2.appendChild(bar(lim ? rem / lim * 100 : 0, bd.key_low ? 'err' : lim && rem / lim < 0.25 ? 'warn' : 'ok'));
			g.appendChild(c2);
		}
		els.body.appendChild(g);
		var ag = e('div', 'ms-chips');
		ag.style.marginTop = '.5rem';
		Object.keys(bd.alloc || {}).forEach(function (k) {
			var sp = (bd.by_agent || {})[k] || 0, al = bd.alloc[k] || 0;
			ag.appendChild(e('span', 'ms-chip' + (sp > al && sp > 0.01 ? ' over' : ''), k + ' ' + money(sp) + ' / ' + money(al)));
		});
		els.body.appendChild(ag);
	}

	function loadBudget() {
		var h = { Accept: 'application/json', 'X-Requested-With': 'kanban' };
		try { var t = localStorage.token; if (t) h.Authorization = 'Bearer ' + t; } catch (x) { /* storage blocked */ }
		return fetch(BUDGET_API, { credentials: 'same-origin', headers: h })
			.then(function (r) {
				return r.json().catch(function () { return { ok: false, error: 'Unexpected response (' + r.status + ')' }; })
					.then(function (j) {
						if (r.status === 401) throw new Error(j.error || 'Sign in to Proxious as an admin');
						if (!j.ok) throw new Error(j.error || 'Failed (' + r.status + ')');
						budget = j.budget || null; budgetError = budget ? null : 'No budget data';
					});
			})
			.catch(function (x) { budgetError = x.message === 'Failed to fetch' ? 'Lost connection.' : x.message; });
	}

	var CARD_LEVEL ={ done: '', running: '', ready: '', todo: '', scheduled: '', triage: '', blocked: 'err', review: 'warn' };

	function renderCrew(cr) {
		section('Crew');
		if (!cr.ok) {
			var eg = e('div', 'ms-grid');
			eg.appendChild(card('Kanban board', 'Unreadable', cr.error || ''));
			els.body.appendChild(eg);
			return;
		}
		els.body.appendChild(e('div', 'ms-sum',
			cr.running + (cr.cap ? ' of ' + cr.cap : '') + ' running · ' + cr.queued + ' queued · ' +
			cr.blocked + ' blocked · ' + cr.done_24h + ' done in 24h'));

		var g = e('div', 'ms-grid');
		(cr.workers || []).forEach(function (w) {
			var c = e('div', 'ms-card');
			var top = e('div', 't');
			top.appendChild(e('div', 'v', w.name));
			var lvl = w.state === 'busy' ? 'ok' : w.state === 'blocked' ? 'err' : '';
			var pill = e('span', 'ms-pill ' + (lvl || 'idle'), w.state);
			if (!lvl) { pill.style.background = 'var(--cv-mono-bg)'; pill.style.color = 'var(--cv-meta)'; }
			top.appendChild(pill);
			c.appendChild(e('div', 'k', w.role));
			c.appendChild(top);
			if (w.task) c.appendChild(e('div', 's', 'Now: ' + w.task.title + ' · ' + rel(w.task.since)));
			else if (w.last) c.appendChild(e('div', 's', 'Last: ' + w.last.title + ' · ' + (w.last.outcome || '') + ' ' + rel(w.last.at)));
			else c.appendChild(e('div', 's', 'No cards yet'));
			var extra = [];
			if (w.queued) extra.push(w.queued + ' queued');
			if (w.blocked) extra.push(w.blocked + ' blocked');
			extra.push(w.done_24h + ' done today');
			c.appendChild(e('div', 's', extra.join(' · ')));
			g.appendChild(c);
		});
		els.body.appendChild(g);

		var list = e('div', 'cv-list');
		list.style.marginTop = '.5rem';
		if (!(cr.recent || []).length) {
			list.appendChild(e('div', 'ms-row', 'No cards yet. In Telegram: /kanban create "…" --assignee scout'));
		}
		(cr.recent || []).forEach(function (t) {
			var lv = CARD_LEVEL[t.status] || '';
			var r = e('div', 'ms-row');
			r.title = t.id + (t.summary ? '\n' + t.summary : '');
			r.appendChild(e('span', 'ms-dot ' + (t.status === 'running' ? 'warn' : lv)));
			r.appendChild(e('span', 'ms-who', '@' + (t.who || '—')));
			r.appendChild(e('span', 'ms-grow', t.title + (t.summary ? ' — ' + t.summary : '')));
			if (t.status !== 'done') r.appendChild(e('span', 'ms-badge ' + lv, t.status));
			r.appendChild(e('span', 'ms-meta', rel(t.at)));
			list.appendChild(r);
		});
		els.body.appendChild(list);
	}

	function render() {
		els.body.textContent = '';
		if (!snap || snap.error) {
			var empty = e('div', 'cv-empty');
			empty.appendChild(e('b', '', loadError ? 'Could not load status' : (snap && snap.error ? 'No snapshot yet' : 'Loading…')));
			empty.appendChild(e('span', '', loadError || (snap && snap.error) || ''));
			els.body.appendChild(empty);
			els.pill.textContent = '';
			return;
		}
		var d = snap, iss = issues(d);
		els.pill.className = 'ms-pill ' + (iss.n ? 'warn' : 'ok');
		els.pill.textContent = iss.n ? iss.n + (iss.n === 1 ? ' issue' : ' issues') : 'All good';
		els.pill.title = iss.list.join('\n');

		if (d.age_s > STALE_SNAPSHOT_S) {
			els.body.appendChild(e('div', 'ms-banner', 'Snapshot is ' + Math.round(d.age_s / 60) + ' min old — the collector (mission_collect.py cron) may have stopped.'));
		}

		/* 1. Health */
		section('Health');
		var g = e('div', 'ms-grid');
		(d.health.gauges || []).forEach(function (x) {
			var c = card(x.label, x.text);
			if (x.pct != null) c.appendChild(bar(x.pct, x.level));
			else c.querySelector('.v').style.color = x.level === 'ok' ? '' : 'var(--cv-' + (x.level === 'err' ? 'err-fg' : 'warn-fg') + ')';
			g.appendChild(c);
		});
		els.body.appendChild(g);
		var chips = e('div', 'ms-chips');
		chips.style.marginTop = '.5rem';
		(d.health.services || []).concat(d.health.containers || []).forEach(function (s) {
			var ch = e('span', 'ms-chip');
			ch.title = s.status || s.unit || '';
			ch.appendChild(e('span', 'ms-dot' + (s.ok ? '' : ' err')));
			ch.appendChild(e('span', '', s.name));
			chips.appendChild(ch);
		});
		els.body.appendChild(chips);

		/* 2. Budget (CEO weekly budget + OpenRouter key) */
		renderBudget();

		/* 3. Crew (Hermes kanban workers) */
		if (d.crew) renderCrew(d.crew);

		/* 4. Automations */
		section('Automations');
		var list = e('div', 'cv-list');
		(d.automations || []).forEach(function (a) {
			var bad = a.enabled && (a.stale || a.status !== 'ok');
			var key = (a.kind || '') + ':' + (a.id || a.name);
			var r = e('div', 'ms-row ms-click' + (a.enabled ? '' : ' dim'));
			r.tabIndex = 0;
			r.setAttribute('role', 'button');
			r.setAttribute('aria-expanded', open[key] ? 'true' : 'false');
			r.title = 'Show details';
			r.addEventListener('click', function () { open[key] = !open[key]; render(); });
			r.addEventListener('keydown', function (ev) {
				if (ev.target === r && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); open[key] = !open[key]; render(); }
			});
			r.appendChild(e('span', 'ms-chev', '›'));
			r.appendChild(e('span', 'ms-dot ' + (!a.enabled ? 'off' : bad ? 'err' : '')));
			r.appendChild(e('span', 'ms-grow', a.name));
			if (a.stale) r.appendChild(e('span', 'ms-badge warn', 'overdue'));
			else if (a.enabled && a.status !== 'ok') r.appendChild(e('span', 'ms-badge err', a.status));
			if (!a.enabled) r.appendChild(e('span', 'ms-badge', 'paused'));
			r.appendChild(e('span', 'ms-meta hide-m', a.source + ' · ' + a.schedule));
			r.appendChild(e('span', 'ms-meta', rel(a.last_run)));
			if (a.kind && a.id && a.status !== 'missing') r.appendChild(toggleBtn(a));
			list.appendChild(r);
			if (open[key]) list.appendChild(details(a));
		});
		els.body.appendChild(list);

		/* 5. AI spend */
		section('AI spend today');
		var ai = d.ai || {}, ag = e('div', 'ms-grid');
		if (ai.ok) {
			var cp = ai.cap_calls ? ai.calls / ai.cap_calls * 100 : 0, sp = ai.cap_cost ? ai.cost / ai.cap_cost * 100 : 0;
			var lv = function (p) { return p > 90 ? 'err' : p > 70 ? 'warn' : 'ok'; };
			var c1 = card('Calls', ai.calls + ' of ' + ai.cap_calls, ai.model);
			c1.appendChild(bar(cp, lv(cp)));
			var c2 = card('Cost', '$' + Number(ai.cost).toFixed(3) + ' of $' + Number(ai.cap_cost).toFixed(2), 'daily cap, resets 00:00 UTC');
			c2.appendChild(bar(sp, lv(sp)));
			ag.appendChild(c1); ag.appendChild(c2);
		} else {
			ag.appendChild(card('LLM gateway', 'Unreachable', ai.error || ''));
		}
		els.body.appendChild(ag);

		/* 6. Leads */
		section('Leads');
		var lg = e('div', 'ms-grid');
		(d.leads || []).forEach(function (l) {
			lg.appendChild(card(l.label, l.count == null ? '—' : String(l.count), l.url ? 'open ↗' : '', l.url));
		});
		els.body.appendChild(lg);

		/* 7. Recent errors */
		section('Recent errors (Proxious)');
		var el2 = e('div', 'cv-list');
		if (!(d.errors || []).length) el2.appendChild(e('div', 'ms-row', 'None — clean.'));
		(d.errors || []).forEach(function (x) {
			var r = e('div', 'ms-row');
			r.style.alignItems = 'flex-start';
			var m = e('span', 'ms-grow');
			m.style.whiteSpace = 'normal';
			m.appendChild(e('span', '', x.msg.split(':')[0]));
			m.appendChild(e('span', 'ms-err', x.msg));
			r.appendChild(m);
			if (x.count > 1) r.appendChild(e('span', 'ms-badge', '×' + x.count));
			r.appendChild(e('span', 'ms-meta', x.last.slice(5, 16)));
			el2.appendChild(r);
		});
		els.body.appendChild(el2);
	}

	/* ---- automation details (row click) ---- */
	function when(iso) {
		if (!iso) return '—';
		var t = new Date(iso);
		if (isNaN(t.getTime())) return '—';
		var abs = t.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
		var s = Math.round((t.getTime() - Date.now()) / 1000);
		if (s <= 0) return abs + ' (' + rel(iso) + ')';
		var inn = s < 3600 ? Math.max(1, Math.round(s / 60)) + 'm' : s < 86400 ? Math.round(s / 3600) + 'h' : Math.round(s / 86400) + 'd';
		return abs + ' (in ' + inn + ')';
	}

	function details(a) {
		var box = e('div', 'cv-detail ms-detail');
		if (a.purpose) box.appendChild(e('p', 'ms-purpose', a.purpose));
		var kv = e('dl', 'cv-kv');
		function row(k, v, cls) {
			if (!v) return;
			kv.appendChild(e('dt', '', k));
			kv.appendChild(e('dd', cls || '', v));
		}
		row('Schedule', a.schedule + ' (' + a.source + ')');
		if (!a.enabled) row('State', a.paused_at ? 'Paused since ' + when(a.paused_at) : 'Paused');
		else if (a.next_run) row('Next run', when(a.next_run));
		row('Last run', a.last_run ? when(a.last_run) + ' · ' + a.status : 'Never ran');
		row('Runs', a.runs, 'mono');
		row('Results go to', a.deliver);
		row('Log', a.log, 'mono');
		row('Last error', a.error, 'err');
		box.appendChild(kv);
		return box;
	}

	/* ---- pause / play an automation ---- */
	function toggleBtn(a) {
		var b = e('button', 'ms-act' + (a.enabled ? '' : ' play'));
		b.type = 'button';
		var label = (a.enabled ? 'Pause ' : 'Resume ') + a.name;
		b.title = label;
		b.setAttribute('aria-label', label);
		b.innerHTML = a.enabled ? ICON_PAUSE : ICON_PLAY; /* static trusted SVG constants */
		b.addEventListener('click', function (ev) { ev.stopPropagation(); confirmToggle(a); });
		b.addEventListener('keydown', function (ev) { ev.stopPropagation(); });
		return b;
	}

	function closeDialog() {
		if (els.dlg && els.dlg.parentNode) els.dlg.parentNode.removeChild(els.dlg);
		els.dlg = null;
	}

	function postToggle(a) {
		var h = { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Requested-With': 'kanban' };
		try { var t = localStorage.token; if (t) h.Authorization = 'Bearer ' + t; } catch (x) { /* storage blocked */ }
		return fetch(ACTION_API, {
			method: 'POST', credentials: 'same-origin', headers: h,
			body: JSON.stringify({ kind: a.kind, id: a.id, action: a.enabled ? 'pause' : 'resume' })
		}).then(function (r) {
			return r.json().catch(function () { return { ok: false, error: 'Unexpected response (' + r.status + ')' }; });
		});
	}

	function confirmToggle(a) {
		closeDialog();
		var pausing = a.enabled;
		var ov = e('div', 'ms-ov');
		var dlg = e('div', 'ms-dlg');
		dlg.setAttribute('role', 'alertdialog'); dlg.setAttribute('aria-modal', 'true');
		dlg.setAttribute('aria-label', pausing ? 'Pause automation' : 'Resume automation');
		dlg.appendChild(e('h2', '', (pausing ? 'Pause “' : 'Resume “') + a.name + '”?'));
		dlg.appendChild(e('p', '', pausing
			? 'It won\'t run again until you press play. Nothing is deleted. Schedule: ' + a.schedule + ' (' + a.source + ').'
			: 'It goes back on its schedule: ' + a.schedule + ' (' + a.source + '). It runs at the next scheduled time, not right now.'));
		var err = e('div', 'ms-err2'); err.hidden = true; dlg.appendChild(err);
		var act = e('div', 'ms-dlg-act');
		var cancel = e('button', 'ms-btn', 'Cancel'); cancel.type = 'button';
		cancel.addEventListener('click', function () { closeDialog(); });
		var go = e('button', 'ms-btn primary', pausing ? 'Pause' : 'Resume'); go.type = 'button';
		go.addEventListener('click', function () {
			go.disabled = cancel.disabled = true; err.hidden = true;
			go.textContent = pausing ? 'Pausing…' : 'Resuming…';
			postToggle(a).then(function (j) {
				if (j.ok) { closeDialog(); a.enabled = !pausing; render(); load(true); return; }
				throw new Error(j.message || j.error || 'Failed');
			}).catch(function (x) {
				go.disabled = cancel.disabled = false; go.textContent = pausing ? 'Pause' : 'Resume';
				err.hidden = false; err.textContent = x.message === 'Failed to fetch' ? 'Lost connection. Try again.' : x.message;
			});
		});
		act.appendChild(cancel); act.appendChild(go); dlg.appendChild(act);
		ov.appendChild(dlg);
		ov.addEventListener('mousedown', function (ev) { if (ev.target === ov && !go.disabled) closeDialog(); });
		ov.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !go.disabled) { ev.stopPropagation(); closeDialog(); } });
		root.appendChild(ov);
		els.dlg = ov;
		cancel.focus();
	}

	function updateStamp() {
		if (!fetchedAt) { els.upd.textContent = ''; return; }
		var s = Math.round((Date.now() - fetchedAt) / 1000);
		els.upd.textContent = 'Updated ' + (s < 60 ? s + 's' : Math.round(s / 60) + 'm') + ' ago';
	}

	function load(manual) {
		if (document.hidden && !manual) return;
		if (manual) els.refresh.classList.add('spin');
		var b = loadBudget();
		fetch(API, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
			.then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
			.then(function (d) { return b.then(function () { snap = d; loadError = null; fetchedAt = Date.now(); render(); updateStamp(); }); })
			.catch(function (err) { loadError = err.message; if (!snap) render(); })
			.then(function () { setTimeout(function () { els.refresh.classList.remove('spin'); }, 400); });
	}

	function build(host) {
		H = host;
		H.ensureStyle();
		if (!document.getElementById('mission-style')) {
			var st = document.createElement('style');
			st.id = 'mission-style';
			st.textContent = CSS;
			document.head.appendChild(st);
		}
		root = e('div', 'cv-view');
		root.id = 'mission-view';
		root.setAttribute('role', 'region');
		root.setAttribute('aria-label', 'Mission');
		root.hidden = true;

		var head = e('div', 'cv-head');
		head.appendChild(H.sidebarToggle());
		head.appendChild(e('span', 'cv-title', 'Mission'));
		els.pill = e('span', 'ms-pill');
		head.appendChild(els.pill);
		head.appendChild(e('span', 'cv-spacer'));
		els.upd = e('span', 'cv-upd');
		head.appendChild(els.upd);
		var links = e('a', 'ms-link', 'Links ↗');
		links.href = '/links'; links.target = '_blank'; links.rel = 'noopener';
		head.appendChild(links);
		els.refresh = e('button', 'cv-iconbtn');
		els.refresh.type = 'button';
		els.refresh.setAttribute('aria-label', 'Refresh');
		els.refresh.innerHTML = H.icons.refresh; /* static trusted SVG from the host */
		els.refresh.addEventListener('click', function () { load(true); });
		head.appendChild(els.refresh);

		var scroller = e('div', 'cv-body');
		els.body = e('div', 'ms-body');
		scroller.appendChild(els.body);
		root.appendChild(head);
		root.appendChild(scroller);
		return root;
	}

	function onOpen() {
		render();
		load(true);
		timer = setInterval(load, POLL_MS);
		tick = setInterval(updateStamp, 5000);
	}

	function onClose() {
		closeDialog();
		clearInterval(timer);
		clearInterval(tick);
	}

	function register() {
		if (!window.__cvRegisterPanel) return false;
		window.__cvRegisterPanel('mission', { build: build, onOpen: onOpen, onClose: onClose });
		return true;
	}
	if (!register()) {
		var tries = 0;
		var t = setInterval(function () { if (register() || ++tries > 100) clearInterval(t); }, 100);
	}
})();

/*
 * notifications-panel.js — "Notifications" sidebar panel (#notifications) for Open WebUI 0.11.4.
 * Registers itself via window.__cvRegisterPanel('notifications', ...) exposed by crons-tab.js; its sidebar row sits
 * first under Search. Also keeps an unread badge on that row (polls every 60 s while the page is visible).
 * Data: GET  /kanban-api/notifications           -> `ceo alerts --json` (host service kanban-board.service, admin only)
 *       POST /kanban-api/notifications {action: read|dismiss, key | all}
 * Alerts come from the CEO engine (/root/ceo/ceo.py): budget first (weekly AI budget, per-agent allowances, OpenRouter
 * key floor + runway), then CEO decisions waiting for the founder. Critical ones also go to Telegram once.
 * Plain DOM, textContent only (never innerHTML with data). Colours come from the shared --cv-* vars.
 */
(function () {
	'use strict';
	if (window.__notifPanelLoaded) return;
	window.__notifPanelLoaded = true;

	var API = '/kanban-api/notifications';
	var LINK_ID = 'sidebar-notifications-button';
	var POLL_MS = 60000;
	var ICON_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
	var ICON_X = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 18 18 6M6 6l12 12"/></svg>';
	var SOURCES = { budget: 'Budget', ceo: 'CEO' };

	var BADGE_CSS =
		'.nt-sbadge{flex:none;margin-left:auto;min-width:1.125rem;height:1.125rem;padding:0 .3125rem;border-radius:9999px;' +
		'font-size:.6875rem;line-height:1.125rem;text-align:center;font-weight:600;color:#fff;background:oklch(.637 .237 25.331)}' +
		'.nt-sbadge.warn{background:oklch(.705 .191 47.604)}.nt-sbadge.info{background:var(--color-gray-500,#737373)}';

	var CSS =
		'.nt-body{max-width:56rem;margin:0 auto;padding:.25rem .25rem 2rem}' +
		'.nt-h{display:flex;align-items:center;gap:.5rem;margin:1.5rem .25rem .5rem;font-size:1.125rem;line-height:1.3;font-weight:600;color:var(--cv-name)}' +
		'.nt-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:.5rem}' +
		'.nt-card{padding:.625rem .75rem;border-radius:.75rem;background:var(--cv-mono-bg);min-width:0}' +
		'.nt-card .k{font-size:.6875rem;color:var(--cv-meta)}' +
		'.nt-card .v{font-size:.9375rem;color:var(--cv-name);margin-top:.125rem}' +
		'.nt-card .s{font-size:.6875rem;color:var(--cv-meta);margin-top:.125rem;overflow-wrap:anywhere}' +
		'.nt-bar{height:.375rem;border-radius:9999px;background:var(--cv-line);margin-top:.5rem;overflow:hidden}' +
		'.nt-bar>i{display:block;height:100%;border-radius:9999px;background:var(--cv-ok)}' +
		'.nt-bar>i.warn{background:var(--cv-warn-fg)}.nt-bar>i.crit{background:var(--cv-err)}' +
		'.nt-agents{display:flex;flex-wrap:wrap;gap:.375rem;margin-top:.5rem}' +
		'.nt-agent{display:inline-flex;align-items:center;gap:.375rem;padding:.125rem .5rem;border-radius:.625rem;background:var(--cv-mono-bg);font-size:.75rem;color:var(--cv-name)}' +
		'.nt-agent.over{background:var(--cv-warn-bg);color:var(--cv-warn-fg)}' +
		'.nt-tools{display:flex;align-items:center;gap:.5rem;margin:1.25rem .25rem .25rem}' +
		'.nt-list{display:grid;gap:.125rem;margin:.25rem 0}' +
		'.nt-row{display:flex;align-items:flex-start;gap:.625rem;padding:.5rem .5rem;border-radius:.75rem;transition:background-color .15s}' +
		'.nt-row:hover{background:var(--cv-hover)}.nt-row.old{opacity:.6}' +
		'.nt-row.unread{cursor:pointer}' +
		'.nt-dot{flex:none;width:.5rem;height:.5rem;margin-top:.375rem;border-radius:9999px;background:var(--cv-faint)}' +
		'.nt-dot.crit{background:var(--cv-err)}.nt-dot.warn{background:var(--cv-warn-fg)}.nt-dot.info{background:var(--color-sky-500,#0ea5e9)}' +
		'.nt-main{flex:1;min-width:0}' +
		'.nt-t{color:var(--cv-name);font-size:.8125rem;line-height:1.25rem;overflow-wrap:anywhere}' +
		'.nt-row.unread .nt-t{font-weight:600}' +
		'.nt-b{color:var(--cv-meta);font-size:.75rem;line-height:1.125rem;margin-top:.125rem;overflow-wrap:anywhere}' +
		'.nt-m{display:flex;align-items:center;gap:.375rem;margin-top:.25rem;font-size:.6875rem;color:var(--cv-faint)}' +
		'.nt-src{font-size:.6875rem;line-height:1rem;padding:0 .375rem;border-radius:.375rem;background:var(--cv-mono-bg);color:var(--cv-meta)}' +
		'.nt-act{flex:none;width:1.75rem;height:1.75rem;display:flex;align-items:center;justify-content:center;border-radius:.5rem;color:var(--cv-meta);transition:background-color .15s,color .15s}' +
		'.nt-act:hover{background:var(--cv-mono-bg);color:var(--cv-name)}.nt-act:focus-visible{outline:2px solid var(--cv-faint);outline-offset:-2px}' +
		'.nt-act:disabled{opacity:.4;cursor:default}' +
		'.nt-pill{flex:none;font-size:.75rem;line-height:1rem;padding:.0625rem .5rem;border-radius:9999px;background:var(--cv-mono-bg);color:var(--cv-meta)}' +
		'.nt-pill.crit{background:var(--cv-err-bg);color:var(--cv-err-fg)}.nt-pill.warn{background:var(--cv-warn-bg);color:var(--cv-warn-fg)}' +
		'.nt-pill.ok{background:var(--cv-ok-bg);color:var(--cv-ok-fg)}' +
		'.nt-link{font-size:.75rem;color:var(--cv-meta);padding:.25rem .5rem!important;border-radius:.5rem}.nt-link:hover{background:var(--cv-hover)}' +
		'.nt-link:disabled{opacity:.4;cursor:default}' +
		'.nt-banner{margin:.5rem .25rem;padding:.5rem .75rem;border-radius:.75rem;background:var(--cv-warn-bg);color:var(--cv-warn-fg);font-size:.75rem}' +
		'.nt-empty{padding:1.5rem .5rem;text-align:center;font-size:.8125rem;color:var(--cv-meta)}' +
		'@media (max-width:767px){.nt-m .nt-when{display:none}}';

	var H = null; /* host helpers from crons-tab.js */
	var root = null, els = {}, data = null, loadError = null, fetchedAt = 0, filter = 'all';
	var timer = null, tick = null, pending = {};

	function e(tag, cls, text) {
		var n = document.createElement(tag);
		if (cls) n.className = cls;
		if (text != null) n.textContent = text;
		return n;
	}

	function headers(json) {
		var h = { Accept: 'application/json', 'X-Requested-With': 'kanban' };
		if (json) h['Content-Type'] = 'application/json';
		try { var t = localStorage.token; if (t) h.Authorization = 'Bearer ' + t; } catch (x) { /* storage blocked */ }
		return h;
	}

	function api(method, body) {
		return fetch(API, {
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

	function money(v) { return v == null ? '–' : '$' + Number(v).toFixed(2); }

	/* ---------------- sidebar badge (works even while the panel is closed) ---------------- */
	function ensureBadgeStyle() {
		if (document.getElementById('notif-badge-style')) return;
		var st = document.createElement('style');
		st.id = 'notif-badge-style';
		st.textContent = BADGE_CSS;
		document.head.appendChild(st);
	}

	function unreadInfo() {
		if (!data || !data.active) return { n: 0, level: '' };
		var n = 0, lvl = '';
		data.active.forEach(function (a) {
			if (a.read_at) return;
			n++;
			if (a.level === 'crit') lvl = 'crit';
			else if (a.level === 'warn' && lvl !== 'crit') lvl = 'warn';
			else if (!lvl) lvl = 'info';
		});
		return { n: n, level: lvl };
	}

	function paintBadge() {
		var link = document.getElementById(LINK_ID);
		if (!link) return;
		var b = link.querySelector('.nt-sbadge');
		var u = unreadInfo();
		if (!u.n) { if (b) b.remove(); return; }
		ensureBadgeStyle();
		if (!b) { b = e('span', 'nt-sbadge'); link.appendChild(b); }
		b.className = 'nt-sbadge' + (u.level === 'crit' ? '' : ' ' + u.level);
		b.textContent = u.n > 99 ? '99+' : String(u.n);
		link.setAttribute('aria-label', 'Notifications, ' + u.n + ' unread');
	}

	var authFailed = false;
	function load(manual) {
		if (authFailed && !manual) return;
		if (document.hidden && !manual) return;
		if (manual && els.refresh) els.refresh.classList.add('spin');
		return api('GET').then(function (j) {
			if (j._status === 401) { authFailed = true; throw new Error(j.error || 'Sign in to Proxious as an admin'); }
			if (!j.ok) throw new Error(j.error || 'Failed (' + j._status + ')');
			authFailed = false; data = j; loadError = null; fetchedAt = Date.now();
		}).catch(function (x) {
			loadError = x.message === 'Failed to fetch' ? 'Lost connection.' : x.message;
		}).then(function () {
			paintBadge();
			if (root && !root.hidden) { render(); updateStamp(); }
			if (els.refresh) setTimeout(function () { els.refresh.classList.remove('spin'); }, 400);
		});
	}

	/* ---------------- panel ---------------- */
	function bar(frac, level) {
		var b = e('div', 'nt-bar');
		var i = e('i', level || '');
		i.style.width = Math.max(2, Math.min(100, Math.round(frac * 100))) + '%';
		b.appendChild(i);
		return b;
	}

	function card(k, v, s) {
		var c = e('div', 'nt-card');
		c.appendChild(e('div', 'k', k));
		c.appendChild(e('div', 'v', v));
		if (s) c.appendChild(e('div', 's', s));
		return c;
	}

	function renderBudget(bd) {
		els.body.appendChild(e('h2', 'nt-h', 'Budget'));
		var g = e('div', 'nt-grid');
		var frac = bd.total ? bd.spent / bd.total : 0;
		var c1 = card('This week (' + bd.week + ')', money(bd.spent) + ' of $' + bd.total,
			bd.projected != null ? 'On pace for ' + money(bd.projected) : 'Too early to project');
		c1.appendChild(bar(frac, frac >= 1 ? 'crit' : frac >= 0.8 ? 'warn' : ''));
		g.appendChild(c1);
		if (bd.key_remaining != null) {
			var rem = Number(bd.key_remaining), lim = Number(bd.key_limit) || 0;
			var c2 = card('OpenRouter key', money(rem) + ' left of $' + lim,
				'Resets ' + (bd.key_reset || '–') + '. Hard ceiling for every agent, Plank included.');
			c2.appendChild(bar(lim ? rem / lim : 0, bd.key_low ? 'crit' : lim && rem / lim < 0.25 ? 'warn' : ''));
			g.appendChild(c2);
		}
		els.body.appendChild(g);
		var ag = e('div', 'nt-agents');
		Object.keys(bd.alloc || {}).forEach(function (k) {
			var sp = (bd.by_agent || {})[k] || 0, al = bd.alloc[k] || 0;
			ag.appendChild(e('span', 'nt-agent' + (sp > al && sp > 0.01 ? ' over' : ''), k + ' ' + money(sp) + ' / ' + money(al)));
		});
		els.body.appendChild(ag);
	}

	function act(a, action) {
		if (pending[a.key]) return;
		pending[a.key] = true;
		render();
		api('POST', { action: action, key: a.key }).then(function (j) {
			if (!j.ok) throw new Error(j.error || j.message || 'Failed');
		}).catch(function (x) {
			loadError = x.message === 'Failed to fetch' ? 'Lost connection. Try again.' : x.message;
		}).then(function () { delete pending[a.key]; return load(true); });
	}

	function row(a, active) {
		var r = e('div', 'nt-row' + (active ? '' : ' old') + (active && !a.read_at ? ' unread' : ''));
		r.appendChild(e('span', 'nt-dot ' + a.level));
		var m = e('div', 'nt-main');
		m.appendChild(e('div', 'nt-t', a.title));
		if (a.body) m.appendChild(e('div', 'nt-b', a.body));
		var meta = e('div', 'nt-m');
		meta.appendChild(e('span', 'nt-src', SOURCES[a.source] || a.source));
		meta.appendChild(e('span', 'nt-when', ago(a.first_seen)));
		if (!active) meta.appendChild(e('span', '', a.dismissed_at ? '· dismissed' : '· resolved ' + ago(a.resolved_at)));
		m.appendChild(meta);
		r.appendChild(m);
		if (active) {
			if (!a.read_at) {
				var rd = e('button', 'nt-act');
				rd.type = 'button'; rd.title = 'Mark as read'; rd.setAttribute('aria-label', 'Mark as read');
				rd.innerHTML = ICON_CHECK; /* static trusted SVG */
				rd.disabled = !!pending[a.key];
				rd.addEventListener('click', function (ev) { ev.stopPropagation(); act(a, 'read'); });
				r.appendChild(rd);
				r.addEventListener('click', function () { act(a, 'read'); });
			}
			var dm = e('button', 'nt-act');
			dm.type = 'button'; dm.title = 'Dismiss'; dm.setAttribute('aria-label', 'Dismiss');
			dm.innerHTML = ICON_X; /* static trusted SVG */
			dm.disabled = !!pending[a.key];
			dm.addEventListener('click', function (ev) { ev.stopPropagation(); act(a, 'dismiss'); });
			r.appendChild(dm);
		}
		return r;
	}

	function chip(key, label, n) {
		var c = e('button', 'cv-chip', label + (n ? ' ' + n : ''));
		c.type = 'button';
		c.setAttribute('aria-pressed', String(filter === key));
		c.addEventListener('click', function () { filter = key; render(); });
		return c;
	}

	function render() {
		if (!root) return;
		var u = unreadInfo();
		els.pill.className = 'nt-pill ' + (u.n ? u.level : 'ok');
		els.pill.textContent = u.n ? u.n + ' unread' : 'All read';
		els.pill.hidden = !data;
		els.readAll.disabled = !u.n;
		els.body.textContent = '';
		if (loadError) els.body.appendChild(e('div', 'nt-banner', loadError));
		if (!data) {
			if (!loadError) els.body.appendChild(e('div', 'nt-empty', 'Loading…'));
			return;
		}
		if (data.budget) renderBudget(data.budget);

		var pass = function (a) { return filter === 'all' || a.source === filter; };
		var active = (data.active || []).filter(pass);
		var count = function (src) { return (data.active || []).filter(function (a) { return a.source === src; }).length; };
		var tools = e('div', 'nt-tools');
		var chips = e('div', 'cv-chips');
		chips.appendChild(chip('all', 'All', (data.active || []).length));
		chips.appendChild(chip('budget', 'Budget', count('budget')));
		chips.appendChild(chip('ceo', 'CEO', count('ceo')));
		tools.appendChild(chips);
		els.body.appendChild(tools);

		els.body.appendChild(e('h2', 'nt-h', 'Active'));
		var list = e('div', 'nt-list');
		if (!active.length) list.appendChild(e('div', 'nt-empty', filter === 'all'
			? 'All clear. Budget alerts and CEO decisions waiting for you show up here.'
			: 'Nothing active in this category.'));
		active.forEach(function (a) { list.appendChild(row(a, true)); });
		els.body.appendChild(list);

		var earlier = (data.earlier || []).filter(pass);
		if (earlier.length) {
			els.body.appendChild(e('h2', 'nt-h', 'Earlier (14 days)'));
			var l2 = e('div', 'nt-list');
			earlier.forEach(function (a) { l2.appendChild(row(a, false)); });
			els.body.appendChild(l2);
		}
	}

	function updateStamp() {
		if (!els.upd) return;
		if (!fetchedAt) { els.upd.textContent = ''; return; }
		var s = Math.round((Date.now() - fetchedAt) / 1000);
		els.upd.textContent = 'Updated ' + (s < 60 ? s + 's' : Math.round(s / 60) + 'm') + ' ago';
	}

	function build(host) {
		H = host;
		H.ensureStyle();
		if (!document.getElementById('notif-style')) {
			var st = document.createElement('style');
			st.id = 'notif-style';
			st.textContent = CSS;
			document.head.appendChild(st);
		}
		root = e('div', 'cv-view');
		root.id = 'notifications-view';
		root.setAttribute('role', 'region');
		root.setAttribute('aria-label', 'Notifications');
		root.hidden = true;

		var head = e('div', 'cv-head');
		head.appendChild(H.sidebarToggle());
		head.appendChild(e('span', 'cv-title', 'Notifications'));
		els.pill = e('span', 'nt-pill');
		els.pill.hidden = true;
		head.appendChild(els.pill);
		head.appendChild(e('span', 'cv-spacer'));
		els.upd = e('span', 'cv-upd');
		head.appendChild(els.upd);
		els.readAll = e('button', 'nt-link', 'Mark all read');
		els.readAll.type = 'button';
		els.readAll.addEventListener('click', function () {
			els.readAll.disabled = true;
			api('POST', { action: 'read', all: true }).then(function () { return load(true); });
		});
		head.appendChild(els.readAll);
		els.refresh = e('button', 'cv-iconbtn');
		els.refresh.type = 'button';
		els.refresh.setAttribute('aria-label', 'Refresh');
		els.refresh.innerHTML = H.icons.refresh; /* static trusted SVG from the host */
		els.refresh.addEventListener('click', function () { load(true); });
		head.appendChild(els.refresh);

		var scroller = e('div', 'cv-body');
		els.body = e('div', 'nt-body');
		scroller.appendChild(els.body);
		root.appendChild(head);
		root.appendChild(scroller);
		return root;
	}

	function onOpen() {
		render();
		load(true);
		tick = setInterval(updateStamp, 5000);
	}

	function onClose() {
		clearInterval(tick);
		tick = null;
	}

	/* badge + data poll run all the time (cheap: one small JSON call a minute while the tab is visible) */
	function startPolling() {
		if (timer) return;
		load(false);
		timer = setInterval(function () { load(false); }, POLL_MS);
		setInterval(paintBadge, 3000); /* the sidebar re-renders; re-attach the badge */
		document.addEventListener('visibilitychange', function () { if (!document.hidden) load(false); });
	}

	function register() {
		if (!window.__cvRegisterPanel) return false;
		window.__cvRegisterPanel('notifications', { build: build, onOpen: onOpen, onClose: onClose });
		return true;
	}
	if (!register()) {
		var tries = 0;
		var t = setInterval(function () { if (register() || ++tries > 100) clearInterval(t); }, 100);
	}
	startPolling();
})();

/*
 * drafter-panel.js — "LinkedIn Drafter" panel for the Open WebUI sidebar (see crons-tab.js).
 * Registers itself via window.__cvRegisterPanel('drafter', ...). No build step; plain DOM; all user
 * text goes in through textContent/value only. Backend: /api/v1/linkedin-drafter (linkedin_drafter.py).
 *
 * Flow (V3, default): dump a raw idea -> Draft it -> Sonnet plans (audience, post type, core line) and may ask up to 3
 * questions -> 3 English drafts checked against the founder's standards -> failures fixed once -> best one + a native
 * German (du) version + a first comment for the link. V2 (12-variant tournament) stays as a secondary button; the V1
 * button is gone but old V1 runs still render. Nothing is posted for you.
 */
(function () {
	'use strict';
	if (window.__drafterPanelLoaded) return;
	window.__drafterPanelLoaded = true;

	var API = '/api/v1/linkedin-drafter';
	var STEPS = ['Writing 6 variants', 'Checking drafts', 'Simulating audience reactions', 'Final matchups', 'Polishing the winner'];
	var ANGLE_LABELS = { contrarian: 'Contrarian take', lesson: 'Personal lesson', example: 'Concrete example', howto: 'How-to', buildinpublic: 'Build in public', question: 'Question-led',
		hook1: 'Hook 1', hook2: 'Hook 2', hook3: 'Hook 3', de: 'Deutsch' };
	/* V3: same keys as AUDIENCES / POST_TYPES / STANDARDS in linkedin_drafter_router.py */
	var AUDIENCES = [['auto', 'Let V3 suggest'], ['dtc', 'DTC brands'], ['agency', 'Agencies'], ['sme', 'Berlin SMEs'], ['founders', 'Founders']];
	var POST_TYPES = [['auto', 'Let V3 suggest'], ['build', 'Build in public'], ['insight', 'Buyer insight'], ['engineer', 'Engineer credibility']];
	var STANDARDS = [
		['hook', 'Hook = best line', 'The strongest line sits in the first two lines, before "see more".'],
		['brand', 'Says what Bunny Search is', 'The one-liner appears once, aimed at the chosen audience, never ad-like.'],
		['ending', 'Easy closing question', 'Ends with a question the audience can answer in one line.'],
		['truth', 'True, current, specific', 'Dates fit today, at least one real quote or number, nothing invented or vague.']
	];
	var STEPS_V3 = ['Planning', 'Waiting for your answers', 'Writing 3 drafts', 'Checking against your standards', 'Fixing what failed', 'Writing the German version', 'Writing the first comment'];
	function label(list, key) { for (var i = 0; i < list.length; i++) if (list[i][0] === key) return list[i][1]; return key || ''; }

	var H = null; /* host API from crons-tab.js */
	var root = null;
	var els = {};
	var S = { tab: 'scratchpad', ideas: [], idea: null, run: null, profile: null, status: null, busyMsg: '', loadingList: true };
	var pollRun = null, pollList = null, pollStatus = null;
	var showOriginal = {}, savedFlash = {};

	/* ------------------------------------------------------------ helpers */
	function authHeaders(json) {
		var h = { Accept: 'application/json' };
		if (json) h['Content-Type'] = 'application/json';
		try { var t = localStorage.token; if (t) h.Authorization = 'Bearer ' + t; } catch (e) { /* cookie auth */ }
		return h;
	}

	function api(method, path, body) {
		return fetch(API + path, {
			method: method, credentials: 'same-origin', headers: authHeaders(body !== undefined),
			body: body !== undefined ? JSON.stringify(body) : undefined
		}).then(function (r) {
			return r.json().catch(function () { return {}; }).then(function (d) {
				if (!r.ok) {
					var msg = d && d.detail;
					if (Array.isArray(msg)) msg = msg.map(function (x) { return x.msg; }).join('; ');
					throw new Error(msg || ('Request failed (' + r.status + ')'));
				}
				return d;
			});
		});
	}

	function ago(ts) {
		if (!ts) return '';
		var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
		if (s < 60) return 'just now';
		if (s < 3600) return Math.round(s / 60) + ' min ago';
		if (s < 86400) return Math.round(s / 3600) + ' h ago';
		return Math.round(s / 86400) + ' d ago';
	}

	function copyText(text, btn) {
		function done(ok) {
			if (!btn) return;
			var old = btn.getAttribute('data-label') || btn.textContent;
			btn.setAttribute('data-label', old);
			btn.textContent = ok ? 'Copied' : 'Copy failed';
			setTimeout(function () { btn.textContent = old; }, 1500);
		}
		if (navigator.clipboard && navigator.clipboard.writeText) {
			navigator.clipboard.writeText(text).then(function () { done(true); }, function () { fallback(); });
		} else fallback();
		function fallback() {
			try {
				var ta = document.createElement('textarea');
				ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
				document.body.appendChild(ta); ta.select();
				var ok = document.execCommand('copy');
				document.body.removeChild(ta); done(ok);
			} catch (e) { done(false); }
		}
	}

	function e(tag, cls, text) { return H.el(tag, cls, text); }

	function btn(label, kind, onClick) {
		var b = e('button', 'dr-btn' + (kind ? ' ' + kind : ''), label);
		b.type = 'button';
		b.addEventListener('click', onClick);
		return b;
	}

	function autosize(ta) {
		ta.style.height = 'auto';
		ta.style.height = Math.min(ta.scrollHeight + 2, 640) + 'px';
	}

	/* ------------------------------------------------------------ styles */
	var CSS =
		'#drafter-view{--dr-field:var(--color-gray-50)}' +
		'html.dark #drafter-view{--dr-field:var(--color-gray-850)}' +
		'.dr-col{width:100%;max-width:48rem;margin:0 auto;padding:.25rem 0 3rem}' +
		'.dr-tabs{display:flex;gap:.125rem;margin:0 0 .5rem}' +
		'.dr-input{display:block;width:100%;border:0;outline:0;resize:vertical;border-radius:1rem;padding:.625rem .875rem;background:var(--dr-field);' +
		'font:inherit;font-size:.875rem;line-height:1.5;color:inherit}' +
		'.dr-input::placeholder{color:var(--cv-faint)}' +
		'.dr-input:focus-visible{outline:2px solid var(--cv-faint);outline-offset:0}' +
		'.dr-row{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin:.5rem 0}' +
		'.dr-spacer{flex:1}' +
		'.dr-hint{font-size:.6875rem;color:var(--cv-meta);line-height:1rem}' +
		'.dr-btn{height:2rem;padding:0 .875rem!important;border-radius:9999px;font-size:.8125rem;display:inline-flex;align-items:center;gap:.375rem;' +
		'color:var(--cv-name)!important;transition:background-color .15s,opacity .15s}' +
		'.dr-btn:hover{background:var(--cv-hover)}' +
		'.dr-btn:focus-visible{outline:2px solid var(--cv-faint);outline-offset:1px}' +
		'.dr-btn.primary{background:var(--color-gray-900);color:var(--color-gray-50)!important}' +
		'html.dark .dr-btn.primary{background:var(--color-gray-100);color:var(--color-gray-900)!important}' +
		'.dr-btn.primary:hover{opacity:.88}' +
		'.dr-btn.secondary{border:1px solid var(--cv-faint);color:var(--cv-name)!important}' +
		'.dr-btn.secondary:hover{background:var(--cv-hover)}' +
		'.dr-btn[disabled]{opacity:.45;cursor:not-allowed}' +
		'.dr-btn[hidden]{display:none!important}' +
		'.dr-btn.danger{color:var(--cv-err-fg)!important}' +
		'.dr-sec{margin:1.25rem 0 .375rem;font-size:.75rem;color:var(--cv-meta)}' +
		'.dr-card{border-radius:1rem;background:var(--cv-mono-bg);padding:.75rem .875rem;margin:.5rem 0}' +
		'.dr-card.err{background:var(--cv-err-bg)}' +
		'.dr-card.err .dr-note,.dr-card.err .dr-hint{color:var(--cv-err-fg)}' +
		'.dr-card h4{margin:0 0 .375rem;font-size:.875rem;font-weight:400;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}' +
		'.dr-badge-ok{flex:none;font-size:.75rem;line-height:1rem;padding:0 .375rem;border-radius:.375rem;background:var(--cv-ok-bg);color:var(--cv-ok-fg)}' +
		'.dr-badge{flex:none;font-size:.75rem;line-height:1rem;padding:0 .375rem;border-radius:.375rem;background:var(--cv-hover);color:var(--cv-meta)}' +
		'.dr-score{font-size:.75rem;color:var(--cv-meta);margin-left:auto}' +
		'.dr-steps{list-style:none;margin:.5rem 0;padding:0;font-size:.8125rem}' +
		'.dr-steps li{display:flex;gap:.5rem;align-items:center;padding:.125rem 0;color:var(--cv-faint)}' +
		'.dr-steps li.on{color:var(--cv-name)}.dr-steps li.done{color:var(--cv-meta)}' +
		'.dr-steps i{width:.5rem;height:.5rem;border-radius:9999px;background:var(--cv-faint);flex:none;opacity:.6}' +
		'.dr-steps li.on i{background:var(--cv-name);opacity:1;animation:dr-pulse 1.2s ease-in-out infinite}' +
		'.dr-steps li.done i{background:var(--cv-ok);opacity:1}' +
		'@keyframes dr-pulse{50%{opacity:.35}}' +
		'.dr-bar{height:.25rem;border-radius:9999px;background:var(--cv-hover);overflow:hidden;margin:.5rem 0}' +
		'.dr-bar>div{height:100%;background:var(--color-gray-500);transition:width .4s}' +
		'.dr-react{font-size:.8125rem;line-height:1.25rem;margin:.25rem 0;color:var(--cv-name)}' +
		'.dr-react b{font-weight:500}.dr-react span{color:var(--cv-meta);font-size:.75rem}' +
		'.dr-chips{display:flex;gap:.25rem;flex-wrap:wrap;margin:.25rem 0}' +
		'.dr-flag{font-size:.6875rem;padding:0 .375rem;border-radius:.375rem;background:var(--cv-warn-bg);color:var(--cv-warn-fg)}' +
		'.dr-det{margin:.375rem 0;border-radius:1rem}' +
		'.dr-det>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:.5rem;min-height:2rem;padding:.25rem .5rem;border-radius:.75rem;font-size:.8125rem}' +
		'.dr-det>summary::-webkit-details-marker{display:none}' +
		'.dr-det>summary:hover{background:var(--cv-hover)}' +
		'.dr-det[open]>summary{background:var(--cv-hover)}' +
		'.dr-pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8125rem;line-height:1.5;padding:.5rem .75rem}' +
		'.dr-note{font-size:.75rem;color:var(--cv-meta);margin:.5rem 0}' +
		'.dr-err{font-size:.75rem;color:var(--cv-err-fg);margin:.375rem 0}' +
		'.dr-back{margin-left:-.5rem}' +
		'.dr-ptitle{font-size:.8125rem;margin:1rem 0 .25rem;color:var(--cv-name)}' +
		'.dr-persona{display:flex;gap:.375rem;align-items:flex-start;margin:.375rem 0}' +
		'.dr-persona .dr-input{flex:1}' +
		'.dr-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem;margin:.5rem 0}' +
		'.dr-set{display:flex;align-items:center;gap:.375rem .75rem;flex-wrap:wrap;margin:.5rem 0 0 .25rem;font-size:.75rem;color:var(--cv-meta)}' +
		'.dr-set label{display:inline-flex;align-items:center;gap:.375rem}' +
		'.dr-set select{font:inherit;font-size:.75rem;color:var(--cv-name);background:var(--dr-field);border:0;border-radius:9999px;padding:.25rem .625rem;cursor:pointer}' +
		'.dr-set select:focus-visible{outline:2px solid var(--cv-faint)}' +
		'.dr-std{position:relative}' +
		'.dr-std>summary{cursor:pointer;list-style:none;color:var(--cv-name);background:var(--dr-field);border-radius:9999px;padding:.25rem .625rem}' +
		'.dr-std>summary::-webkit-details-marker{display:none}' +
		'.dr-std[open]>summary{outline:1px solid var(--cv-faint)}' +
		'.dr-std-list{margin:.375rem 0 0;padding:.5rem .625rem;border-radius:.75rem;background:var(--dr-field);max-width:30rem}' +
		'.dr-std-list label{display:flex;align-items:flex-start;gap:.5rem;padding:.25rem 0;color:var(--cv-name);cursor:pointer}' +
		'.dr-std-list label span{display:block;color:var(--cv-meta);font-size:.6875rem}' +
		'.dr-std-list input{margin-top:.2rem}' +
		'.dr-rot{font-size:.6875rem;color:var(--cv-meta)}' +
		'.dr-checks{display:flex;gap:.25rem;flex-wrap:wrap;margin:0 0 .375rem}' +
		'.dr-ck{font-size:.6875rem;padding:0 .375rem;border-radius:.375rem;cursor:help}' +
		'.dr-ck.ok{background:var(--cv-ok-bg);color:var(--cv-ok-fg)}' +
		'.dr-ck.no{background:var(--cv-warn-bg);color:var(--cv-warn-fg)}' +
		'.dr-q{margin:.625rem 0}' +
		'.dr-q b{display:block;font-size:.8125rem;font-weight:500;margin:0 0 .125rem}' +
		'.dr-cell{display:flex;flex-direction:column;gap:.125rem;min-width:0}' +
		'.dr-num{padding:.375rem .625rem;border-radius:.75rem;-moz-appearance:textfield}' +
		'.dr-notes{margin:.5rem 0 0}' +
		'.dr-notes-head{display:flex;align-items:baseline;gap:.5rem;margin:0 0 .25rem .25rem}' +
		'.dr-notes-head b{font-size:.75rem;font-weight:500;color:var(--cv-name)}' +
		'.dr-notes .dr-input{font-size:.8125rem;border-radius:.875rem;padding:.5rem .75rem}' +
		'.dr-notes .dr-chips{margin:.375rem 0 0}' +
		'.dr-notes .cv-chip{font-size:.75rem}' +
		'.dr-notes-more{margin:.375rem 0 0 .25rem}' +
		'.dr-notes-grp{display:flex;align-items:baseline;gap:.375rem;flex-wrap:wrap;margin:.25rem 0}' +
		'.dr-notes-grp>span{font-size:.6875rem;color:var(--cv-meta);min-width:3.5rem}' +
		'.dr-used{font-size:.75rem;color:var(--cv-meta);margin:.25rem 0 .5rem;white-space:pre-wrap;overflow-wrap:anywhere}' +
		'.dr-used b{font-weight:500;color:var(--cv-name)}' +
		'.dr-lnwrap{position:relative}' +
		'.dr-lnwrap>textarea.dr-input{padding-left:2.625rem}' +
		'.dr-gutter{position:absolute;left:0;top:0;bottom:0;width:2.25rem;overflow:hidden;pointer-events:none;border-radius:1rem 0 0 1rem}' +
		'.dr-gutter>span{position:absolute;right:.375rem;font-size:.6875rem;color:var(--cv-meta);opacity:.8;font-variant-numeric:tabular-nums;user-select:none}' +
		'.dr-lnmirror{position:absolute;left:0;top:0;visibility:hidden;pointer-events:none;white-space:pre-wrap;overflow-wrap:break-word;height:auto!important;resize:none}' +
		'@media (max-width:767px){.dr-col{padding-bottom:5rem}.dr-grid{grid-template-columns:repeat(2,1fr)}.dr-set{margin-left:0}}';

	/* ------------------------------------------------------------ build */
	function build(host) {
		H = host;
		H.ensureStyle();
		if (!document.getElementById('drafter-style')) {
			var st = document.createElement('style');
			st.id = 'drafter-style';
			st.textContent = CSS;
			document.head.appendChild(st);
		}
		root = e('div', 'cv-view');
		root.id = 'drafter-view';
		root.setAttribute('role', 'region');
		root.setAttribute('aria-label', 'LinkedIn Drafter');
		root.hidden = true;

		var head = e('div', 'cv-head');
		head.appendChild(H.sidebarToggle());
		head.appendChild(e('span', 'cv-title', 'LinkedIn Drafter'));
		head.appendChild(e('span', 'cv-spacer'));
		els.gw = e('span', 'cv-upd');
		head.appendChild(els.gw);

		els.body = e('div', 'cv-body');
		els.col = e('div', 'dr-col');
		els.body.appendChild(els.col);
		root.appendChild(head);
		root.appendChild(els.body);
		return root;
	}

	/* ------------------------------------------------------------ lifecycle */
	function onOpen() {
		S.tab = (S.tab === 'profile' || S.tab === 'review') ? S.tab : 'scratchpad';
		render();
		loadStatus();
		loadList();
		pollStatus = setInterval(loadStatus, 30000);
		pollList = setInterval(function () {
			if (S.tab === 'scratchpad' && !S.idea && S.ideas.some(function (i) { return i.run && i.run.status === 'running'; })) loadList(true);
		}, 4000);
		if (S.idea && S.run && S.run.status === 'running') startRunPoll();
	}

	function onClose() {
		clearInterval(pollStatus); clearInterval(pollList); stopRunPoll();
	}

	function loadStatus() {
		api('GET', '/status').then(function (d) {
			S.status = d;
			var g = d.gateway || {};
			els.gw.textContent = g.ok ? ('Gateway ok' + (g.cap_calls ? ' · ' + g.calls_today + '/' + g.cap_calls + ' calls today' : '')) : 'Gateway offline';
			els.gw.style.color = g.ok ? '' : 'var(--cv-err-fg)';
			if (S.tab === 'scratchpad' && !S.idea) updateComposerState();
		}).catch(function () { els.gw.textContent = 'Backend unreachable'; });
	}

	function loadList(quiet) {
		return api('GET', '/ideas').then(function (d) {
			S.ideas = d.ideas || [];
			S.loadingList = false;
			if (S.tab === 'scratchpad' && !S.idea) { renderIdeas(); if (els.settings) els.settings.refreshHint(); }
		}).catch(function (err) {
			S.loadingList = false;
			if (!quiet && S.tab === 'scratchpad' && !S.idea) { S.listError = err.message; renderIdeas(); }
		});
	}

	/* ------------------------------------------------------------ render shell */
	function render() {
		els.col.textContent = '';
		var tabs = e('div', 'dr-tabs');
		[['scratchpad', 'Scratchpad'], ['review', 'Review'], ['profile', 'Profile & audience']].forEach(function (t) {
			var b = e('button', 'cv-chip', t[1]);
			b.type = 'button';
			b.setAttribute('aria-pressed', S.tab === t[0] && !(t[0] === 'scratchpad' && S.idea) ? 'true' : 'false');
			b.addEventListener('click', function () {
				stopRunPoll();
				S.tab = t[0]; S.idea = null; S.run = null;
				render();
				if (t[0] === 'scratchpad') loadList();
			});
			tabs.appendChild(b);
		});
		els.col.appendChild(tabs);
		els.main = e('div');
		els.col.appendChild(els.main);
		if (S.tab === 'profile') return renderProfile();
		if (S.tab === 'review') return renderReview();
		if (S.idea) return renderDetail();
		renderScratchpad();
	}

	/* ------------------------------------------------------------ scratchpad */
	function renderScratchpad() {
		var m = els.main;
		m.textContent = '';
		els.input = e('textarea', 'dr-input');
		els.input.rows = 5;
		els.input.maxLength = 4000;
		els.input.placeholder = 'Dump a raw idea. Rough notes, a story, a number, a half-formed opinion. It does not need to be pretty.';
		els.input.setAttribute('aria-label', 'Raw idea');
		els.input.addEventListener('input', function () { autosize(els.input); updateComposerState(); });
		m.appendChild(els.input);

		els.notes = notesBox(S.notesDraft || '', function (v) { S.notesDraft = v; });
		m.appendChild(els.notes.wrap);
		els.settings = settingsBox(S.setDraft || {}, function (v) { S.setDraft = v; });
		m.appendChild(els.settings.wrap);

		var row = e('div', 'dr-row');
		els.count = e('span', 'dr-hint');
		row.appendChild(els.count);
		row.appendChild(e('span', 'dr-spacer'));
		els.save = btn('Save for later', '', function () { createIdea(false); });
		els.draft = btn('Draft it', 'primary', function () { createIdea(true, 'v3'); });
		els.draftV2 = btn('Draft with V2', 'secondary', function () { createIdea(true, 'v2'); });
		row.appendChild(els.save);
		row.appendChild(els.draftV2);
		row.appendChild(els.draft);
		m.appendChild(row);
		els.composerErr = e('div', 'dr-err');
		els.composerErr.hidden = true;
		m.appendChild(els.composerErr);

		m.appendChild(e('div', 'dr-sec', 'Ideas'));
		els.list = e('div', 'cv-list');
		m.appendChild(els.list);
		updateComposerState();
		renderIdeas();
	}

	function updateComposerState() {
		if (!els.input || !els.count) return;
		var n = els.input.value.trim().length;
		els.count.textContent = n + ' / 4000';
		var ok = n >= 10;
		var busy = S.status && S.status.busy;
		var offline = S.status && S.status.gateway && !S.status.gateway.ok;
		els.save.disabled = !ok;
		els.draft.disabled = !ok || busy || offline;
		els.draftV2.disabled = !ok || busy || offline;
		var why = busy ? 'Another draft is being written; wait for it to finish.' : offline ? 'The LLM gateway is offline.' : (ok ? '' : 'Write at least 10 characters.');
		els.draft.title = why || 'V3 (Sonnet): plans first and may ask you up to 3 questions, then writes 3 English drafts, checks them against your standards, fixes what fails, and adds a German version and a first comment. About a minute.';
		els.draftV2.title = why || 'The older V2 tournament: 12 variants scored by simulated personas. Kept for comparison for a few weeks.';
	}

	/* ------------------------------------------------------------ V3 settings: audience, post type, standards */
	function selectBox(list, value, aria) {
		var s = document.createElement('select');
		s.setAttribute('aria-label', aria);
		list.forEach(function (o) {
			var op = document.createElement('option');
			op.value = o[0]; op.textContent = o[1];
			s.appendChild(op);
		});
		s.value = value || 'auto';
		return s;
	}

	/* "Last posted: Build in public x3" so the three post types keep rotating. Uses the idea list (posted_at + post_type). */
	function rotationHint() {
		var posted = (S.ideas || []).filter(function (i) { return i.posted_at && i.post_type && i.post_type !== 'auto'; })
			.sort(function (a, b) { return b.posted_at - a.posted_at; }).slice(0, 3);
		if (!posted.length) return '';
		var last = posted[0].post_type, n = 0;
		while (n < posted.length && posted[n].post_type === last) n++;
		var txt = 'Last posted: ' + label(POST_TYPES, last) + (n > 1 ? ' ×' + n : '');
		if (n >= 2) {
			var other = POST_TYPES.filter(function (t) { return t[0] !== 'auto' && t[0] !== last; }).map(function (t) { return t[1]; });
			txt += ' · time for ' + other.join(' or ');
		}
		return txt;
	}

	function settingsBox(init, onChange) {
		var wrap = e('div', 'dr-set');
		var aud = selectBox(AUDIENCES, init.audience, 'Who this post is for');
		var typ = selectBox(POST_TYPES, init.post_type, 'Post type');
		var la = e('label'); la.appendChild(document.createTextNode('For')); la.appendChild(aud);
		var lt = e('label'); lt.appendChild(document.createTextNode('Type')); lt.appendChild(typ);
		wrap.appendChild(la);
		wrap.appendChild(lt);

		var on = {};
		var std0 = init.standards || STANDARDS.map(function (s) { return s[0]; });
		STANDARDS.forEach(function (s) { on[s[0]] = std0.indexOf(s[0]) !== -1; });
		var det = e('details', 'dr-std');
		var sum = e('summary');
		det.appendChild(sum);
		var list = e('div', 'dr-std-list');
		list.appendChild(e('div', 'dr-hint', 'Every V3 draft is checked against these and fixed once if it fails. Untick one for this post only.'));
		STANDARDS.forEach(function (s) {
			var l = e('label');
			var cb = document.createElement('input');
			cb.type = 'checkbox'; cb.checked = on[s[0]];
			cb.addEventListener('change', function () { on[s[0]] = cb.checked; changed(); });
			l.appendChild(cb);
			var t = e('div');
			t.appendChild(document.createTextNode(s[1]));
			t.appendChild(e('span', null, s[2]));
			l.appendChild(t);
			list.appendChild(l);
		});
		det.appendChild(list);
		wrap.appendChild(det);
		var rot = e('span', 'dr-rot', rotationHint());
		wrap.appendChild(rot);

		function get() {
			return { audience: aud.value, post_type: typ.value, standards: STANDARDS.filter(function (s) { return on[s[0]]; }).map(function (s) { return s[0]; }) };
		}
		function changed() {
			var v = get();
			sum.textContent = 'Standards ' + v.standards.length + '/' + STANDARDS.length + ' ▾';
			onChange && onChange(v);
		}
		aud.addEventListener('change', changed);
		typ.addEventListener('change', changed);
		sum.textContent = 'Standards ' + STANDARDS.filter(function (s) { return on[s[0]]; }).length + '/' + STANDARDS.length + ' ▾';
		return { wrap: wrap, get: get, refreshHint: function () { rot.textContent = rotationHint(); } };
	}

	/* ------------------------------------------------------------ notes for the agent */
	/* Short founder hints for ONE post (tone, length, hook, include/avoid, ending). Sent with the idea; the backend
	 * puts them in front of the writer, the polish step and Polish correction, where they outrank the angle and format. */
	var NOTE_QUICK = [
		['Shorter', 'Keep it short: under 800 characters.'],
		['Self-ironic', 'Tone: self-ironic, laugh at myself a little.'],
		['Honest & raw', 'Tone: honest and a bit raw, admit what I got wrong.'],
		['Story first', 'Open with the moment it happened, lesson at the end.'],
		['Hook idea…', 'Hook idea: '],
		['Must include…', 'Must include: '],
		["Don't mention…", "Don't mention: "],
		['End with a question', 'End with a question a founder can answer in one line.']
	];
	var NOTE_MORE = [
		['Tone', [
			['Calm, no hype', 'Tone: calm and matter-of-fact, zero hype.'],
			['Like a text to a friend', 'Write it like I am texting a friend, not presenting.'],
			['Confident', 'Tone: confident, no hedging, no apologising.'],
			['Curious', 'Tone: curious, I am still figuring this out.']
		]],
		['Shape', [
			['Very short', 'Very short: 3-5 lines max.'],
			['One line per paragraph', 'One sentence per paragraph.'],
			['Short list', 'Put the main points in a short list (3-5 items).'],
			['Lead with a number…', 'Open with this number: '],
			['Before / after', 'Show it as before vs after.']
		]],
		['Facts', [
			['Fact…', 'Fact (true, use it): '],
			['Not live yet', 'It is not live yet, so no result claims.'],
			['Write for…', 'Write for: '],
			['Mention someone…', 'Mention (by first name only): ']
		]],
		['Ending', [
			['Soft invite', 'End with a soft invite to try it, no hard sell.'],
			['No call to action', 'No call to action, just end on the thought.'],
			['Ask for feedback…', 'End by asking for honest feedback on '],
			['Concrete next step', 'End with what I am doing next this week.']
		]],
		['Avoid', [
			['No hashtags / emoji', 'No hashtags, no emoji.'],
			['No product plug', 'Do not mention my product at all.'],
			['No buzzwords', "Avoid 'journey', 'excited', 'game-changer', 'unlock'."],
			['No fake drama', 'No dramatic hook, no "nobody talks about this".']
		]]
	];
	var NOTE_EXAMPLES = [
		'e.g. Self-ironic. Admit I almost did not ship it. Under 800 characters. End by asking who else charges for a side project.',
		'e.g. Hook idea: "I made my first euro this week and I am oddly nervous." No hashtags. Keep the product to one mention.',
		'e.g. Write for other solo founders. Fact: first payment came in on Tuesday. No call to action.',
		'e.g. Story first, lesson last. Plain words, short sentences. Don\'t mention pricing.'
	];

	function notesBox(initial, onChange) {
		var wrap = e('div', 'dr-notes');
		var head = e('div', 'dr-notes-head');
		head.appendChild(e('b', null, 'Notes for the agent'));
		head.appendChild(e('span', 'dr-hint', 'optional · how the post should be, not what it is about'));
		var cnt = e('span', 'dr-hint');
		cnt.style.marginLeft = 'auto';
		head.appendChild(cnt);
		wrap.appendChild(head);

		var ta = e('textarea', 'dr-input');
		ta.rows = 2; ta.maxLength = 1500;
		ta.value = initial || '';
		ta.placeholder = NOTE_EXAMPLES[Math.floor(Math.random() * NOTE_EXAMPLES.length)];
		ta.setAttribute('aria-label', 'Notes for the agent: hints on tone, length, hook, what to include or avoid');
		function changed() { autosize(ta); cnt.textContent = ta.value.length ? ta.value.length + ' / 1500' : ''; onChange && onChange(ta.value); }
		ta.addEventListener('input', changed);
		wrap.appendChild(ta);
		setTimeout(function () { if (ta.value) changed(); }, 0);

		function add(text) {
			var cur = ta.value.replace(/\s+$/, '');
			if (cur && cur.indexOf(text.trim()) !== -1) { ta.focus(); return; }
			ta.value = (cur ? cur + '\n' : '') + text;
			changed();
			ta.focus();
			ta.setSelectionRange(ta.value.length, ta.value.length);
		}
		function chip(p) {
			var b = e('button', 'cv-chip', p[0]);
			b.type = 'button';
			b.title = p[1].trim();
			b.addEventListener('click', function () { add(p[1]); });
			return b;
		}

		var chips = e('div', 'dr-chips');
		NOTE_QUICK.forEach(function (p) { chips.appendChild(chip(p)); });
		var last = null;
		try { last = localStorage.getItem('dr-last-notes'); } catch (x) { /* storage blocked */ }
		if (last) {
			var reuse = e('button', 'cv-chip', '↺ Last notes');
			reuse.type = 'button';
			reuse.title = last;
			reuse.addEventListener('click', function () { add(last); });
			chips.appendChild(reuse);
		}
		var moreBtn = e('button', 'cv-chip', 'More ideas');
		moreBtn.type = 'button';
		moreBtn.setAttribute('aria-expanded', 'false');
		chips.appendChild(moreBtn);
		wrap.appendChild(chips);

		var more = e('div', 'dr-notes-more');
		more.hidden = true;
		NOTE_MORE.forEach(function (g) {
			var grp = e('div', 'dr-notes-grp');
			grp.appendChild(e('span', null, g[0]));
			g[1].forEach(function (p) { grp.appendChild(chip(p)); });
			more.appendChild(grp);
		});
		more.appendChild(e('div', 'dr-hint', 'Tip: one hint per line. Talk to it like a ghostwriter: the tone, the length, a hook you like, a fact it must use, a word you hate, how to end. Hints ending in "…" need you to finish the sentence.'));
		wrap.appendChild(more);
		moreBtn.addEventListener('click', function () {
			more.hidden = !more.hidden;
			moreBtn.textContent = more.hidden ? 'More ideas' : 'Fewer ideas';
			moreBtn.setAttribute('aria-expanded', more.hidden ? 'false' : 'true');
		});

		return { wrap: wrap, ta: ta, set: function (v) { ta.value = v || ''; changed(); } };
	}

	function createIdea(runNow, engine) {
		var text = els.input.value.trim();
		var notes = els.notes.ta.value.trim();
		var set = els.settings.get();
		els.composerErr.hidden = true;
		els.save.disabled = els.draft.disabled = els.draftV2.disabled = true;
		api('POST', '/ideas', { text: text, notes: notes, audience: set.audience, post_type: set.post_type, standards: set.standards }).then(function (d) {
			if (notes) { try { localStorage.setItem('dr-last-notes', notes); } catch (x) { /* storage blocked */ } }
			S.notesDraft = ''; S.setDraft = null;
			if (!runNow) { els.input.value = ''; autosize(els.input); els.notes.set(''); updateComposerState(); return loadList(); }
			return startRun(d.id, engine).then(function () { els.input && (els.input.value = ''); });
		}).catch(function (err) {
			els.composerErr.textContent = err.message;
			els.composerErr.hidden = false;
			updateComposerState();
		});
	}

	function startRun(ideaId, engine) {
		return api('POST', '/ideas/' + ideaId + '/run', { engine: engine || 'v3' }).then(function () { return openIdea(ideaId); });
	}

	function renderIdeas() {
		if (!els.list) return;
		var L = els.list;
		L.textContent = '';
		if (S.loadingList) { L.appendChild(e('div', 'dr-note', 'Loading…')); return; }
		if (S.listError) { L.appendChild(e('div', 'dr-err', 'Could not load ideas: ' + S.listError)); S.listError = null; return; }
		if (!S.ideas.length) {
			var em = e('div', 'cv-empty');
			em.style.minHeight = '12rem';
			em.appendChild(e('b', null, 'No ideas yet'));
			em.appendChild(e('span', null, 'Dump a raw idea above and press Draft it. V3 plans the post, may ask you a few questions, then writes it in English and German in about a minute.'));
			L.appendChild(em);
			return;
		}
		S.ideas.forEach(function (i) {
			var st = i.run ? i.run.status : 'new';
			var row = e('div', 'cv-row');
			row.setAttribute('role', 'button'); row.tabIndex = 0;
			row.appendChild(e('span', 'cv-dot ' + (st === 'done' ? 'ok' : st === 'failed' ? 'fail' : st === 'running' ? 'ok' : '')));
			var main = e('div', 'cv-main');
			var l1 = e('div', 'cv-l1');
			var nm = e('div', 'cv-name', i.text.replace(/\s+/g, ' ').slice(0, 140));
			l1.appendChild(nm);
			main.appendChild(l1);
			var meta = ago(i.updated_at) + (i.audience && i.audience !== 'auto' ? ' · for ' + label(AUDIENCES, i.audience) : '') +
				(i.notes ? ' · with notes' : '') + (st === 'running' ? ' · ' + (i.run.step || 'working') : st === 'failed' ? ' · failed' :
				st === 'needs_input' ? ' · waiting for your answers' : st === 'new' ? ' · not drafted yet' : '');
			main.appendChild(e('div', 'cv-when', meta));
			row.appendChild(main);
			if (i.posted) row.appendChild(e('span', 'dr-badge-ok', 'Posted'));
			if (st === 'running') row.appendChild(e('span', 'dr-badge', 'Drafting…'));
			if (st === 'needs_input') row.appendChild(e('span', 'dr-flag', 'Needs answers'));
			if (st === 'failed') row.appendChild(e('span', 'cv-badge fail', 'Failed'));
			var open = function () { openIdea(i.id); };
			row.addEventListener('click', open);
			row.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } });
			L.appendChild(row);
		});
	}

	/* ------------------------------------------------------------ detail */
	function openIdea(id) {
		stopRunPoll();
		return api('GET', '/ideas/' + id).then(function (d) {
			S.tab = 'scratchpad'; S.idea = d; S.run = d.latest_run;
			render();
			if (S.run && S.run.status === 'running') startRunPoll();
		}).catch(function (err) { alert(err.message); });
	}

	function startRunPoll() {
		stopRunPoll();
		pollRun = setInterval(function () {
			if (!S.run) return;
			api('GET', '/runs/' + S.run.id).then(function (r) {
				var was = S.run.status;
				S.run = r;
				if (r.status !== 'running') { stopRunPoll(); loadStatus(); }
				if (S.idea && S.tab === 'scratchpad') {
					if (r.status !== was) renderDetail(); else updateProgress();
				}
			}).catch(function () { /* keep polling */ });
		}, 2000);
	}

	function stopRunPoll() { clearInterval(pollRun); pollRun = null; }

	function renderDetail() {
		var m = els.main;
		m.textContent = '';
		var idea = S.idea;
		var top = e('div', 'dr-row');
		top.appendChild(btn('← Scratchpad', 'dr-back', function () { stopRunPoll(); S.idea = null; S.run = null; render(); loadList(); }));
		top.appendChild(e('span', 'dr-spacer'));
		var running = S.run && (S.run.status === 'running' || S.run.status === 'needs_input');
		var again = btn(S.run ? 'Draft again' : 'Draft it', 'primary', function () {
			again.disabled = true;
			/* unsaved idea/notes/settings edits go in first, so the new run uses what is on screen */
			(sv.hidden ? Promise.resolve() : saveIdea())
				.then(function () { return startRun(idea.id, 'v3'); })
				.catch(function (err) { again.disabled = false; flashError(err.message); });
		});
		again.disabled = !!running || (S.status && S.status.busy && !running);
		top.appendChild(again);
		var del = btn('Delete', 'danger', function () {
			if (!window.confirm('Delete this idea and all its drafts?')) return;
			api('DELETE', '/ideas/' + idea.id).then(function () { S.idea = null; S.run = null; render(); loadList(); })
				.catch(function (err) { flashError(err.message); });
		});
		del.disabled = !!running;
		top.appendChild(del);
		m.appendChild(top);
		els.detailErr = e('div', 'dr-err');
		els.detailErr.hidden = true;
		m.appendChild(els.detailErr);

		var card = e('div', 'dr-card');
		card.appendChild(e('div', 'dr-hint', 'Raw idea'));
		var ta = e('textarea', 'dr-input');
		ta.value = idea.text; ta.rows = 3; ta.maxLength = 4000;
		ta.setAttribute('aria-label', 'Raw idea text');
		ta.style.marginTop = '.25rem';
		setTimeout(function () { autosize(ta); }, 0);
		idea.notes = idea.notes || '';
		function setKey(v) { return v.audience + '|' + v.post_type + '|' + v.standards.join(','); }
		var set0 = setKey({ audience: idea.audience || 'auto', post_type: idea.post_type || 'auto', standards: idea.standards || STANDARDS.map(function (s) { return s[0]; }) });
		function dirty() { return ta.value.trim() !== idea.text || nb.ta.value.trim() !== idea.notes || setKey(sb.get()) !== set0; }
		ta.addEventListener('input', function () { autosize(ta); sv.hidden = !dirty(); });
		var nb = notesBox(idea.notes, function () { sv.hidden = !dirty(); });
		var sb = settingsBox({ audience: idea.audience, post_type: idea.post_type, standards: idea.standards }, function () { sv.hidden = !dirty(); });
		function saveIdea() {
			var set = sb.get();
			var body = { text: ta.value, notes: nb.ta.value, audience: set.audience, post_type: set.post_type, standards: set.standards };
			return api('PUT', '/ideas/' + idea.id, body).then(function () {
				idea.text = body.text.trim(); idea.notes = body.notes.trim();
				idea.audience = set.audience; idea.post_type = set.post_type; idea.standards = set.standards;
				set0 = setKey(set); sv.hidden = true;
			});
		}
		var sv = btn('Save changes', '', function () { saveIdea().catch(function (err) { flashError(err.message); }); });
		sv.hidden = true;
		card.appendChild(ta);
		card.appendChild(nb.wrap);
		card.appendChild(sb.wrap);
		card.appendChild(sv);
		m.appendChild(card);

		els.runBox = e('div');
		m.appendChild(els.runBox);
		renderRun();
	}

	function flashError(msg) {
		if (!els.detailErr) return alert(msg);
		els.detailErr.textContent = msg;
		els.detailErr.hidden = false;
	}

	function renderRun() {
		var box = els.runBox;
		box.textContent = '';
		var r = S.run;
		if (!r) { box.appendChild(e('div', 'dr-note', 'Not drafted yet. Press Draft it: V3 plans the post, may ask you a few questions, then writes it in English and German.')); return; }
		if (r.status === 'needs_input') return renderQuestions(box, r);
		if (r.status === 'running') {
			var c = e('div', 'dr-card');
			c.appendChild(e('h4', null, 'Drafting…'));
			els.stepList = e('ul', 'dr-steps');
			c.appendChild(els.stepList);
			var bar = e('div', 'dr-bar');
			els.barFill = e('div');
			bar.appendChild(els.barFill);
			c.appendChild(bar);
			els.runMeta = e('div', 'dr-hint');
			c.appendChild(els.runMeta);
			c.appendChild(btn('Cancel', '', function () {
				api('POST', '/runs/' + r.id + '/cancel').then(function () { return api('GET', '/runs/' + r.id); })
					.then(function (x) { S.run = x; stopRunPoll(); renderDetail(); }).catch(function (err) { flashError(err.message); });
			}));
			box.appendChild(c);
			updateProgress();
			return;
		}
		if (r.status === 'failed' || r.status === 'cancelled') {
			var ec = e('div', 'dr-card err');
			ec.appendChild(e('h4', null, r.status === 'failed' ? 'This run failed' : 'This run was cancelled'));
			ec.appendChild(e('div', 'dr-note', r.error || 'Stopped before finishing.'));
			ec.appendChild(e('div', 'dr-hint', 'Completed steps are kept, so Retry only repeats what is missing (' + r.calls + '/' + r.max_calls + ' calls used so far).'));
			var rb = btn('Retry', 'primary', function () {
				rb.disabled = true;
				api('POST', '/runs/' + r.id + '/retry').then(function () { return api('GET', '/runs/' + r.id); })
					.then(function (x) { S.run = x; renderDetail(); startRunPoll(); }).catch(function (err) { rb.disabled = false; flashError(err.message); });
			});
			rb.style.marginTop = '.5rem';
			ec.appendChild(rb);
			box.appendChild(ec);
			if (!r.drafts.some(function (d) { return d.rank; })) return;
		}
		renderResults(box, r);
	}

	function updateProgress() {
		var r = S.run;
		if (!r || !els.stepList) return;
		var steps = r.engine === 'v3' ? STEPS_V3 :
			(r.v2 ? ['Writing 12 variants (6 V1 + 6 V2)', 'Checking drafts', 'Simulating audience reactions', 'Final matchups', 'Polishing the winner'] : STEPS);
		var idx = Math.max(0, steps.indexOf(r.step));
		els.stepList.textContent = '';
		steps.forEach(function (name, i) {
			var li = e('li', i < idx ? 'done' : i === idx ? 'on' : '');
			li.appendChild(e('i'));
			li.appendChild(e('span', null, name));
			els.stepList.appendChild(li);
		});
		els.barFill.style.width = Math.max(4, r.progress) + '%';
		els.runMeta.textContent = r.calls + ' of ' + r.max_calls + ' model calls · $' + r.cost_usd.toFixed(4) + ' · usually 1–2 minutes';
	}

	/* ------------------------------------------------------------ V3: plan + questions before writing */
	function planCard(r, open) {
		var p = (r.meta && r.meta.plan) || {};
		var det = e('details', 'dr-det');
		det.open = !!open;
		det.appendChild(e('summary', null, "V3's plan · for " + label(AUDIENCES, r.meta.audience) + ' · ' + label(POST_TYPES, r.meta.post_type)));
		var c = e('div', 'dr-card');
		if (p.reasoning) c.appendChild(e('div', 'dr-react', p.reasoning));
		if (p.core_line) {
			var cl = e('div', 'dr-react');
			cl.appendChild(e('b', null, 'Core line: '));
			cl.appendChild(document.createTextNode(p.core_line));
			c.appendChild(cl);
		}
		if (p.ending_question) c.appendChild(e('div', 'dr-hint', 'Closing question idea: ' + p.ending_question));
		(r.meta.answers || []).forEach(function (a) {
			c.appendChild(e('div', 'dr-hint', a.ask + ' → ' + (a.answer || '(skipped)')));
		});
		det.appendChild(c);
		return det;
	}

	function renderQuestions(box, r) {
		var qs = (r.meta && r.meta.plan && r.meta.plan.questions) || [];
		box.appendChild(planCard(r, true));
		var c = e('div', 'dr-card');
		c.appendChild(e('h4', null, 'Before writing, V3 wants to know'));
		c.appendChild(e('div', 'dr-hint', 'Real details make the post. Answer what you can and leave the rest empty: V3 will write around a gap instead of inventing it.'));
		var inputs = qs.map(function (q, i) {
			var w = e('div', 'dr-q');
			w.appendChild(e('b', null, (i + 1) + '. ' + q.ask));
			if (q.why) w.appendChild(e('div', 'dr-hint', q.why));
			var t = e('textarea', 'dr-input');
			t.rows = 2; t.maxLength = 800;
			t.setAttribute('aria-label', q.ask);
			t.style.marginTop = '.25rem';
			t.addEventListener('input', function () { autosize(t); });
			w.appendChild(t);
			c.appendChild(w);
			return t;
		});
		var row = e('div', 'dr-row');
		function send(skipAll) {
			go.disabled = skip.disabled = true;
			var answers = inputs.map(function (t) { return skipAll ? '' : t.value.trim(); });
			api('POST', '/runs/' + r.id + '/answers', { answers: answers }).then(function () { return api('GET', '/runs/' + r.id); })
				.then(function (x) { S.run = x; renderDetail(); startRunPoll(); })
				.catch(function (err) { go.disabled = skip.disabled = false; flashError(err.message); });
		}
		var go = btn('Write the post', 'primary', function () { send(false); });
		var skip = btn('Skip all and write', '', function () { send(true); });
		row.appendChild(go);
		row.appendChild(skip);
		row.appendChild(e('span', 'dr-spacer'));
		row.appendChild(btn('Cancel', 'danger', function () {
			api('POST', '/runs/' + r.id + '/cancel').then(function () { return api('GET', '/runs/' + r.id); })
				.then(function (x) { S.run = x; renderDetail(); }).catch(function (err) { flashError(err.message); });
		}));
		c.appendChild(row);
		box.appendChild(c);
		if (inputs[0]) setTimeout(function () { inputs[0].focus(); }, 0);
	}

	/* ------------------------------------------------------------ V3 results: EN + DE + first comment */
	function checkChips(d) {
		var ch = e('div', 'dr-checks');
		STANDARDS.forEach(function (s) {
			var c = d.checks && d.checks[s[0]];
			if (!c) return;
			var chip = e('span', 'dr-ck ' + (c.pass ? 'ok' : 'no'), (c.pass ? '✓ ' : '✗ ') + s[1]);
			chip.title = c.why || s[2];
			ch.appendChild(chip);
		});
		return ch;
	}

	function renderV3Results(box, r) {
		var en = r.drafts.filter(function (d) { return d.lang !== 'de'; }).sort(function (a, b) { return (a.rank || 99) - (b.rank || 99); });
		var de = r.drafts.filter(function (d) { return d.lang === 'de'; })[0];
		box.appendChild(planCard(r, false));
		var pairs = [['English', en[0]], ['Deutsch', de]];
		pairs.forEach(function (pr) {
			if (!pr[1]) return;
			box.appendChild(e('div', 'dr-sec', pr[0]));
			var best = draftCard(pr[1], r, true);
			box.appendChild(best);
			if (pr[1].reaction) {
				var re = e('div', 'dr-react');
				re.appendChild(e('b', null, label(AUDIENCES, r.meta.audience) + ' reader: '));
				re.appendChild(document.createTextNode(pr[1].reaction));
				box.appendChild(re);
			}
			box.appendChild(correctionCard(pr[1], best));
		});
		var fc = r.meta && r.meta.first_comment;
		if (fc && (fc.en || fc.de)) {
			box.appendChild(e('div', 'dr-sec', 'First comment'));
			var c = e('div', 'dr-card');
			c.appendChild(e('div', 'dr-hint', 'Post this as the first comment right after publishing. The link lives here, not in the post, because links in the post body cost reach. Add your real URL in Profile & audience (section LINK) if it shows [link].'));
			[['EN', fc.en], ['DE', fc.de]].forEach(function (x) {
				if (!x[1]) return;
				var row = e('div', 'dr-row');
				row.appendChild(e('span', 'dr-badge', x[0]));
				var t = e('span', 'dr-react', x[1]);
				t.style.flex = '1';
				row.appendChild(t);
				row.appendChild(btn('Copy', '', function (ev) { copyText(x[1], ev.currentTarget); }));
				c.appendChild(row);
			});
			box.appendChild(c);
		}
		if (en.length > 1) {
			box.appendChild(e('div', 'dr-sec', 'Other English drafts'));
			en.slice(1).forEach(function (d) {
				var det = draftDetails(d, false);
				det.querySelector('.dr-card').insertBefore(checkChips(d), det.querySelector('.dr-card').firstChild);
				box.appendChild(det);
			});
		}
		box.appendChild(e('div', 'dr-note', (r.summary && r.summary.disclaimer) || ''));
	}

	function renderResults(box, r) {
		var ranked = r.drafts.filter(function (d) { return d.rank; });
		var others = r.drafts.filter(function (d) { return !d.rank; });
		if (!ranked.length) { box.appendChild(e('div', 'dr-note', 'No drafts passed the quality filters. Try a richer raw idea.')); return; }
		if (r.notes) {
			var used = e('div', 'dr-used');
			used.appendChild(e('b', null, 'Drafted with your notes: '));
			used.appendChild(document.createTextNode(r.notes));
			box.appendChild(used);
		}
		if (r.engine === 'v3') { renderV3Results(box, r); }
		else if (r.v2) { renderCompareResults(box, r, ranked, others); }
		else { renderSingleResults(box, r, ranked, others); }
		box.appendChild(e('div', 'dr-hint', r.calls + ' model calls · $' + r.cost_usd.toFixed(4) + ' · finished ' + ago(r.finished_at)));
	}

	function renderSingleResults(box, r, ranked, others) {
		var w = ranked[0];
		var best = draftCard(w, r, true);
		box.appendChild(best);
		box.appendChild(correctionCard(w, best));

		var why = e('details', 'dr-det');
		why.open = true;
		var sum = e('summary', null, 'Why it won');
		why.appendChild(sum);
		var wb = e('div', 'dr-card');
		(w.reactions || []).forEach(function (x) {
			var p = e('div', 'dr-react');
			p.appendChild(e('b', null, x.persona + ': '));
			p.appendChild(document.createTextNode(x.reaction));
			if (x.flaw) p.appendChild(e('span', null, '  (' + x.flaw + ')'));
			wb.appendChild(p);
		});
		var weak = r.summary && r.summary.weakest_criterion;
		if (weak) wb.appendChild(e('div', 'dr-note', 'Weakest area in the simulation: ' + weak.replace('_', ' ') + '.'));
		if (w.means) {
			wb.appendChild(e('div', 'dr-hint', Object.keys(w.means).map(function (k) { return k.replace('_', ' ') + ' ' + w.means[k].toFixed(1); }).join(' · ') + '  (1–5)'));
		}
		wb.appendChild(e('div', 'dr-note', r.disclaimer));
		why.appendChild(wb);
		box.appendChild(why);

		if (ranked.length > 1) {
			box.appendChild(e('div', 'dr-sec', 'Runners-up'));
			ranked.slice(1, 3).forEach(function (d) { box.appendChild(draftDetails(d, false)); });
		}
		var rest = ranked.slice(3).concat(others);
		if (rest.length) {
			box.appendChild(e('div', 'dr-sec', 'Other drafts'));
			rest.forEach(function (d) { box.appendChild(draftDetails(d, !d.rank)); });
		}
	}

	function renderCompareResults(box, r, ranked, others) {
		box.appendChild(e('div', 'dr-note', r.disclaimer));
		['v1', 'v2'].forEach(function (set, si) {
			var setRanked = ranked.filter(function (d) { return (d.variant_set || 'v1') === set; });
			var setOthers = others.filter(function (d) { return (d.variant_set || 'v1') === set; });
			var label = set === 'v1' ? 'V1 — current prompts' : 'V2 — comment-first prompts';
			box.appendChild(e('div', 'dr-sec', label));
			var w = setRanked[0];
			if (w) {
				var best = draftCard(w, r, true);
				box.appendChild(best);
				box.appendChild(correctionCard(w, best));
				var why = e('details', 'dr-det');
				why.open = true;
				var sum = e('summary', null, 'Why it won');
				why.appendChild(sum);
				var wb = e('div', 'dr-card');
				(w.reactions || []).forEach(function (x) {
					var p = e('div', 'dr-react');
					p.appendChild(e('b', null, x.persona + ': '));
					p.appendChild(document.createTextNode(x.reaction));
					if (x.flaw) p.appendChild(e('span', null, '  (' + x.flaw + ')'));
					wb.appendChild(p);
				});
				if (w.means) {
					wb.appendChild(e('div', 'dr-hint', Object.keys(w.means).map(function (k) { return k.replace('_', ' ') + ' ' + w.means[k].toFixed(1); }).join(' · ') + '  (1–5)'));
				}
				why.appendChild(wb);
				box.appendChild(why);
			}
			var runners = setRanked.slice(1);
			if (runners.length) {
				box.appendChild(e('div', 'dr-sec', 'Runners-up'));
				runners.slice(0, 2).forEach(function (d) { box.appendChild(draftDetails(d, false)); });
			}
			var rest = runners.slice(2).concat(setOthers);
			if (rest.length) {
				box.appendChild(e('div', 'dr-sec', 'Other drafts'));
				rest.forEach(function (d) { box.appendChild(draftDetails(d, !d.rank)); });
			}
		});
	}

	function bestText(d) { return d.edited_text || d.polished_text || d.text; }

	function draftCard(d, r, isBest) {
		var c = e('div', 'dr-card');
		var h = e('h4');
		h.appendChild(e('span', 'dr-badge-ok', 'Best draft'));
		h.appendChild(e('span', 'dr-badge', ANGLE_LABELS[d.angle] || d.angle));
		if (d.posted_at) h.appendChild(e('span', 'dr-badge', 'Posted'));
		if (r.engine === 'v3') h.appendChild(e('span', 'dr-score', d.score10 != null ? d.score10 + ' / 10 as the reader' : ''));
		else h.appendChild(e('span', 'dr-score', Math.round(d.score) + ' / 100 simulated'));
		c.appendChild(h);
		if (d.checks) c.appendChild(checkChips(d));
		var ta = e('textarea', 'dr-input');
		ta.value = showOriginal[d.id] ? d.text : bestText(d);
		ta.setAttribute('aria-label', 'Best draft text');
		setTimeout(function () { autosize(ta); }, 0);
		ta.addEventListener('input', function () { autosize(ta); sv.disabled = false; sv.textContent = 'Save edit'; });
		var ln = lineNumbers(ta);
		c.appendChild(ln.el);
		setTimeout(ln.refresh, 0);
		var chars = e('div', 'dr-hint');
		function upd() {
			chars.textContent = ta.value.length + ' characters · L1, L2… number the lines, so you can say "L3 is too dramatic" in the correction box · LinkedIn cuts the preview after roughly two lines, so the first lines are your hook';
			ln.refresh();
		}
		upd(); ta.addEventListener('input', upd);
		var row = e('div', 'dr-row');
		row.appendChild(btn('Copy', 'primary', function (ev) { copyText(ta.value, ev.currentTarget); }));
		var sv = btn('Save edit', '', function () {
			api('PUT', '/drafts/' + d.id + '/edit', { text: ta.value }).then(function () { d.edited_text = ta.value.trim(); sv.textContent = 'Saved'; sv.disabled = true; })
				.catch(function (err) { flashError(err.message); });
		});
		sv.disabled = true;
		row.appendChild(sv);
		if (d.polished_text) {
			var tg = btn(showOriginal[d.id] ? 'Show polished' : 'Show original', '', function () {
				showOriginal[d.id] = !showOriginal[d.id];
				ta.value = showOriginal[d.id] ? d.text : bestText(d);
				tg.textContent = showOriginal[d.id] ? 'Show polished' : 'Show original';
				autosize(ta); upd();
			});
			row.appendChild(tg);
		}
		row.appendChild(e('span', 'dr-spacer'));
		var pb = btn(d.posted_at ? 'Posted ✓ (undo)' : 'Mark as posted', '', function () {
			api('POST', '/drafts/' + d.id + '/posted', { posted: !d.posted_at }).then(function () {
				d.posted_at = d.posted_at ? null : Math.round(Date.now() / 1000);
				renderRun();
			}).catch(function (err) { flashError(err.message); });
		});
		row.appendChild(pb);
		c.appendChild(chars);
		c.appendChild(row);
		c._ta = ta;
		c._upd = upd;
		c._saved = function () { sv.disabled = true; sv.textContent = 'Saved'; };
		return c;
	}

	/* Line-number gutter for a draft textarea. Numbers count NON-EMPTY lines only (blank lines between paragraphs are
	 * skipped); the backend's numbered_lines() in /refine uses the same rule, so "L3" means the same line on both sides.
	 * A hidden mirror with the textarea's width/font measures where each line starts once wrapping is applied. */
	function lineNumbers(ta) {
		var wrap = e('div', 'dr-lnwrap');
		var gutter = e('div', 'dr-gutter');
		gutter.setAttribute('aria-hidden', 'true');
		var mirror = e('div', 'dr-input dr-lnmirror');
		mirror.setAttribute('aria-hidden', 'true');
		wrap.appendChild(ta);
		wrap.appendChild(gutter);
		wrap.appendChild(mirror);
		function refresh() {
			if (!ta.isConnected || !ta.clientWidth) return;
			var cs = getComputedStyle(ta);
			mirror.style.width = ta.clientWidth + 'px';
			mirror.style.paddingLeft = cs.paddingLeft;
			mirror.textContent = '';
			var lines = ta.value.split('\n'), spans = [];
			lines.forEach(function (l, i) {
				var sp = document.createElement('span');
				sp.textContent = l || '​';
				mirror.appendChild(sp);
				if (i < lines.length - 1) mirror.appendChild(document.createTextNode('\n'));
				if (l.trim()) spans.push(sp);
			});
			gutter.textContent = '';
			var lh = cs.lineHeight;
			spans.forEach(function (sp, n) {
				var g = document.createElement('span');
				g.textContent = 'L' + (n + 1);
				g.style.top = (sp.offsetTop - ta.scrollTop) + 'px';
				g.style.lineHeight = lh;
				gutter.appendChild(g);
			});
		}
		ta.addEventListener('input', refresh);
		ta.addEventListener('scroll', refresh);
		if (window.ResizeObserver) new ResizeObserver(refresh).observe(ta);
		return { el: wrap, refresh: refresh };
	}

	function draftDetails(d, dropped) {
		var det = e('details', 'dr-det');
		var s = e('summary');
		s.appendChild(e('span', 'cv-name', (ANGLE_LABELS[d.angle] || d.angle) + (dropped ? ' · filtered out' : '')));
		if (d.score != null) s.appendChild(e('span', 'dr-score', Math.round(d.score) + ' / 100'));
		det.appendChild(s);
		var b = e('div', 'dr-card');
		if (d.flags && d.flags.length) {
			var ch = e('div', 'dr-chips');
			d.flags.forEach(function (f) { ch.appendChild(e('span', 'dr-flag', f)); });
			b.appendChild(ch);
		}
		b.appendChild(e('div', 'dr-pre', d.polished_text || d.text));
		var row = e('div', 'dr-row');
		row.appendChild(btn('Copy', '', function (ev) { copyText(d.polished_text || d.text, ev.currentTarget); }));
		b.appendChild(row);
		det.appendChild(b);
		return det;
	}

	/* ------------------------------------------------------------ polish correction */
	var PRESETS = [
		['Too fake', 'Too fake: '],
		['Out of context', 'Out of context: '],
		['Less dramatic', 'Less dramatic, tone the hook down. '],
		['Too long', 'Too long: cut it by about a third. '],
		['More like me', 'More like me: plainer words, shorter sentences. '],
		['Fix a line…', 'L1: '],
		['Cut a line…', 'Cut L']
	];

	function correctionCard(d, best) {
		var c = e('div', 'dr-card');
		c.appendChild(e('h4', null, 'Polish correction'));
		c.appendChild(e('div', 'dr-hint', 'Tell the AI what is off: too fake, out of context, a fact it missed. Point at lines with L1, L2… (the numbers next to the draft). It rewrites the draft above, keeps any edits you typed, and remembers the lesson for future drafts.'));

		var box = e('textarea', 'dr-input');
		box.rows = 2; box.maxLength = 1500;
		box.setAttribute('aria-label', 'What should the AI fix?');
		box.placeholder = "L1: too dramatic, say it plainly. L4: I'd never say 'brutal truth'. Cut L6. Out of context: signups aren't live yet, so no result claims.";
		box.style.marginTop = '.375rem';
		box.addEventListener('input', function () { autosize(box); });
		c.appendChild(box);

		var chips = e('div', 'dr-chips');
		chips.style.margin = '.375rem 0';
		PRESETS.forEach(function (p) {
			var b = e('button', 'cv-chip', p[0]);
			b.type = 'button';
			b.addEventListener('click', function () {
				box.value = (box.value ? box.value.replace(/\s+$/, '') + '\n' : '') + p[1];
				autosize(box); box.focus();
			});
			chips.appendChild(b);
		});
		c.appendChild(chips);

		var row = e('div', 'dr-row');
		var msg = e('span', 'dr-hint');
		function setMsg(t, isErr) { msg.textContent = t; msg.style.color = isErr ? 'var(--cv-err-fg)' : ''; }
		var go = btn('Polish correction', 'primary', function () {
			var instruction = box.value.trim();
			if (instruction.length < 3) { setMsg('Say what is off first, even a few words.', true); return; }
			go.disabled = true; go.textContent = 'Polishing…'; setMsg('', false); result.textContent = '';
			api('POST', '/drafts/' + d.id + '/refine', { instruction: instruction, text: best._ta.value }).then(function (r) {
				best._ta.value = r.text; autosize(best._ta); best._upd(); best._saved();
				d.edited_text = r.text; d.revisions = (d.revisions || 0) + 1;
				box.value = ''; autosize(box);
				showResult(r);
				if (hist.open) loadHist();
			}).catch(function (err) { setMsg(err.message, true); })
				.then(function () { go.disabled = false; go.textContent = 'Polish correction'; });
		});
		row.appendChild(go);
		row.appendChild(msg);
		c.appendChild(row);

		var result = e('div');
		c.appendChild(result);

		function showResult(r) {
			result.textContent = '';
			result.appendChild(e('div', 'dr-note', r.changed ? 'Rewrote the draft above. Your own edits were kept.' : 'The AI returned the same text. Try saying more specifically what is wrong.'));
			if (r.lesson) {
				var l = e('div', 'dr-react');
				l.appendChild(e('b', null, 'Saved to memory: '));
				l.appendChild(document.createTextNode(r.lesson + ' '));
				var rm = btn('Remove', 'danger', function () {
					api('DELETE', '/learnings/' + r.learning_id).then(function () { l.remove(); }).catch(function (err) { setMsg(err.message, true); });
				});
				l.appendChild(rm);
				result.appendChild(l);
			}
			var undo = btn('Undo this correction', '', function () {
				undo.disabled = true;
				api('POST', '/revisions/' + r.revision_id + '/undo').then(function (u) {
					best._ta.value = u.text; autosize(best._ta); best._upd(); best._saved();
					d.edited_text = u.text; d.revisions = Math.max(0, (d.revisions || 1) - 1);
					result.textContent = ''; setMsg('Reverted to the version before.', false);
					if (hist.open) loadHist();
				}).catch(function (err) { undo.disabled = false; setMsg(err.message, true); });
			});
			result.appendChild(undo);
		}

		var hist = e('details', 'dr-det');
		var hs = e('summary', null, 'Revision history' + (d.revisions ? ' (' + d.revisions + ')' : ''));
		hist.appendChild(hs);
		var hbody = e('div');
		hist.appendChild(hbody);
		hist.addEventListener('toggle', function () { if (hist.open) loadHist(); });
		c.appendChild(hist);

		function loadHist() {
			hbody.textContent = '';
			hbody.appendChild(e('div', 'dr-hint', 'Loading…'));
			api('GET', '/drafts/' + d.id + '/revisions').then(function (x) {
				hbody.textContent = '';
				hs.textContent = 'Revision history (' + x.revisions.filter(function (v) { return !v.undone; }).length + ')';
				if (!x.revisions.length) { hbody.appendChild(e('div', 'dr-hint', 'No corrections or hand edits yet.')); return; }
				x.revisions.forEach(function (v) {
					var it = e('div', 'dr-card');
					var h = e('h4');
					h.appendChild(e('span', 'dr-badge', v.kind === 'manual' ? 'Your edit' : 'AI correction'));
					if (v.undone) h.appendChild(e('span', 'dr-badge', 'Undone'));
					h.appendChild(e('span', 'dr-score', ago(v.created_at)));
					it.appendChild(h);
					if (v.instruction) it.appendChild(e('div', 'dr-react', '“' + v.instruction + '”'));
					(v.changes || []).slice(0, 2).forEach(function (ch) {
						if (ch.before) it.appendChild(e('div', 'dr-hint', 'was: ' + ch.before.slice(0, 180)));
						if (ch.after) it.appendChild(e('div', 'dr-hint', 'now: ' + ch.after.slice(0, 180)));
					});
					if (v.lesson) it.appendChild(e('div', 'dr-note', 'Lesson: ' + v.lesson));
					if (!v.undone) {
						var rb = btn('Restore the version before this', '', function () {
							rb.disabled = true;
							api('POST', '/revisions/' + v.id + '/undo').then(function (u) {
								best._ta.value = u.text; autosize(best._ta); best._upd(); best._saved();
								d.edited_text = u.text; result.textContent = ''; loadHist();
							}).catch(function (err) { rb.disabled = false; setMsg(err.message, true); });
						});
						it.appendChild(rb);
					}
					hbody.appendChild(it);
				});
			}).catch(function (err) { hbody.textContent = ''; hbody.appendChild(e('div', 'dr-err', err.message)); });
		}
		return c;
	}

	/* ------------------------------------------------------------ weekly review */
	function weekLabel(ws) {
		var d = new Date(ws + 'T00:00:00Z'), e2 = new Date(d.getTime() + 6 * 86400000);
		var f = function (x) { return x.toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' }); };
		return f(d) + ' – ' + f(e2);
	}

	function num(v) {
		if (v === '' || v == null) return null;
		var n = parseInt(v, 10);
		return isNaN(n) ? null : n;
	}

	function renderReview(week) {
		var m = els.main;
		m.textContent = '';
		m.appendChild(e('div', 'dr-note', 'Loading…'));
		api('GET', '/reviews' + (week ? '?week=' + encodeURIComponent(week) : '')).then(function (w) {
			S.review = w;
			drawReview(w);
		}).catch(function (err) {
			m.textContent = '';
			m.appendChild(e('div', 'dr-err', 'Could not load the review: ' + err.message));
		});
	}

	function drawReview(w) {
		var m = els.main;
		m.textContent = '';
		var nav = e('div', 'dr-row');
		nav.appendChild(btn('‹', '', function () { renderReview(w.prev); }));
		nav.appendChild(e('span', 'dr-ptitle', 'Week of ' + weekLabel(w.week_start)));
		nav.appendChild(btn('›', '', function () { renderReview(w.next); }));
		nav.appendChild(e('span', 'dr-spacer'));
		var thisWeek = btn('This week', '', function () { renderReview(); });
		nav.appendChild(thisWeek);
		m.appendChild(nav);
		m.appendChild(e('div', 'dr-note', 'Drop the real numbers from LinkedIn for each post you published. DMs, signups and profile visits matter more than likes, and noting who engaged makes the next drafts better.'));

		var anyResults = false;
		if (!w.posts.length) {
			var em = e('div', 'cv-empty');
			em.style.minHeight = '9rem';
			em.appendChild(e('b', null, 'Nothing posted this week'));
			em.appendChild(e('span', null, 'Open a draft in the Scratchpad and press Mark as posted. It will show up here so you can add its numbers.'));
			m.appendChild(em);
		}
		w.posts.forEach(function (p) {
			if (p.review && (p.review.impressions != null || p.review.reactions != null || p.review.comments != null || p.review.reposts != null || p.review.dms != null || p.review.signups != null)) anyResults = true;
			m.appendChild(reviewCard(p, function () { anyResults = true; analyze.disabled = false; }));
		});

		var arow = e('div', 'dr-row');
		var analyze = btn('Analyze this week', 'primary', function () {
			analyze.disabled = true; analyze.textContent = 'Analyzing…'; msg.textContent = '';
			api('POST', '/reviews/analyze', { week: w.week_start }).then(function () { renderReview(w.week_start); })
				.catch(function (err) { analyze.disabled = false; analyze.textContent = 'Analyze this week'; msg.textContent = err.message; });
		});
		analyze.disabled = !anyResults;
		var msg = e('span', 'dr-err');
		if (w.posts.length) { arow.appendChild(analyze); arow.appendChild(msg); m.appendChild(arow); }

		if (w.insight) {
			var c = e('div', 'dr-card');
			c.appendChild(e('h4', null, 'What the data says'));
			c.appendChild(e('div', 'dr-react', w.insight.summary));
			function list(title, items) {
				if (!items || !items.length) return;
				c.appendChild(e('div', 'dr-hint', title));
				items.forEach(function (t) { c.appendChild(e('div', 'dr-react', '• ' + t)); });
			}
			list('Worked', w.insight.worked);
			list("Didn't", w.insight.didnt);
			if (w.insight.icp_signal) { c.appendChild(e('div', 'dr-hint', 'Audience signal')); c.appendChild(e('div', 'dr-react', w.insight.icp_signal)); }
			if (w.insight.notes && w.insight.notes.length) c.appendChild(e('div', 'dr-note', w.insight.notes.length + ' guideline(s) added below. They are applied to your next drafts.'));
			c.appendChild(e('div', 'dr-note', 'Based on ' + w.insight.posts_analyzed + ' post(s). Small samples are noisy, so treat this as direction, not proof.'));
			m.appendChild(c);
		}

		m.appendChild(e('div', 'dr-sec', 'What the drafter has learned'));
		m.appendChild(e('div', 'dr-note', 'Active guidelines are added to the brief of every draft run. \u270e marks lessons learned from your own corrections (max 10 active); the others come from weekly reviews (max 8). Switch one off if it is wrong; the newest win.'));
		if (!w.learnings.length) m.appendChild(e('div', 'dr-hint', 'Nothing yet. Analyze a week with results to create guidelines.'));
		w.learnings.forEach(function (l) {
			var row = e('div', 'dr-persona');
			var cb = document.createElement('input');
			cb.type = 'checkbox'; cb.checked = !!l.active; cb.style.marginTop = '.375rem';
			cb.setAttribute('aria-label', 'Guideline active');
			cb.addEventListener('change', function () {
				api('PUT', '/learnings/' + l.id, { active: cb.checked }).then(function () { renderReview(w.week_start); })
					.catch(function () { cb.checked = !cb.checked; });
			});
			row.appendChild(cb);
			var t = e('div', 'dr-react', (l.kind === 'correction' ? '\u270e ' : '') + l.note);
			t.style.flex = '1';
			if (!l.active) t.style.opacity = '.55';
			row.appendChild(t);
			var del = btn('Delete', 'danger', function () {
				api('DELETE', '/learnings/' + l.id).then(function () { renderReview(w.week_start); });
			});
			row.appendChild(del);
			m.appendChild(row);
		});

		var cal = w.calibration;
		if (cal) {
			m.appendChild(e('div', 'dr-sec', 'Does the simulation match reality?'));
			m.appendChild(e('div', 'dr-note', cal.message + (cal.spearman != null ? ' (rank correlation ' + cal.spearman + ' over ' + cal.n + ' posts)' : '')));
		}
		m.appendChild(e('div', 'dr-hint', w.disclaimer));
	}

	function reviewCard(p, onSaved) {
		var c = e('div', 'dr-card');
		var h = e('h4');
		h.appendChild(e('span', 'dr-badge', ANGLE_LABELS[p.angle] || p.angle));
		if (p.audience) h.appendChild(e('span', 'dr-badge', 'for ' + label(AUDIENCES, p.audience)));
		if (p.post_type) h.appendChild(e('span', 'dr-badge', label(POST_TYPES, p.post_type)));
		if (p.sim_score != null) h.appendChild(e('span', 'dr-badge', Math.round(p.sim_score) + ' / 100 simulated'));
		h.appendChild(e('span', 'dr-score', 'Posted ' + new Date(p.posted_at * 1000).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })));
		c.appendChild(h);
		var det = e('details', 'dr-det');
		var sm = e('summary');
		sm.appendChild(e('span', 'cv-name', p.text.replace(/\s+/g, ' ').slice(0, 120)));
		det.appendChild(sm);
		det.appendChild(e('div', 'dr-pre', p.text));
		c.appendChild(det);

		var rv = p.review || {};
		var grid = e('div', 'dr-grid');
		var fields = {};
		[['impressions', 'Impressions'], ['reactions', 'Reactions'], ['comments', 'Comments'], ['reposts', 'Reposts'], ['profile_views', 'Profile views'], ['dms', 'DMs'], ['signups', 'Signups']].forEach(function (f) {
			var cell = e('label', 'dr-cell');
			cell.appendChild(e('span', 'dr-hint', f[1]));
			var inp = document.createElement('input');
			inp.type = 'number'; inp.min = '0'; inp.inputMode = 'numeric';
			inp.className = 'dr-input dr-num';
			inp.value = rv[f[0]] != null ? rv[f[0]] : '';
			cell.appendChild(inp);
			fields[f[0]] = inp;
			grid.appendChild(cell);
		});
		c.appendChild(grid);
		var note = e('textarea', 'dr-input');
		note.rows = 2; note.maxLength = 600;
		note.placeholder = 'Who engaged? e.g. 2 brand owners commented, the rest were founder peers. (This matters more than raw reach.)';
		note.value = rv.icp_note || '';
		note.style.marginTop = '.5rem';
		c.appendChild(note);
		var row = e('div', 'dr-row');
		var out = e('span', 'dr-hint');
		var save = btn('Save results', 'primary', function () {
			save.disabled = true;
			var body = { icp_note: note.value };
			Object.keys(fields).forEach(function (k) { body[k] = num(fields[k].value); });
			api('PUT', '/drafts/' + p.draft_id + '/review', body).then(function () {
				out.textContent = 'Saved'; out.style.color = '';
				save.disabled = false; onSaved();
			}).catch(function (err) { out.textContent = err.message; out.style.color = 'var(--cv-err-fg)'; save.disabled = false; });
		});
		row.appendChild(save);
		row.appendChild(out);
		c.appendChild(row);
		return c;
	}

	/* ------------------------------------------------------------ profile & audience */
	function renderProfile() {
		var m = els.main;
		m.textContent = '';
		m.appendChild(e('div', 'dr-note', 'Everything below is sent with every draft run. The brief steers the writing; the personas are the simulated audience that scores the drafts.'));
		m.appendChild(e('div', 'dr-note', 'Tip: paste your LinkedIn headline and About, plus 5–10 recent posts with their results (impressions, comments). LinkedIn cannot be read automatically, so this is where your real profile goes.'));
		m.appendChild(e('div', 'dr-note', 'Loading…'));
		api('GET', '/profile').then(function (p) {
			S.profile = p;
			m.textContent = '';
			m.appendChild(e('div', 'dr-note', 'Everything below is sent with every draft run. The brief steers the writing; the personas are the simulated audience that scores the drafts.'));
			m.appendChild(e('div', 'dr-ptitle', 'Brief: who you are, who you write for, your voice'));
			var brief = e('textarea', 'dr-input');
			brief.value = p.brief; brief.maxLength = 8000;
			brief.setAttribute('aria-label', 'Brief');
			setTimeout(function () { brief.style.minHeight = '16rem'; autosize(brief); }, 0);
			brief.addEventListener('input', function () { autosize(brief); });
			m.appendChild(brief);

			m.appendChild(e('div', 'dr-ptitle', 'Audience personas (1–5), who reads and reacts in the simulation'));
			var list = e('div');
			m.appendChild(list);
			var personas = p.personas.slice();
			function drawPersonas() {
				list.textContent = '';
				personas.forEach(function (txt, i) {
					var row = e('div', 'dr-persona');
					var t = e('textarea', 'dr-input');
					t.rows = 3; t.value = txt; t.maxLength = 600;
					t.setAttribute('aria-label', 'Persona ' + (i + 1));
					t.addEventListener('input', function () { personas[i] = t.value; });
					row.appendChild(t);
					var rm = btn('Remove', 'danger', function () { personas.splice(i, 1); drawPersonas(); });
					rm.disabled = personas.length <= 1;
					row.appendChild(rm);
					list.appendChild(row);
				});
				add.disabled = personas.length >= 5;
			}
			var add = btn('+ Add persona', '', function () { personas.push(''); drawPersonas(); });
			var row = e('div', 'dr-row');
			row.appendChild(add);
			m.appendChild(row);
			drawPersonas();

			var msg = e('div', 'dr-note');
			var actions = e('div', 'dr-row');
			actions.appendChild(btn('Save', 'primary', function () {
				api('PUT', '/profile', { brief: brief.value, personas: personas }).then(function () { msg.textContent = 'Saved. New runs will use this.'; msg.style.color = ''; })
					.catch(function (err) { msg.textContent = err.message; msg.style.color = 'var(--cv-err-fg)'; });
			}));
			actions.appendChild(btn('Reset to defaults', '', function () {
				if (!window.confirm('Replace your brief and personas with the defaults?')) return;
				api('DELETE', '/profile').then(function () { renderProfile(); }).catch(function (err) { msg.textContent = err.message; });
			}));
			m.appendChild(actions);
			m.appendChild(msg);
			if (p.is_default) m.appendChild(e('div', 'dr-hint', 'Currently showing the default brief, drafted from what Proxious knows about you. Edit it and Save to make it yours.'));
		}).catch(function (err) {
			m.textContent = '';
			m.appendChild(e('div', 'dr-err', 'Could not load your profile: ' + err.message));
		});
	}

	/* ------------------------------------------------------------ register */
	function register() {
		if (!window.__cvRegisterPanel) return false;
		window.__cvRegisterPanel('drafter', { build: build, onOpen: onOpen, onClose: onClose });
		return true;
	}
	if (!register()) {
		var tries = 0;
		var t = setInterval(function () { if (register() || ++tries > 100) clearInterval(t); }, 100);
	}
})();

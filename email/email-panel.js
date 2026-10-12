/*
 * email-panel.js — "Email" sidebar panel (#email) for Open WebUI 0.11.4: founder outreach to Berlin CRM leads.
 * Pick a lead (here under "Leads", or "Email with Jimmy" in the Berlin CRM drawer), let Jimmy draft the first email
 * in German or English, attach the sample lead, read it, send. Plain text, no inbox.
 * Registers itself via window.__cvRegisterPanel('email', ...) exposed by crons-tab.js, and exposes
 * window.__emailOpenLead(id) for crm-panel.js.
 * Data: host service mail-send.service (/root/mail-send/mail_send_api.py), Caddy /mail-api/* -> 127.0.0.1:8806.
 *   GET  /mail-api/status   account (from, host; never the password), sends in the last hour
 *   GET  /mail-api/sent     sent log, newest first
 *   GET  /mail-api/leads    Berlin CRM leads with an email (+ emailed_at, blocked)
 *   POST /mail-api/draft    {lead_id, lang, formal, sample, attachments, extra} -> {subject, body}
 *   POST /mail-api/send     {to, cc, bcc, subject, body, lead_id, mark_contacted, attachments:[{name,type,data}]}
 *   POST /mail-api/optout   {address}
 * The text draft is kept in localStorage until it is sent or discarded (attachments are not). Sending always asks.
 * Plain DOM, textContent / value only (never innerHTML with data). Colours come from the shared --cv-* vars.
 */
(function () {
	'use strict';
	if (window.__emailPanelLoaded) return;
	window.__emailPanelLoaded = true;

	var API = '/mail-api';
	var DRAFT_KEY = 'proxious-email-draft';
	var MAX_FILES = 5, MAX_BYTES = 8 * 1024 * 1024;
	var ACCEPT = '.pdf,.csv,.xlsx,.xls,.png,.jpg,.jpeg,.webp,.txt,.docx,.md,.html';
	var ICON_SEND = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5"/></svg>';
	var ICON_CLIP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m18.375 12.739-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13"/></svg>';
	var ICON_SPARK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456Z"/></svg>';

	var CSS =
		'#email-view [hidden]{display:none!important}' +
		'.em-body{max-width:48rem;margin:0 auto;padding:.25rem .5rem 2rem}' +
		'.em-banner{margin:.5rem 0 .75rem;padding:.625rem .75rem;border-radius:.75rem;background:var(--cv-warn-bg);color:var(--cv-warn-fg);font-size:.75rem;line-height:1.125rem}' +
		'.em-banner.err{background:var(--cv-err-bg);color:var(--cv-err-fg)}.em-banner.ok{background:var(--cv-ok-bg);color:var(--cv-ok-fg)}' +
		'.em-banner code{font-family:var(--font-mono,ui-monospace,monospace);font-size:.6875rem;padding:.0625rem .25rem;border-radius:.25rem;background:var(--cv-mono-bg);user-select:all}' +
		'.em-f{display:flex;align-items:center;gap:.5rem;min-height:2.5rem;border-bottom:1px solid var(--cv-line);padding:0 .25rem}' +
		'.em-f label{flex:none;width:3.75rem;font-size:.75rem;color:var(--cv-meta)}' +
		'.em-f input{flex:1;min-width:0;font:inherit;font-size:.875rem;border:0;outline:0;background:transparent;color:var(--cv-name);padding:.5rem 0}' +
		'.em-f input::placeholder,.em-text::placeholder,.em-in::placeholder{color:var(--cv-faint)}' +
		'.em-from{flex:1;min-width:0;font-size:.875rem;color:var(--cv-name);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
		'.em-from.none{color:var(--cv-faint)}' +
		'.em-link{flex:none;font-size:.75rem!important;color:var(--cv-meta)!important;padding:.25rem .375rem!important;border-radius:.5rem}' +
		'.em-link:hover{background:var(--cv-hover);color:var(--cv-name)!important}' +
		'.em-link.danger:hover{color:var(--cv-err-fg)!important}' +
		'.em-text{display:block;width:100%;min-height:40vh;font:inherit;font-size:.9375rem;line-height:1.625rem;border:0;outline:0;background:transparent;color:inherit;resize:none;padding:.875rem .25rem;overflow:hidden}' +
		'.em-files{display:flex;flex-wrap:wrap;gap:.375rem;padding:0 .25rem .625rem}' +
		'.em-file{display:inline-flex;align-items:center;gap:.375rem;max-width:100%;height:1.75rem;padding:0 .25rem 0 .625rem;border-radius:9999px;background:var(--cv-mono-bg);font-size:.75rem;color:var(--cv-name)}' +
		'.em-file span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.em-file small{color:var(--cv-meta);font-size:.6875rem;flex:none}' +
		'.em-file button{width:1.25rem;height:1.25rem;border-radius:9999px;color:var(--cv-meta);flex:none}.em-file button:hover{background:var(--cv-hover);color:var(--cv-err-fg)}' +
		'.em-foot{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;padding:.75rem .25rem 0;border-top:1px solid var(--cv-line)}' +
		'.em-send,.em-jbtn{display:inline-flex;align-items:center;gap:.375rem;height:2rem;padding:0 .875rem!important;border-radius:9999px;font-size:.8125rem;transition:opacity .15s}' +
		'.em-send{background:var(--cv-name)!important;color:var(--color-white,#fff)!important}' +
		'html.dark .em-send{color:var(--color-gray-900,#171717)!important}' +
		'.em-send:hover,.em-jbtn:hover{opacity:.85}.em-send:disabled,.em-jbtn:disabled{opacity:.4;cursor:default}' +
		'.em-attach{display:inline-flex;align-items:center;gap:.25rem}' +
		'.em-send:focus-visible,.em-jbtn:focus-visible,.em-link:focus-visible,.em-row:focus-visible,.em-seg button:focus-visible{outline:2px solid var(--cv-faint);outline-offset:2px}' +
		'.em-state{font-size:.75rem;color:var(--cv-meta)}.em-state.err{color:var(--cv-err-fg)}.em-state.ok{color:var(--cv-ok-fg)}' +
		'.em-check{display:inline-flex;align-items:center;gap:.375rem;font-size:.75rem;color:var(--cv-meta);cursor:pointer}' +
		'.em-quota{margin-left:auto;font-size:.6875rem;color:var(--cv-faint)}' +
		/* Jimmy card */
		'.em-jim{margin:.25rem 0 .75rem;padding:.75rem .875rem;border-radius:1rem;border:1px solid var(--cv-line);background:var(--cv-mono-bg)}' +
		'.em-jhead{display:flex;align-items:flex-start;gap:.5rem}' +
		'.em-jt{flex:1;min-width:0}.em-jt b{display:block;font-weight:500;font-size:.875rem;color:var(--cv-name);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
		'.em-jt span{display:block;font-size:.75rem;color:var(--cv-meta);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
		'.em-need{margin:.5rem 0 .625rem;font-size:.75rem;line-height:1.125rem;color:var(--cv-name);overflow-wrap:anywhere}' +
		'.em-need i{font-style:normal;color:var(--cv-meta)}' +
		'.em-jrow{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin-top:.5rem}' +
		'.em-seg{display:inline-flex;padding:.125rem;border-radius:.625rem;background:var(--cv-hover)}' +
		'.em-seg button{height:1.625rem;padding:0 .625rem!important;border-radius:.5rem;font-size:.75rem;color:var(--cv-meta)!important}' +
		'.em-seg button[aria-pressed=true]{background:var(--cv-bg,var(--color-white,#fff));color:var(--cv-name)!important;box-shadow:0 1px 2px rgb(0 0 0 / .08)}' +
		'html.dark .em-seg button[aria-pressed=true]{background:var(--color-gray-800,#333)}' +
		'.em-in{flex:1 1 14rem;min-width:0;height:2rem;font:inherit;font-size:.8125rem;padding:0 .625rem;border-radius:.625rem;border:1px solid var(--cv-line);background:transparent;color:var(--cv-name);outline:0}' +
		'.em-in:focus{border-color:var(--cv-faint)}' +
		'.em-jbtn{border:1px solid var(--cv-line)!important;color:var(--cv-name)!important}' +
		'.em-jmeta{font-size:.6875rem;color:var(--cv-faint)}.em-jmeta.err{color:var(--cv-err-fg)}' +
		'.em-warn{margin-top:.5rem;font-size:.75rem;color:var(--cv-warn-fg)}.em-warn.err{color:var(--cv-err-fg)}' +
		/* lists */
		'.em-g{margin:1.25rem .5rem .375rem;font-size:.75rem;font-weight:500;color:var(--cv-meta)}' +
		'.em-row{display:flex;align-items:flex-start;gap:.75rem;width:100%;padding:.5rem .625rem;border-radius:.75rem;text-align:left;cursor:pointer;transition:background-color .15s}' +
		'.em-row:hover,.em-item.open>.em-row{background:var(--cv-hover)}' +
		'.em-row.off{opacity:.55}' +
		'.em-main{flex:1;min-width:0}' +
		'.em-t{color:var(--cv-name);font-size:.875rem;line-height:1.25rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
		'.em-s{color:var(--cv-meta);font-size:.75rem;line-height:1.125rem;margin-top:.125rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
		'.em-when{flex:none;font-size:.6875rem;line-height:1.25rem;color:var(--cv-faint);white-space:nowrap;text-align:right}' +
		'.em-when.done{color:var(--cv-ok-fg)}.em-when.off{color:var(--cv-err-fg)}' +
		'.em-lsearch{display:flex;gap:.5rem;align-items:center;margin:.5rem .25rem .25rem}' +
		'.em-detail{margin:.125rem .5rem .5rem;padding:.75rem;border-radius:.75rem;background:var(--cv-mono-bg);font-size:.75rem;line-height:1.125rem}' +
		'.em-kv{display:grid;grid-template-columns:auto 1fr;gap:.125rem .75rem;margin:0 0 .625rem}' +
		'.em-kv dt{color:var(--cv-meta)}.em-kv dd{margin:0;min-width:0;overflow-wrap:anywhere;color:var(--cv-name)}' +
		'.em-msg{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8125rem;line-height:1.375rem;color:var(--cv-name);margin:0 0 .625rem}' +
		'.em-acts{display:flex;flex-wrap:wrap;gap:.25rem}' +
		'.em-empty{display:flex;min-height:calc(100dvh - 12rem);flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:.375rem}' +
		'.em-empty b{font-weight:400;font-size:.875rem;color:var(--cv-name)}' +
		'.em-empty span{font-size:.75rem;line-height:1.25rem;color:var(--cv-meta);max-width:24rem}' +
		'@media (max-width:767px){.em-body{padding:.25rem .25rem 2rem}.em-f label{width:3rem}.em-quota{margin-left:0;width:100%}}';

	var H = null; /* host helpers from crons-tab.js */
	var root = null, els = {};
	var tab = 'compose', acct = null, acctError = null, sent = null, sentError = null, sending = false, openId = null;
	var leads = null, leadsError = null, leadQuery = '', pendingLead = null;
	/* compose state that is not a text field */
	var lead = null, lang = 'de', formal = true, files = [], drafting = false, markCrm = true;

	function e(tag, cls, text) {
		var n = document.createElement(tag);
		if (cls) n.className = cls;
		if (text != null) n.textContent = text;
		return n;
	}

	function btn(cls, text, onClick) {
		var b = e('button', cls, text);
		b.type = 'button';
		if (onClick) b.addEventListener('click', onClick);
		return b;
	}

	function headers(json) {
		var h = { Accept: 'application/json', 'X-Requested-With': 'mail' };
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
		}, function () { return { ok: false, _status: 0, error: 'Lost connection.' }; });
	}

	/* ---------------- dates ---------------- */
	function ts(iso) { var d = new Date(iso); return isNaN(d) ? 0 : d.getTime(); }

	function ago(iso) {
		var t = ts(iso);
		if (!t) return '';
		var s = Math.max(0, Math.round((Date.now() - t) / 1000));
		if (s < 45) return 'just now';
		if (s < 3600) return Math.round(s / 60) + 'm ago';
		if (s < 86400) return Math.round(s / 3600) + 'h ago';
		if (s < 86400 * 7) return Math.round(s / 86400) + 'd ago';
		try { return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }); } catch (x) { return ''; }
	}

	function full(iso) {
		var t = ts(iso);
		if (!t) return '';
		try { return new Date(t).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); } catch (x) { return iso; }
	}

	function bucket(iso) {
		var t = ts(iso), d = new Date();
		d.setHours(0, 0, 0, 0);
		var day = 86400000, start = d.getTime();
		if (t >= start) return 'Today';
		if (t >= start - day) return 'Yesterday';
		if (t >= start - 7 * day) return 'Previous 7 days';
		if (t >= start - 30 * day) return 'Previous 30 days';
		var nd = new Date(t);
		try {
			return nd.getFullYear() === d.getFullYear()
				? nd.toLocaleDateString(undefined, { month: 'long' })
				: nd.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
		} catch (x) { return String(nd.getFullYear()); }
	}

	function size(n) { return n < 1024 ? n + ' B' : n < 1048576 ? Math.round(n / 1024) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }

	/* ---------------- draft (per browser) ---------------- */
	var FIELDS = ['to', 'cc', 'bcc', 'subject', 'body', 'sample', 'extra'];

	function readDraft() {
		try { var d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); return d && typeof d === 'object' ? d : {}; } catch (x) { return {}; }
	}

	function hasText(d) { return !!((d.to || '') + (d.subject || '') + (d.body || '')).trim(); }

	function saveDraft() {
		if (!els.to) return;
		var d = { lead: lead, lang: lang, formal: formal, mark: markCrm }, any = !!lead;
		FIELDS.forEach(function (k) { d[k] = els[k].value; if (d[k].trim()) any = true; });
		try { if (any) localStorage.setItem(DRAFT_KEY, JSON.stringify(d)); else localStorage.removeItem(DRAFT_KEY); } catch (x) { /* storage blocked */ }
	}

	function fillDraft(d) {
		FIELDS.forEach(function (k) { els[k].value = d[k] || ''; });
		lead = d.lead && typeof d.lead === 'object' ? d.lead : null;
		lang = d.lang === 'en' ? 'en' : 'de';
		formal = d.formal !== false;
		markCrm = d.mark !== false;
		showCc(!!((d.cc || '').trim() || (d.bcc || '').trim()));
		grow();
		paintJimmy();
		saveDraft();
	}

	/* ---------------- compose ---------------- */
	function grow() {
		if (!els.body) return;
		els.body.style.height = 'auto';
		els.body.style.height = els.body.scrollHeight + 'px';
	}

	function field(label, key, placeholder) {
		var row = e('div', 'em-f');
		var id = 'em-' + key;
		var l = e('label', null, label);
		l.htmlFor = id;
		var inp = e('input');
		inp.id = id;
		inp.type = 'text';
		inp.autocomplete = 'off';
		inp.spellcheck = key === 'subject';
		if (placeholder) inp.placeholder = placeholder;
		inp.maxLength = key === 'subject' ? 300 : 2000;
		inp.addEventListener('input', function () { setState('', ''); saveDraft(); if (key === 'to') paintWarn(); });
		row.appendChild(l);
		row.appendChild(inp);
		els[key] = inp;
		els[key + 'Row'] = row;
		return row;
	}

	function showCc(on) {
		els.ccRow.hidden = els.bccRow.hidden = !on;
		els.ccBtn.hidden = on;
	}

	function setState(text, kind) {
		if (!els.state) return;
		els.state.textContent = text;
		els.state.className = 'em-state' + (kind ? ' ' + kind : '');
	}

	function seg(options, get, set) {
		var s = e('div', 'em-seg');
		s.setAttribute('role', 'group');
		var bs = options.map(function (o) {
			var b = btn(null, o[1], function () { set(o[0]); paint(); saveDraft(); });
			s.appendChild(b);
			return [o[0], b];
		});
		function paint() { bs.forEach(function (p) { p[1].setAttribute('aria-pressed', String(get() === p[0])); }); }
		paint();
		s._paint = paint;
		return s;
	}

	function buildJimmy(box) {
		var c = e('div', 'em-jim');
		c.hidden = true;
		var head = e('div', 'em-jhead');
		var t = e('div', 'em-jt');
		els.jName = e('b');
		els.jSub = e('span');
		t.appendChild(els.jName);
		t.appendChild(els.jSub);
		head.appendChild(t);
		head.appendChild(btn('em-link', 'Change lead', function () { setTab('leads'); }));
		head.appendChild(btn('em-link', 'Remove', function () { lead = null; paintJimmy(); saveDraft(); }));
		c.appendChild(head);
		els.jNeed = e('div', 'em-need');
		c.appendChild(els.jNeed);

		var r1 = e('div', 'em-jrow');
		els.segLang = seg([['de', 'Deutsch'], ['en', 'English']], function () { return lang; }, function (v) { lang = v; els.segForm.hidden = v !== 'de'; });
		r1.appendChild(els.segLang);
		els.segForm = seg([[true, 'Sie'], [false, 'du']], function () { return formal; }, function (v) { formal = v; });
		r1.appendChild(els.segForm);
		c.appendChild(r1);

		var r2 = e('div', 'em-jrow');
		els.sample = e('input', 'em-in');
		els.sample.type = 'text';
		els.sample.maxLength = 400;
		els.sample.placeholder = 'Sample lead you attach, e.g. “Berlin food creator, café reels on TikTok”';
		els.sample.setAttribute('aria-label', 'Describe the sample lead you attach');
		els.sample.addEventListener('input', saveDraft);
		r2.appendChild(els.sample);
		c.appendChild(r2);

		var r3 = e('div', 'em-jrow');
		els.extra = e('input', 'em-in');
		els.extra.type = 'text';
		els.extra.maxLength = 400;
		els.extra.placeholder = 'Note for Jimmy (optional), e.g. “shorter”, “mention their candles”';
		els.extra.setAttribute('aria-label', 'Note for Jimmy');
		els.extra.addEventListener('input', saveDraft);
		r3.appendChild(els.extra);
		els.jBtn = btn('em-jbtn', null, doDraft);
		els.jBtn.innerHTML = ICON_SPARK; /* static trusted SVG */
		els.jBtnLbl = e('span', null, 'Draft with Jimmy');
		els.jBtn.appendChild(els.jBtnLbl);
		r3.appendChild(els.jBtn);
		c.appendChild(r3);
		els.jMeta = e('div', 'em-jmeta');
		els.jMeta.style.marginTop = '.5rem';
		c.appendChild(els.jMeta);
		els.jWarn = e('div', 'em-warn');
		c.appendChild(els.jWarn);
		els.jim = c;
		box.appendChild(c);
	}

	function paintJimmy() {
		if (!els.jim) return;
		els.jim.hidden = !lead;
		els.markWrap.hidden = !lead;
		els.mark.checked = markCrm;
		if (!lead) { paintWarn(); return; }
		els.jName.textContent = lead.company || 'Lead #' + lead.id;
		els.jSub.textContent = [lead.email, lead.city, lead.category].filter(Boolean).join(' · ');
		els.jNeed.textContent = '';
		els.jNeed.appendChild(e('i', null, 'Their post: '));
		els.jNeed.appendChild(document.createTextNode(lead.need || '—'));
		els.segLang._paint();
		els.segForm._paint();
		els.segForm.hidden = lang !== 'de';
		els.jBtn.disabled = drafting;
		els.jBtnLbl.textContent = drafting ? 'Jimmy is writing…' : (els.body.value.trim() ? 'Redraft with Jimmy' : 'Draft with Jimmy');
		paintWarn();
	}

	/* warnings: already emailed / opted out (from the leads list, so it also covers addresses typed by hand) */
	function paintWarn() {
		if (!els.jWarn) return;
		var w = '', bad = false, to = (els.to.value || '').toLowerCase();
		var cur = lead && leads ? leads.filter(function (l) { return l.id === lead.id; })[0] : null;
		if (!cur && leads && to) cur = leads.filter(function (l) { return to.indexOf(l.email.toLowerCase()) !== -1; })[0];
		if (cur && cur.blocked) { w = cur.email + ' asked not to be contacted. Sending is blocked.'; bad = true; }
		else if (cur && cur.emailed_at) w = 'You already emailed ' + (cur.company || cur.email) + ' ' + ago(cur.emailed_at) + ' (' + full(cur.emailed_at) + ').';
		els.jWarn.textContent = w;
		els.jWarn.className = 'em-warn' + (bad ? ' err' : '');
		els.jWarn.hidden = !w;
		if (!lead && w) { els.jim.hidden = true; } /* the card is only for leads; keep the warning visible below instead */
		els.toWarn.textContent = !lead ? w : '';
		els.toWarn.className = 'em-warn' + (bad ? ' err' : '');
		els.toWarn.hidden = !!lead || !w;
	}

	function doDraft() {
		if (!lead || drafting) return;
		if (els.body.value.trim() && !window.confirm('Replace the current subject and text with a new draft from Jimmy?')) return;
		drafting = true;
		els.jMeta.textContent = '';
		els.jMeta.className = 'em-jmeta';
		paintJimmy();
		api('POST', '/draft', {
			lead_id: lead.id, lang: lang, formal: formal, sample: els.sample.value, extra: els.extra.value,
			attachments: files.map(function (f) { return f.name; })
		}).then(function (j) {
			drafting = false;
			if (j.ok) {
				if (j.to && !els.to.value.trim()) els.to.value = j.to;
				els.subject.value = j.subject || '';
				els.body.value = j.body || '';
				grow();
				saveDraft();
				els.jMeta.textContent = 'Drafted in ' + j.seconds + ' s · $' + (j.cost || 0).toFixed(4) + ' · read it, change what you like, then send.';
				els.body.focus();
				els.body.setSelectionRange(0, 0);
			} else {
				els.jMeta.textContent = j.error || 'Jimmy could not draft (' + j._status + ')';
				els.jMeta.className = 'em-jmeta err';
			}
			paintJimmy();
		});
	}

	function useLead(l) {
		if (l.blocked) { window.alert((l.company || l.email) + ' asked not to be contacted (do-not-contact list).'); setTab('leads'); return; }
		var d = readDraft();
		if (hasText(d) && (!lead || lead.id !== l.id) && !window.confirm('Start a new email to ' + (l.company || l.email) + '? Your current draft will be replaced.')) return;
		var keep = lead && lead.id === l.id;
		lead = { id: l.id, company: l.company, email: l.email, city: l.city, category: l.category, need: l.need };
		if (!keep) {
			['cc', 'bcc', 'subject', 'body', 'extra'].forEach(function (k) { els[k].value = ''; });
			files = [];
			paintFiles();
			els.jMeta.textContent = '';
			markCrm = true;
		}
		els.to.value = l.email;
		setTab('compose');
		paintJimmy();
		saveDraft();
		setState('', '');
		els.sample.focus();
	}

	/* ---------------- attachments ---------------- */
	function paintFiles() {
		if (!els.files) return;
		els.files.textContent = '';
		els.files.hidden = !files.length;
		files.forEach(function (f, i) {
			var c = e('span', 'em-file');
			c.title = f.name;
			c.appendChild(e('span', null, f.name));
			c.appendChild(e('small', null, size(f.size)));
			var x = btn(null, '×', function () { files.splice(i, 1); paintFiles(); });
			x.setAttribute('aria-label', 'Remove ' + f.name);
			c.appendChild(x);
			els.files.appendChild(c);
		});
	}

	function addFiles(list) {
		var total = files.reduce(function (s, f) { return s + f.size; }, 0);
		Array.prototype.forEach.call(list, function (file) {
			if (files.length >= MAX_FILES) { setState('At most ' + MAX_FILES + ' attachments.', 'err'); return; }
			if (total + file.size > MAX_BYTES) { setState(file.name + ' is too large (8 MB together).', 'err'); return; }
			var ext = (file.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
			if (ACCEPT.split(',').indexOf(ext) === -1) { setState(file.name + ': this file type is not allowed.', 'err'); return; }
			total += file.size;
			var entry = { name: file.name, type: file.type, size: file.size, data: null };
			files.push(entry);
			var rd = new FileReader();
			rd.onload = function () { entry.data = String(rd.result).split(',')[1] || ''; };
			rd.onerror = function () { files = files.filter(function (f) { return f !== entry; }); paintFiles(); setState('Could not read ' + file.name, 'err'); };
			rd.readAsDataURL(file);
		});
		paintFiles();
	}

	/* ---------------- compose layout ---------------- */
	function buildCompose(box) {
		els.banner = e('div', 'em-banner');
		els.banner.hidden = true;
		box.appendChild(els.banner);
		buildJimmy(box);

		var from = e('div', 'em-f');
		from.appendChild(e('label', null, 'From'));
		els.from = e('div', 'em-from none', '…');
		from.appendChild(els.from);
		box.appendChild(from);

		var toRow = field('To', 'to', 'name@example.com, other@example.com');
		els.ccBtn = btn('em-link', 'Cc / Bcc', function () { showCc(true); els.cc.focus(); });
		toRow.appendChild(els.ccBtn);
		box.appendChild(toRow);
		els.toWarn = e('div', 'em-warn');
		els.toWarn.hidden = true;
		box.appendChild(els.toWarn);
		box.appendChild(field('Cc', 'cc'));
		box.appendChild(field('Bcc', 'bcc'));
		box.appendChild(field('Subject', 'subject'));

		els.body = e('textarea', 'em-text');
		els.body.placeholder = 'Write your message, or pick a lead under “Leads” and let Jimmy draft it…';
		els.body.setAttribute('aria-label', 'Message');
		els.body.spellcheck = true;
		els.body.addEventListener('input', function () { grow(); setState('', ''); saveDraft(); });
		box.appendChild(els.body);

		els.files = e('div', 'em-files');
		els.files.hidden = true;
		box.appendChild(els.files);

		var foot = e('div', 'em-foot');
		els.send = btn('em-send', null, doSend);
		els.send.innerHTML = ICON_SEND; /* static trusted SVG */
		els.send.appendChild(e('span', null, 'Send'));
		els.send.title = 'Send (Ctrl+Enter)';
		foot.appendChild(els.send);
		var picker = e('input');
		picker.type = 'file';
		picker.multiple = true;
		picker.accept = ACCEPT;
		picker.hidden = true;
		picker.addEventListener('change', function () { addFiles(picker.files); picker.value = ''; });
		foot.appendChild(picker);
		var attach = btn('em-link em-attach', null, function () { picker.click(); });
		attach.innerHTML = ICON_CLIP; /* static trusted SVG */
		attach.appendChild(e('span', null, 'Attach'));
		foot.appendChild(attach);
		foot.appendChild(btn('em-link', 'Discard', function () {
			if ((hasText(readDraft()) || files.length) && !window.confirm('Discard this draft?')) return;
			files = [];
			paintFiles();
			fillDraft({});
			if (els.jMeta) els.jMeta.textContent = '';
			setState('', '');
			els.to.focus();
		}));
		els.markWrap = e('label', 'em-check');
		els.mark = e('input');
		els.mark.type = 'checkbox';
		els.mark.addEventListener('change', function () { markCrm = els.mark.checked; saveDraft(); });
		els.markWrap.appendChild(els.mark);
		els.markWrap.appendChild(document.createTextNode('Mark as contacted in Berlin CRM'));
		els.markWrap.hidden = true;
		foot.appendChild(els.markWrap);
		els.state = e('span', 'em-state');
		els.state.setAttribute('role', 'status');
		foot.appendChild(els.state);
		els.quota = e('span', 'em-quota');
		foot.appendChild(els.quota);
		box.appendChild(foot);

		box.addEventListener('keydown', function (ev) {
			if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { ev.preventDefault(); doSend(); }
		});
		box.addEventListener('dragover', function (ev) { if (ev.dataTransfer && ev.dataTransfer.types.indexOf('Files') !== -1) ev.preventDefault(); });
		box.addEventListener('drop', function (ev) {
			if (ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files.length) { ev.preventDefault(); addFiles(ev.dataTransfer.files); }
		});
		fillDraft(readDraft());
		paintAccount();
	}

	function paintAccount() {
		if (!els.from) return;
		var b = els.banner;
		b.textContent = '';
		b.className = 'em-banner';
		b.hidden = true;
		if (acctError) {
			b.className = 'em-banner err';
			b.textContent = acctError;
			b.hidden = false;
		} else if (acct && !acct.configured && !acct.dry_run) {
			b.appendChild(document.createTextNode('No mail account is set up yet, so you can draft but not send. On the server run '));
			b.appendChild(e('code', null, 'python3 /root/mail-send/set_smtp.py'));
			b.appendChild(document.createTextNode(' and enter your SMTP login (for Gmail: an App Password). It is checked before it is saved; no restart needed.'));
			b.hidden = false;
		}
		if (acct && (acct.configured || acct.dry_run)) {
			els.from.textContent = acct.from ? (acct.from_name ? acct.from_name + ' <' + acct.from + '>' : acct.from) : '(test mode)';
			els.from.className = 'em-from';
			els.from.title = acct.host ? 'via ' + acct.host : '';
		} else {
			els.from.textContent = acct ? 'Not set up' : '…';
			els.from.className = 'em-from none';
		}
		els.send.disabled = sending || !acct || !(acct.configured || acct.dry_run);
		els.quota.textContent = acct ? acct.sent_last_hour + ' / ' + acct.limit_per_hour + ' this hour' : '';
	}

	function count(v) {
		return v.split(/[,;\n]/).filter(function (x) { return x.trim(); }).length;
	}

	function doSend() {
		if (sending || !els.send || els.send.disabled) return;
		var msg = {};
		['to', 'cc', 'bcc', 'subject', 'body'].forEach(function (k) { msg[k] = els[k].value; });
		if (!msg.to.trim()) { setState('Add a recipient in To.', 'err'); els.to.focus(); return; }
		if (!msg.subject.trim() && !msg.body.trim()) { setState('Write a subject or a message first.', 'err'); return; }
		if (files.some(function (f) { return f.data == null; })) { setState('Still reading the attachment, try again in a second.', 'err'); return; }
		var n = count(msg.to) + count(msg.cc) + count(msg.bcc);
		var q = 'Send “' + (msg.subject.trim() || '(no subject)') + '”\nto ' + msg.to.trim() +
			(n > count(msg.to) ? '\n(+' + (n - count(msg.to)) + ' in Cc/Bcc)' : '') +
			(files.length ? '\nwith ' + files.map(function (f) { return f.name; }).join(', ') : '\nwithout attachment') + '?';
		if (!window.confirm(q)) return;
		if (lead) { msg.lead_id = lead.id; msg.mark_contacted = markCrm; }
		msg.attachments = files.map(function (f) { return { name: f.name, type: f.type, data: f.data }; });
		sending = true;
		paintAccount();
		setState('Sending…', '');
		api('POST', '/send', msg).then(function (j) {
			sending = false;
			if (j.ok) {
				var who = lead ? (lead.company || lead.email) : n + ' recipient' + (n === 1 ? '' : 's');
				files = [];
				paintFiles();
				fillDraft({});
				if (els.jMeta) els.jMeta.textContent = '';
				setState('Sent to ' + who + '.' + (j.crm_updated ? ' Marked as contacted in Berlin CRM.' : j.crm_updated === false ? ' (Berlin CRM was not updated.)' : ''), j.crm_updated === false ? 'err' : 'ok');
				if (sent) sent.unshift(j.message);
				if (acct) acct.sent_last_hour++;
				loadLeads();
			} else {
				setState(j.error || 'Not sent (' + j._status + ')', 'err');
			}
			paintAccount();
		});
	}

	/* ---------------- leads ---------------- */
	function loadLeads() {
		return api('GET', '/leads').then(function (j) {
			if (j.ok) { leads = j.leads || []; leadsError = null; } else leadsError = j.error || 'Could not load leads (' + j._status + ')';
			if (pendingLead != null) openPending();
			if (tab === 'leads') render();
			paintTabs();
			paintWarn();
		});
	}

	function openPending() {
		if (!leads) return;
		var id = pendingLead;
		pendingLead = null;
		var l = leads.filter(function (x) { return x.id === id; })[0];
		if (l) useLead(l);
		else { setTab('leads'); leadsError = 'That lead has no valid email address in Berlin CRM.'; render(); }
	}

	function renderLeads(box) {
		var top = e('div', 'em-lsearch');
		var s = e('div', 'cv-search');
		s.style.flex = '1';
		s.insertAdjacentHTML('beforeend', H.icons.search); /* static trusted SVG from the host */
		var inp = e('input');
		inp.type = 'text';
		inp.placeholder = 'Search company, city, what they need';
		inp.setAttribute('aria-label', 'Search leads');
		inp.value = leadQuery;
		inp.addEventListener('input', function () { leadQuery = inp.value; paintLeadRows(); });
		s.appendChild(inp);
		top.appendChild(s);
		box.appendChild(top);
		if (leadsError) box.appendChild(e('div', 'em-banner err', leadsError));
		els.leadRows = e('div');
		box.appendChild(els.leadRows);
		paintLeadRows();
		setTimeout(function () { inp.focus(); }, 0);
	}

	function paintLeadRows() {
		var box = els.leadRows;
		if (!box) return;
		box.textContent = '';
		if (!leads) { if (!leadsError) box.appendChild(e('div', 'em-empty', 'Loading…')); return; }
		var q = leadQuery.trim().toLowerCase();
		var shown = leads.filter(function (l) {
			return !q || [l.company, l.city, l.need, l.category, l.email].join(' ').toLowerCase().indexOf(q) !== -1;
		});
		if (!shown.length) {
			var em = e('div', 'em-empty');
			em.appendChild(e('b', null, q ? 'No lead matches' : 'No leads with an email yet'));
			em.appendChild(e('span', null, q ? 'Try another word.' : 'Leads come from Berlin CRM.'));
			box.appendChild(em);
			return;
		}
		var fresh = shown.filter(function (l) { return !l.emailed_at && !l.blocked; });
		var done = shown.filter(function (l) { return l.emailed_at || l.blocked; });
		[['Not emailed yet', fresh], ['Already emailed', done]].forEach(function (g) {
			if (!g[1].length) return;
			box.appendChild(e('div', 'em-g', g[0] + ' · ' + g[1].length));
			g[1].forEach(function (l) {
				var r = e('div', 'em-row' + (l.blocked ? ' off' : ''));
				r.setAttribute('role', 'button');
				r.tabIndex = 0;
				var m = e('div', 'em-main');
				m.appendChild(e('div', 'em-t', l.company || l.email));
				m.appendChild(e('div', 'em-s', [l.city, l.need].filter(Boolean).join(' · ')));
				r.appendChild(m);
				var w = e('div', 'em-when' + (l.blocked ? ' off' : l.emailed_at ? ' done' : ''),
					l.blocked ? 'opted out' : l.emailed_at ? 'emailed ' + ago(l.emailed_at) : l.status);
				if (l.emailed_at) w.title = full(l.emailed_at);
				r.appendChild(w);
				var go = function () { useLead(l); };
				r.addEventListener('click', go);
				r.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } });
				box.appendChild(r);
			});
		});
	}

	/* ---------------- sent ---------------- */
	function renderSent(box) {
		if (sentError) box.appendChild(e('div', 'em-banner err', sentError));
		if (!sent) {
			if (!sentError) box.appendChild(e('div', 'em-empty', 'Loading…'));
			return;
		}
		if (!sent.length) {
			var em = e('div', 'em-empty');
			em.appendChild(e('b', null, 'Nothing sent yet'));
			em.appendChild(e('span', null, 'Emails you send from this tab show up here.'));
			box.appendChild(em);
			return;
		}
		var group = null;
		sent.forEach(function (m) {
			var g = bucket(m.sent_at);
			if (g !== group) { group = g; box.appendChild(e('div', 'em-g', g)); }
			var item = e('div', 'em-item' + (openId === m.id ? ' open' : ''));
			var r = e('div', 'em-row');
			r.setAttribute('role', 'button');
			r.setAttribute('aria-expanded', String(openId === m.id));
			r.tabIndex = 0;
			var main = e('div', 'em-main');
			main.appendChild(e('div', 'em-t', m.subject || '(no subject)'));
			main.appendChild(e('div', 'em-s', 'To ' + m.to + ((m.attachments || []).length ? ' · 📎 ' + m.attachments.length : '') + (m.dry_run ? ' · test, not delivered' : '')));
			r.appendChild(main);
			var w = e('div', 'em-when', ago(m.sent_at));
			w.title = full(m.sent_at);
			r.appendChild(w);
			var toggle = function () { openId = openId === m.id ? null : m.id; render(); };
			r.addEventListener('click', toggle);
			r.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); } });
			item.appendChild(r);
			if (openId === m.id) item.appendChild(detail(m));
			box.appendChild(item);
		});
	}

	function detail(m) {
		var d = e('div', 'em-detail');
		var kv = e('dl', 'em-kv');
		var att = (m.attachments || []).map(function (a) { return a.name + ' (' + size(a.size) + ')'; }).join(', ');
		[['From', m.from], ['To', m.to], ['Cc', m.cc], ['Bcc', m.bcc], ['Reply-To', m.reply_to], ['Attached', att], ['Sent', full(m.sent_at)]].forEach(function (p) {
			if (!p[1]) return;
			kv.appendChild(e('dt', null, p[0]));
			kv.appendChild(e('dd', null, p[1]));
		});
		d.appendChild(kv);
		if (m.body) d.appendChild(e('p', 'em-msg', m.body));
		var acts = e('div', 'em-acts');
		acts.appendChild(btn('em-link', 'Use as new draft', function () {
			if (hasText(readDraft()) && !window.confirm('Replace your current draft?')) return;
			setTab('compose');
			fillDraft({ to: m.to, cc: m.cc, bcc: m.bcc, subject: m.subject, body: m.body });
			els.to.focus();
		}));
		acts.appendChild(btn('em-link danger', 'They asked not to be contacted', function () {
			var addrs = m.to.split(',').map(function (a) { var x = a.match(/<([^>]+)>/); return (x ? x[1] : a).trim(); }).filter(Boolean);
			if (!window.confirm('Put ' + addrs.join(', ') + ' on the do-not-contact list?\nThe Email tab will refuse to send to ' + (addrs.length > 1 ? 'them' : 'this address') + ' again.')) return;
			Promise.all(addrs.map(function (a) { return api('POST', '/optout', { address: a }); })).then(function (rs) {
				var bad = rs.filter(function (j) { return !j.ok; })[0];
				window.alert(bad ? (bad.error || 'Could not save') : 'Saved. They will not be emailed from here again.');
				loadLeads();
			});
		}));
		d.appendChild(acts);
		return d;
	}

	/* ---------------- panel shell ---------------- */
	function load() {
		api('GET', '/status').then(function (j) {
			if (j.ok) { acct = j; acctError = null; } else acctError = j.error || 'Could not reach the mail service (' + j._status + ')';
			paintAccount();
		});
		loadLeads();
		return api('GET', '/sent').then(function (j) {
			if (j.ok) { sent = j.sent || []; sentError = null; } else sentError = j.error || 'Could not load (' + j._status + ')';
			if (tab === 'sent') render();
			paintTabs();
		});
	}

	function paintTabs() {
		if (!els.cCompose) return;
		els.cCompose.setAttribute('aria-pressed', String(tab === 'compose'));
		els.cLeads.setAttribute('aria-pressed', String(tab === 'leads'));
		els.cSent.setAttribute('aria-pressed', String(tab === 'sent'));
		var fresh = leads ? leads.filter(function (l) { return !l.emailed_at && !l.blocked; }).length : 0;
		els.cLeads.textContent = 'Leads' + (fresh ? ' · ' + fresh : '');
		els.cSent.textContent = 'Sent' + (sent && sent.length ? ' · ' + sent.length : '');
	}

	function setTab(t) {
		if (tab === t) return;
		tab = t;
		render();
	}

	function render() {
		if (!root) return;
		paintTabs();
		if (!els.to) { els.box.textContent = ''; buildCompose(els.box); }
		els.box.hidden = tab !== 'compose';
		els.other.hidden = tab === 'compose';
		if (tab === 'compose') { grow(); return; }
		saveDraft();
		els.other.textContent = '';
		els.leadRows = null;
		if (tab === 'leads') renderLeads(els.other);
		else renderSent(els.other);
	}

	function chip(label, t) {
		return btn('cv-chip', label, function () { setTab(t); });
	}

	function build(host) {
		H = host;
		H.ensureStyle();
		if (!document.getElementById('email-style')) {
			var st = document.createElement('style');
			st.id = 'email-style';
			st.textContent = CSS;
			document.head.appendChild(st);
		}
		root = e('div', 'cv-view');
		root.id = 'email-view';
		root.setAttribute('role', 'region');
		root.setAttribute('aria-label', 'Email');
		root.hidden = true;

		var head = e('div', 'cv-head');
		head.appendChild(H.sidebarToggle());
		head.appendChild(e('span', 'cv-title', 'Email'));
		head.appendChild(e('span', 'cv-spacer'));
		els.cCompose = chip('Compose', 'compose');
		els.cLeads = chip('Leads', 'leads');
		els.cSent = chip('Sent', 'sent');
		head.appendChild(els.cCompose);
		head.appendChild(els.cLeads);
		head.appendChild(els.cSent);

		var scroller = e('div', 'cv-body');
		var wrap = e('div', 'em-body');
		els.box = e('div');
		els.other = e('div');
		els.other.hidden = true;
		wrap.appendChild(els.box);
		wrap.appendChild(els.other);
		scroller.appendChild(wrap);
		root.appendChild(head);
		root.appendChild(scroller);
		render();
		return root;
	}

	function onOpen() {
		render();
		load();
		if (tab === 'compose' && els.to && pendingLead == null) setTimeout(function () { (els.to.value ? els.body : els.to).focus(); }, 0);
	}

	function onClose() { saveDraft(); }

	/* Berlin CRM drawer -> "Email with Jimmy" (via crm-panel.js) */
	window.__emailOpenLead = function (id) {
		pendingLead = Number(id);
		if (location.hash !== '#email') location.hash = 'email'; /* crons-tab.js opens the panel on hashchange */
		if (root && leads) openPending();
		else if (root) loadLeads();
	};

	function register() {
		if (!window.__cvRegisterPanel) return false;
		window.__cvRegisterPanel('email', { build: build, onOpen: onOpen, onClose: onClose });
		return true;
	}
	if (!register()) {
		var tries = 0;
		var t = setInterval(function () { if (register() || ++tries > 100) clearInterval(t); }, 100);
	}
})();

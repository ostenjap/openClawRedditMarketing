/*
 * rnd-panel.js — "R&D" sidebar panel (#rnd) for Open WebUI 0.11.4: a plain notes page for ideas and reflections
 * on what to improve. Modelled on the native Notes page (search, notes grouped by date, title + text editor),
 * but notes only: no AI, no chat, no sharing.
 * Registers itself via window.__cvRegisterPanel('rnd', ...) exposed by crons-tab.js.
 * Data: host service rnd-notes.service (/root/rnd-notes/rnd_notes_api.py), Caddy /rnd-api/* -> 127.0.0.1:8804.
 *       Each note is a Markdown file in /root/rnd/notes/ (front matter: title, created, updated).
 *   GET  /rnd-api/notes                list      POST /rnd-api/notes          create {title, body}
 *   GET  /rnd-api/notes/<id>           one note  POST /rnd-api/notes/<id>     save {title, body, base}
 *   POST /rnd-api/notes/<id>/delete    moves the file to .trash/
 * Edits autosave (debounced); `base` makes a save from a stale tab fail with 409 instead of overwriting.
 * Plain DOM, textContent / value only (never innerHTML with data). Colours come from the shared --cv-* vars.
 */
(function () {
	'use strict';
	if (window.__rndPanelLoaded) return;
	window.__rndPanelLoaded = true;

	var API = '/rnd-api/notes';
	var SAVE_MS = 800;
	var ICON_PLUS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
	var ICON_BACK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>';
	var ICON_TRASH = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/></svg>';

	var CSS =
		'#rnd-view [hidden]{display:none!important}' +
		'.rd-body{max-width:48rem;margin:0 auto;padding:.25rem .25rem 2rem}' +
		'.rd-new{display:inline-flex;align-items:center;gap:.375rem;height:1.875rem;padding:0 .75rem!important;border-radius:9999px;font-size:.8125rem;' +
		'background:var(--cv-name)!important;color:var(--color-white,#fff)!important;transition:opacity .15s}' +
		'html.dark .rd-new{color:var(--color-gray-900,#171717)!important}' +
		'.rd-new:hover{opacity:.85}.rd-new:focus-visible{outline:2px solid var(--cv-faint);outline-offset:2px}' +
		'.rd-g{margin:1.25rem .5rem .375rem;font-size:.75rem;font-weight:500;color:var(--cv-meta)}' +
		'.rd-list{display:grid;gap:.125rem}' +
		'.rd-row{display:flex;align-items:flex-start;gap:.75rem;width:100%;padding:.5rem .625rem;border-radius:.75rem;text-align:left;cursor:pointer;transition:background-color .15s}' +
		'.rd-row:hover{background:var(--cv-hover)}.rd-row:focus-visible{outline:2px solid var(--cv-faint);outline-offset:-2px}' +
		'.rd-main{flex:1;min-width:0}' +
		'.rd-t{color:var(--cv-name);font-size:.875rem;line-height:1.25rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
		'.rd-t.untitled{color:var(--cv-meta)}' +
		'.rd-s{color:var(--cv-meta);font-size:.75rem;line-height:1.125rem;margin-top:.125rem;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:anywhere}' +
		'.rd-when{flex:none;font-size:.6875rem;line-height:1.25rem;color:var(--cv-faint);white-space:nowrap}' +
		'.rd-hit{background:var(--cv-warn-bg);color:inherit;border-radius:.1875rem}' +
		'.rd-empty{display:flex;min-height:calc(100dvh - 12rem);flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:.375rem}' +
		'.rd-empty b{font-weight:400;font-size:.875rem;color:var(--cv-name)}' +
		'.rd-empty span{font-size:.75rem;line-height:1.25rem;color:var(--cv-meta);max-width:24rem}' +
		'.rd-empty .rd-new{margin-top:.75rem}.rd-empty .rd-new span{color:inherit;font-size:inherit;max-width:none}' +
		'.rd-banner{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin:.5rem .25rem;padding:.5rem .75rem;border-radius:.75rem;background:var(--cv-warn-bg);color:var(--cv-warn-fg);font-size:.75rem}' +
		'.rd-banner.err{background:var(--cv-err-bg);color:var(--cv-err-fg)}' +
		'.rd-banner button{text-decoration:underline;font-size:.75rem}' +
		'.rd-ed{max-width:48rem;margin:0 auto;padding:.75rem 1rem 3rem;display:flex;flex-direction:column;min-height:100%}' +
		'.rd-title{width:100%;font:inherit;font-size:1.75rem;line-height:2.25rem;font-weight:500;border:0;outline:0;background:transparent;color:var(--cv-name);padding:0}' +
		'.rd-title::placeholder,.rd-text::placeholder{color:var(--cv-faint)}' +
		'.rd-meta{display:flex;align-items:center;gap:.375rem;margin:.375rem 0 1rem;font-size:.75rem;color:var(--cv-faint)}' +
		'.rd-text{flex:1;width:100%;min-height:50vh;font:inherit;font-size:.9375rem;line-height:1.625rem;border:0;outline:0;background:transparent;color:inherit;resize:none;padding:0;overflow:hidden}' +
		'.rd-state.err{color:var(--cv-err-fg)}' +
		'.cv-iconbtn.rd-del:hover{color:var(--cv-err-fg)}' +
		'@media (max-width:767px){.rd-ed{padding:.5rem .75rem 3rem}.rd-title{font-size:1.375rem;line-height:1.875rem}.rd-new .rd-lbl{display:none}.rd-new{padding:0 .5rem!important}}';

	var H = null; /* host helpers from crons-tab.js */
	var root = null, els = {};
	var notes = null, loadError = null, query = '';
	/* editor state: note = server copy ({id,title,body,updated,...}) or {id:null} for a not-yet-created note */
	var ed = null, saveTimer = null, saving = null, dirty = false, edError = null, conflict = null, openSeq = 0;

	function e(tag, cls, text) {
		var n = document.createElement(tag);
		if (cls) n.className = cls;
		if (text != null) n.textContent = text;
		return n;
	}

	function headers(json) {
		var h = { Accept: 'application/json', 'X-Requested-With': 'rnd' };
		if (json) h['Content-Type'] = 'application/json';
		try { var t = localStorage.token; if (t) h.Authorization = 'Bearer ' + t; } catch (x) { /* storage blocked */ }
		return h;
	}

	function api(method, path, body) {
		return fetch(API + (path || ''), {
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

	/* same buckets as the native Notes / chat list */
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

	/* ---------------- list ---------------- */
	function load() {
		return api('GET').then(function (j) {
			if (!j.ok) throw new Error(j.error || 'Failed (' + j._status + ')');
			notes = j.notes || [];
			loadError = null;
		}).catch(function (x) { loadError = x.message; }).then(function () { if (!ed) renderList(); });
	}

	function highlight(parent, text, q) {
		if (!q) { parent.textContent = text; return; }
		var lo = text.toLowerCase(), i = 0, k;
		while ((k = lo.indexOf(q, i)) !== -1) {
			parent.appendChild(document.createTextNode(text.slice(i, k)));
			parent.appendChild(e('mark', 'rd-hit', text.slice(k, k + q.length)));
			i = k + q.length;
		}
		parent.appendChild(document.createTextNode(text.slice(i)));
	}

	function newButton() {
		var b = e('button', 'rd-new');
		b.type = 'button';
		b.innerHTML = ICON_PLUS; /* static trusted SVG */
		b.appendChild(e('span', 'rd-lbl', 'New note'));
		b.setAttribute('aria-label', 'New note');
		b.addEventListener('click', function () { openEditor(null); });
		return b;
	}

	function renderHead() {
		els.back.hidden = !ed;
		els.search.hidden = !!ed;
		els.newBtn.hidden = !!ed;
		els.del.hidden = !ed;
		els.count.textContent = !ed && notes && notes.length ? String(notes.length) : '';
	}

	function renderList() {
		if (!root) return;
		renderHead();
		var body = els.body;
		body.textContent = '';
		body.className = 'rd-body';
		if (loadError) body.appendChild(e('div', 'rd-banner err', loadError));
		if (!notes) {
			if (!loadError) body.appendChild(e('div', 'rd-empty', 'Loading…'));
			return;
		}
		var q = query.trim().toLowerCase();
		var shown = notes.filter(function (n) {
			return !q || (n.title + ' ' + n.snippet).toLowerCase().indexOf(q) !== -1;
		});
		if (!shown.length) {
			var em = e('div', 'rd-empty');
			if (q) {
				em.appendChild(e('b', null, 'No results found'));
				em.appendChild(e('span', null, 'No note matches “' + query.trim() + '”.'));
			} else {
				em.appendChild(e('b', null, 'No notes yet'));
				em.appendChild(e('span', null, 'Write down what to improve, what you noticed, ideas worth testing. Notes are saved as Markdown files on the server.'));
				em.appendChild(newButton());
			}
			body.appendChild(em);
			return;
		}
		var group = null, list = null;
		shown.forEach(function (n) {
			var g = bucket(n.updated);
			if (g !== group) {
				group = g;
				body.appendChild(e('div', 'rd-g', g));
				list = e('div', 'rd-list');
				body.appendChild(list);
			}
			var r = e('div', 'rd-row');
			r.setAttribute('role', 'button');
			r.tabIndex = 0;
			var m = e('div', 'rd-main');
			var t = e('div', 'rd-t' + (n.title ? '' : ' untitled'));
			highlight(t, n.title || 'Untitled', n.title ? q : '');
			m.appendChild(t);
			if (n.snippet) { var s = e('div', 'rd-s'); highlight(s, n.snippet, q); m.appendChild(s); }
			r.appendChild(m);
			var w = e('div', 'rd-when', ago(n.updated));
			w.title = full(n.updated);
			r.appendChild(w);
			r.addEventListener('click', function () { openEditor(n.id); });
			r.addEventListener('keydown', function (ev) {
				if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openEditor(n.id); }
			});
			list.appendChild(r);
		});
	}

	/* ---------------- editor ---------------- */
	function grow() {
		if (!els.text) return;
		els.text.style.height = 'auto';
		els.text.style.height = els.text.scrollHeight + 'px';
	}

	function paintState() {
		if (!els.state) return;
		var txt = '', err = false;
		if (edError) { txt = edError; err = true; }
		else if (conflict) { txt = 'Not saved'; err = true; }
		else if (saving || dirty) txt = 'Saving…';
		else if (ed && ed.updated) txt = 'Saved · edited ' + ago(ed.updated);
		else txt = 'Not saved yet. Start typing.';
		els.state.textContent = txt;
		els.state.className = 'rd-state' + (err ? ' err' : '');
		if (ed && ed.created) els.state.title = 'Created ' + full(ed.created) + (ed.updated ? '\nEdited ' + full(ed.updated) : '');
	}

	function paintConflict() {
		els.conflict.textContent = '';
		els.conflict.hidden = !conflict;
		if (!conflict) return;
		els.conflict.appendChild(e('span', null, 'This note was changed in another tab or by someone else.'));
		var mine = e('button', null, 'Keep my version');
		mine.type = 'button';
		mine.addEventListener('click', function () { conflict = null; paintConflict(); flush(true); });
		var theirs = e('button', null, 'Load the other version');
		theirs.type = 'button';
		theirs.addEventListener('click', function () {
			var other = conflict;
			conflict = null; dirty = false; edError = null;
			ed = other;
			els.title.value = other.title;
			els.text.value = other.body;
			grow(); paintConflict(); paintState();
		});
		els.conflict.appendChild(mine);
		els.conflict.appendChild(theirs);
	}

	function buildEditor() {
		var body = els.body;
		body.textContent = '';
		body.className = 'rd-ed';
		els.conflict = e('div', 'rd-banner');
		els.conflict.hidden = true;
		body.appendChild(els.conflict);
		els.title = e('input', 'rd-title');
		els.title.type = 'text';
		els.title.placeholder = 'Untitled';
		els.title.maxLength = 200;
		els.title.setAttribute('aria-label', 'Title');
		els.state = e('div', 'rd-meta');
		els.text = e('textarea', 'rd-text');
		els.text.placeholder = 'What could be better? What did you notice? What should change next?';
		els.text.setAttribute('aria-label', 'Note');
		els.text.spellcheck = true;
		[els.title, els.text].forEach(function (inp) {
			inp.addEventListener('input', function () { dirty = true; edError = null; if (inp === els.text) grow(); paintState(); schedule(); });
			inp.addEventListener('keydown', function (ev) {
				if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's') { ev.preventDefault(); flush(false); }
			});
		});
		els.title.addEventListener('keydown', function (ev) {
			if (ev.key === 'Enter' || (ev.key === 'ArrowDown' && els.title.selectionStart === els.title.value.length)) {
				ev.preventDefault();
				els.text.focus();
				els.text.setSelectionRange(0, 0);
			}
		});
		body.appendChild(els.title);
		body.appendChild(els.state);
		body.appendChild(els.text);
	}

	function openEditor(id) {
		ed = { id: null, title: '', body: '', created: '', updated: '' };
		dirty = false; edError = null; conflict = null;
		renderHead();
		buildEditor();
		paintState();
		var seq = ++openSeq;
		if (!id) { els.title.focus(); return; }
		els.title.disabled = els.text.disabled = true;
		els.state.textContent = 'Loading…';
		api('GET', '/' + encodeURIComponent(id)).then(function (j) {
			if (!ed || seq !== openSeq) return; /* closed or switched meanwhile */
			if (!j.ok) { edError = j.error || 'Could not open this note'; paintState(); return; }
			ed = j.note;
			els.title.value = ed.title;
			els.text.value = ed.body;
			els.title.disabled = els.text.disabled = false;
			grow();
			paintState();
			els.text.focus();
		});
	}

	function schedule() {
		clearTimeout(saveTimer);
		saveTimer = setTimeout(function () { flush(false); }, SAVE_MS);
	}

	/* Save now. force = overwrite even if the file changed elsewhere (user chose "Keep my version"). */
	function flush(force) {
		clearTimeout(saveTimer);
		saveTimer = null;
		if (!ed || conflict) return Promise.resolve();
		if (saving) return saving.then(function () { return dirty ? flush(force) : null; });
		if (!dirty && !force) return Promise.resolve();
		var title = els.title.value, body = els.text.value;
		if (!ed.id && !title.trim() && !body.trim()) { dirty = false; paintState(); return Promise.resolve(); }
		var target = ed;
		var req = target.id
			? api('POST', '/' + encodeURIComponent(target.id), force ? { title: title, body: body } : { title: title, body: body, base: target.updated })
			: api('POST', '', { title: title, body: body });
		dirty = false;
		paintState();
		saving = req.then(function (j) {
			saving = null;
			if (ed !== target) { if (j.ok) touchList(j.note); return; }
			if (j.ok) {
				ed = j.note;
				touchList(j.note);
				/* typing during the request: keep it dirty so the next save picks it up */
				if (els.title.value !== title || els.text.value !== body) { dirty = true; schedule(); }
			} else if (j._status === 409 && j.note) {
				conflict = j.note;
				dirty = true;
				paintConflict();
			} else {
				dirty = true;
				edError = (j.error || 'Not saved') + ' Retrying…';
				saveTimer = setTimeout(function () { flush(false); }, 5000);
			}
			paintState();
		});
		return saving;
	}

	function touchList(n) {
		if (!notes || !n) return;
		var item = { id: n.id, title: n.title, snippet: n.body.replace(/[#>*_`~\-\[\]]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 240), created: n.created, updated: n.updated };
		notes = notes.filter(function (x) { return x.id !== n.id; });
		notes.unshift(item);
	}

	function closeEditor() {
		var p = flush(false);
		if (conflict) loadError = 'Your last edit was not saved because the note changed elsewhere.';
		ed = null; conflict = null; edError = null; openSeq++;
		els.title = els.text = els.state = els.conflict = null;
		renderList();
		return p.then(load);
	}

	function del() {
		if (!ed) return;
		if (!ed.id) { dirty = false; closeEditor(); return; }
		var name = ed.title || 'Untitled';
		if (!window.confirm('Delete “' + name + '”?\n\nThe file is moved to the trash folder on the server, so it can still be recovered.')) return;
		var id = ed.id;
		clearTimeout(saveTimer);
		dirty = false; ed = null;
		api('POST', '/' + encodeURIComponent(id) + '/delete').then(function (j) {
			if (!j.ok) loadError = j.error || 'Could not delete';
			if (notes) notes = notes.filter(function (x) { return x.id !== id; });
			els.title = els.text = els.state = els.conflict = null;
			renderList();
			load();
		});
	}

	/* ---------------- panel shell ---------------- */
	function build(host) {
		H = host;
		H.ensureStyle();
		if (!document.getElementById('rnd-style')) {
			var st = document.createElement('style');
			st.id = 'rnd-style';
			st.textContent = CSS;
			document.head.appendChild(st);
		}
		root = e('div', 'cv-view');
		root.id = 'rnd-view';
		root.setAttribute('role', 'region');
		root.setAttribute('aria-label', 'R&D');
		root.hidden = true;

		var head = e('div', 'cv-head');
		head.appendChild(H.sidebarToggle());
		els.back = e('button', 'cv-iconbtn');
		els.back.type = 'button';
		els.back.title = 'All notes';
		els.back.setAttribute('aria-label', 'Back to all notes');
		els.back.innerHTML = ICON_BACK; /* static trusted SVG */
		els.back.addEventListener('click', closeEditor);
		head.appendChild(els.back);
		head.appendChild(e('span', 'cv-title', 'R&D'));
		els.count = e('span', 'cv-count');
		head.appendChild(els.count);
		head.appendChild(e('span', 'cv-spacer'));

		els.search = e('div', 'cv-search');
		els.search.style.maxWidth = '16rem';
		els.search.insertAdjacentHTML('beforeend', H.icons.search); /* static trusted SVG from the host */
		var inp = e('input');
		inp.type = 'text';
		inp.placeholder = 'Search notes';
		inp.setAttribute('aria-label', 'Search notes');
		inp.maxLength = 200;
		inp.addEventListener('input', function () { query = inp.value; renderList(); });
		inp.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { inp.value = ''; query = ''; renderList(); } });
		els.search.appendChild(inp);
		head.appendChild(els.search);
		els.newBtn = newButton();
		head.appendChild(els.newBtn);
		els.del = e('button', 'cv-iconbtn rd-del');
		els.del.type = 'button';
		els.del.title = 'Delete note';
		els.del.setAttribute('aria-label', 'Delete note');
		els.del.innerHTML = ICON_TRASH; /* static trusted SVG */
		els.del.addEventListener('click', del);
		head.appendChild(els.del);

		var scroller = e('div', 'cv-body');
		els.body = e('div', 'rd-body');
		scroller.appendChild(els.body);
		root.appendChild(head);
		root.appendChild(scroller);
		root.addEventListener('keydown', function (ev) {
			if (ev.key === 'Escape' && ed && !conflict) { ev.preventDefault(); closeEditor(); }
		});
		renderHead();
		return root;
	}

	function onOpen() {
		if (!ed) renderList();
		load();
	}

	function onClose() {
		flush(false);
	}

	window.addEventListener('beforeunload', function (ev) {
		if (dirty || saving) { flush(false); ev.preventDefault(); ev.returnValue = ''; }
	});
	document.addEventListener('visibilitychange', function () { if (document.hidden) flush(false); });

	function register() {
		if (!window.__cvRegisterPanel) return false;
		window.__cvRegisterPanel('rnd', { build: build, onOpen: onOpen, onClose: onClose });
		return true;
	}
	if (!register()) {
		var tries = 0;
		var t = setInterval(function () { if (register() || ++tries > 100) clearInterval(t); }, 100);
	}
})();

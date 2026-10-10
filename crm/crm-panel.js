/*
 * crm-panel.js — "Berlin CRM" sidebar panel (#berlin-crm) for Open WebUI 0.11.4.
 * Embeds https://campaign.techno-wanderlust.com/berlin-v2/ in an iframe (the CRM allows framing
 * by this origin only). The CRM is a separate app with its own sign-in; nothing is proxied or stored here.
 * Theme: posts this app's REAL computed colours (light/dark/OLED) to the iframe so it looks native;
 * the CRM validates each value before applying it. Registers via window.__cvRegisterPanel('crm', ...).
 */
(function () {
	'use strict';
	if (window.__crmPanelLoaded) return;
	window.__crmPanelLoaded = true;

	var ORIGIN = 'https://campaign.techno-wanderlust.com';
	var SRC = ORIGIN + '/berlin-v2/';
	/* iframe CSS variable -> this app's panel variable */
	var MAP = {
		txt: '--cv-name', mut: '--cv-meta', faint: '--cv-faint', line: '--cv-line', hover: '--cv-hover', soft: '--cv-mono-bg',
		ok: '--cv-ok', err: '--cv-err', warn: '--cv-warn-fg', errbg: '--cv-err-bg', warnbg: '--cv-warn-bg'
	};
	var CSS =
		'.crm-wrap{flex:1;min-height:0;display:flex}' +
		'.crm-frame{flex:1;width:100%;height:100%;border:0;display:block;background:transparent}' +
		'.crm-link{font-size:.75rem;color:var(--cv-meta);text-decoration:none;padding:.25rem .5rem;border-radius:.5rem;white-space:nowrap}' +
		'.crm-link:hover{background:var(--cv-hover)}';

	var H = null, root = null, frame = null, mo = null, listening = false;

	function theme() {
		var cs = getComputedStyle(root), vars = {}, k;
		for (k in MAP) {
			var v = cs.getPropertyValue(MAP[k]).trim();
			if (v) vars[k] = v;
		}
		/* A table needs more legible secondary text than the sidebar's dim meta colour:
		   use the app's own gray scale one step brighter (same palette, so it still matches). */
		var dark = document.documentElement.classList.contains('dark');
		var rs = getComputedStyle(document.documentElement);
		var mut = rs.getPropertyValue('--color-gray-' + (dark ? '400' : '500')).trim();
		var faint = rs.getPropertyValue('--color-gray-' + (dark ? '500' : '400')).trim();
		if (mut) vars.mut = mut;
		if (faint) vars.faint = faint;
		var bg = root.style.backgroundColor || cs.backgroundColor;
		if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') vars.bg = bg;
		return { type: 'ui-theme', dark: dark, vars: vars };
	}

	function push() {
		if (!frame || !frame.contentWindow || !frame.src) return;
		try { frame.contentWindow.postMessage(theme(), ORIGIN); } catch (e) { /* frame not ready */ }
	}

	function onMessage(e) {
		/* the CRM announces itself once loaded; only answer our own iframe */
		if (e.origin === ORIGIN && frame && e.source === frame.contentWindow && e.data && e.data.type === 'crm-ready') push();
	}

	function build(host) {
		H = host;
		H.ensureStyle();
		if (!document.getElementById('crm-panel-style')) {
			var st = document.createElement('style');
			st.id = 'crm-panel-style';
			st.textContent = CSS;
			document.head.appendChild(st);
		}
		root = H.el('div', 'cv-view');
		root.id = 'crm-view';
		root.setAttribute('role', 'region');
		root.setAttribute('aria-label', 'Berlin CRM');
		root.hidden = true;

		var head = H.el('div', 'cv-head');
		head.appendChild(H.sidebarToggle());
		head.appendChild(H.el('span', 'cv-title', 'Berlin CRM'));
		head.appendChild(H.el('span', 'cv-spacer'));
		var open = H.el('a', 'crm-link', 'Open in new tab ↗');
		open.href = SRC; open.target = '_blank'; open.rel = 'noopener';
		head.appendChild(open);
		var reload = H.el('button', 'cv-iconbtn');
		reload.type = 'button';
		reload.setAttribute('aria-label', 'Reload CRM');
		reload.innerHTML = H.icons.refresh; /* static trusted SVG from the host */
		reload.addEventListener('click', function () { if (frame) frame.src = SRC; });
		head.appendChild(reload);

		var wrap = H.el('div', 'crm-wrap');
		frame = document.createElement('iframe');
		frame.className = 'crm-frame';
		frame.title = 'Berlin CRM V2';
		frame.setAttribute('allow', 'clipboard-write');
		frame.setAttribute('referrerpolicy', 'no-referrer');
		frame.addEventListener('load', push);
		wrap.appendChild(frame);

		root.appendChild(head);
		root.appendChild(wrap);
		return root;
	}

	function onOpen() {
		if (!frame.getAttribute('src')) frame.src = SRC; /* load lazily, first time only */
		else push();
		if (!listening) { window.addEventListener('message', onMessage); listening = true; }
		/* follow light/dark switches while the panel is open */
		mo = new MutationObserver(push);
		mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
	}

	function onClose() {
		if (mo) { mo.disconnect(); mo = null; }
		if (listening) { window.removeEventListener('message', onMessage); listening = false; }
	}

	function register() {
		if (!window.__cvRegisterPanel) return false;
		window.__cvRegisterPanel('crm', { build: build, onOpen: onOpen, onClose: onClose });
		return true;
	}
	if (!register()) {
		var tries = 0;
		var t = setInterval(function () { if (register() || ++tries > 100) clearInterval(t); }, 100);
	}
})();

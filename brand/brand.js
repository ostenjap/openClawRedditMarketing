/*
 * brand.js — Proxious logo, light/dark aware, for ui.techno-wanderlust.com (Open WebUI 0.11.4).
 * Open WebUI shows /static/favicon.png (and /favicon.png for default model avatars) in ~90 places: sidebar,
 * login page, chat avatars. The file itself is the LIGHT logo (near-black ink). In dark mode this swaps every such
 * <img> to /static/favicon-dark.png (near-white ink) and back again, following the html.dark class.
 * Runs on every page incl. login; cheap: only touches <img> elements whose src points at the favicon.
 * Branding change is allowed by the Open WebUI LICENSE §4(i): this deployment has <= 50 users per 30 days.
 */
(function () {
	'use strict';
	if (window.__brandLoaded) return;
	window.__brandLoaded = true;

	var DARK_SRC = '/static/favicon-dark.png';
	var LIGHT_RE = /^(?:https?:\/\/[^/]+)?\/(?:static\/)?favicon\.png(?:[?#].*)?$/;
	var ATTR = 'data-brand-src';

	function isDark() {
		return document.documentElement.classList.contains('dark');
	}

	function fix(img) {
		var src = img.getAttribute('src') || '';
		var orig = img.getAttribute(ATTR);
		if (orig && src !== orig && src !== DARK_SRC) {
			/* the app reused this <img> for a different picture */
			img.removeAttribute(ATTR);
			img.style.removeProperty('filter');
			orig = null;
		}
		if (!orig) {
			if (!LIGHT_RE.test(src)) return;
			img.setAttribute(ATTR, src);
			orig = src;
		}
		var dark = isDark();
		var want = dark ? DARK_SRC : orig;
		if (src !== want) img.setAttribute('src', want);
		/* never let a theme filter (e.g. dark:invert) flip our already-correct variant */
		if (dark) img.style.setProperty('filter', 'none');
		else img.style.removeProperty('filter');
	}

	function sweep() {
		var list = document.querySelectorAll('img[src*="favicon"], img[' + ATTR + ']');
		for (var i = 0; i < list.length; i++) fix(list[i]);
	}

	/* microtask, not requestAnimationFrame: rAF is paused in hidden tabs, so a theme switch there would leave the wrong
	   logo until the tab is shown. One sweep per burst of mutations. */
	var queued = false;
	function schedule() {
		if (queued) return;
		queued = true;
		Promise.resolve().then(function () { queued = false; sweep(); });
	}

	function start() {
		sweep();
		new MutationObserver(schedule).observe(document.documentElement, {
			subtree: true, childList: true, attributes: true, attributeFilter: ['src', 'class']
		});
	}

	if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
	else start();
})();

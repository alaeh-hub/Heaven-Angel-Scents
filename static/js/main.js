// Shared CSRF helper — every fetch()/XHR call in the app that needs to
// send X-CSRFToken (currently just scan.js's /scan/verify call, but any
// future one too) should read it from here instead of re-querying
// meta[name="csrf-token"] itself. base.html is the one place that meta
// tag is rendered ({{ csrf_token() }}), so this is the one place that
// reads it back out.
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

(function initSidebarScrollMemory() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;

    const STORAGE_KEY = 'sidebarScrollTop';

    const saved = sessionStorage.getItem(STORAGE_KEY);
    if (saved !== null) {
        sidebar.scrollTop = parseInt(saved, 10) || 0;
    }

    let saveTimer = null;
    sidebar.addEventListener('scroll', () => {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
            sessionStorage.setItem(STORAGE_KEY, String(sidebar.scrollTop));
        }, 80);
    });

    sidebar.addEventListener('click', (e) => {
        if (e.target.closest && e.target.closest('a')) {
            sessionStorage.setItem(STORAGE_KEY, String(sidebar.scrollTop));
        }
    });
})();

function initMobileSidebar() {
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebarBackdrop');
    const openBtn = document.getElementById('menuToggle');
    const closeBtn = document.getElementById('sidebarClose');
    if (!sidebar || !backdrop || !openBtn) return;

    function openMenu() {
        sidebar.classList.add('open');
        backdrop.classList.add('open');
        openBtn.setAttribute('aria-expanded', 'true');
        document.body.style.overflow = 'hidden';
    }

    function closeMenu() {
        sidebar.classList.remove('open');
        backdrop.classList.remove('open');
        openBtn.setAttribute('aria-expanded', 'false');
        document.body.style.overflow = '';
    }

    openBtn.addEventListener('click', openMenu);
    if (closeBtn) closeBtn.addEventListener('click', closeMenu);
    backdrop.addEventListener('click', closeMenu);

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeMenu();
    });

    sidebar.querySelectorAll('a').forEach((link) => {
        link.addEventListener('click', closeMenu);
    });

    window.addEventListener('resize', () => {
        if (window.innerWidth > 760) closeMenu();
    });
}

function initSidebarNavTooltips() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    // The sidebar rests collapsed to an icon-only rail and expands on
    // hover/keyboard-focus purely via CSS (see style.css's :not(:hover)
    // :not(:focus-within) rules) — no JS involved in that part at all.
    //
    // Collapsed nav-link labels are hidden with font-size: 0, not
    // display: none (see style.css), so a screen reader still gets the
    // text, but there's no visible label to read at a glance. Mirror
    // each one into a native title tooltip as a fallback for anyone who
    // mouses over the rail without lingering long enough to trigger the
    // hover-expand.
    sidebar.querySelectorAll('.nav-link').forEach((link) => {
        const clone = link.cloneNode(true);
        const badge = clone.querySelector('.nav-badge');
        if (badge) badge.remove();
        link.title = clone.textContent.trim();
    });
}

// Wires up the footer's "Pin sidebar open" button — the only way to
// hold the sidebar expanded, since the rest of the collapse/expand
// behavior is pure CSS hover/focus-within (see style.css's .sidebar
// comment). Toggles html.sidebar-pinned, which every collapsed-rail
// rule in style.css is scoped with html:not(.sidebar-pinned), and
// persists the choice to localStorage so it survives across sessions
// like the theme setting. The inline <head> script in base.html
// applies that stored choice (or a touch/coarse-pointer default)
// before first paint — this only needs to handle clicks from here on
// and keep the button's own label/aria-pressed in sync.
function initSidebarPinToggle() {
    const btn = document.getElementById('sidebarPinToggle');
    const label = document.getElementById('sidebarPinToggleLabel');
    if (!btn || !label) return;

    const root = document.documentElement;

    function sync() {
        const pinned = root.classList.contains('sidebar-pinned');
        btn.setAttribute('aria-pressed', pinned ? 'true' : 'false');
        label.textContent = pinned ? 'Unpin sidebar' : 'Pin sidebar open';
    }

    sync();

    btn.addEventListener('click', () => {
        const pinned = !root.classList.contains('sidebar-pinned');
        root.classList.toggle('sidebar-pinned', pinned);
        try {
            localStorage.setItem('sidebarPinned', String(pinned));
        } catch (e) { }
        sync();
    });
}

function initThemeToggle() {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;

    const root = document.documentElement;

    btn.addEventListener('click', () => {
        const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';

        root.classList.add('theme-transitioning');
        root.setAttribute('data-theme', next);
        try {
            localStorage.setItem('theme', next);
        } catch (e) {

        }

        const isTouch = window.matchMedia('(hover: none), (pointer: coarse)').matches;
        window.setTimeout(() => root.classList.remove('theme-transitioning'), isTouch ? 180 : 400);

        if (window.Motion && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            var toggleAnim = window.Motion.animate(
                btn,
                { transform: ['scale(1)', 'scale(1.15)', 'scale(1)'] },
                { duration: 0.4, easing: [0.34, 1.56, 0.64, 1] }
            );

            if (toggleAnim && typeof toggleAnim.then === 'function') {
                toggleAnim.then(null, function () { });
            }
        }
    });
}

function revealContent() {
    if (!window.Motion || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const items = document.querySelectorAll('.content > *');
    if (!items.length) return;

    var revealAnim = window.Motion.animate(
        items,
        { opacity: [0, 1], transform: ['translateY(8px)', 'translateY(0px)'] },
        { duration: 0.4, delay: window.Motion.stagger(0.05), easing: [0.16, 1, 0.3, 1] }
    );

    if (revealAnim && typeof revealAnim.then === 'function') {
        revealAnim.then(null, function () { });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initMobileSidebar();
    initSidebarNavTooltips();
    initSidebarPinToggle();
    initThemeToggle();
    initPhClock();
    revealContent();

    document.querySelectorAll('.fill-bar[data-pct]').forEach((el) => {
        const pct = parseFloat(el.dataset.pct) || 0;
        el.style.width = pct + '%';
    });

    document.querySelectorAll('.flash').forEach(attachFlashDismiss);

    initConfirmDialogs();

    initSmartTables();
    initSmartLists();
    initDispatchQtyWarnings();
    initCustomSelects();
    // Registered BEFORE initSoftNav() — both are delegated on
    // `document` in the bubble phase, and listeners on the same node
    // fire in registration order, so this must run first. initSoftNav()'s
    // own submit handler calls e.preventDefault() on every soft-nav-
    // eligible form (i.e. nearly every form in the app) to take over
    // with its own fetch — that's a legitimate interception, not a
    // cancelled submission, but e.defaultPrevented can't tell the two
    // apart. Running first means this only ever sees defaultPrevented
    // from something that actually meant to cancel the submit (a
    // capture-phase confirm() dialog, a form's own validation
    // listener) by the time it checks, never from initSoftNav()'s own
    // preventDefault() a moment later.
    initSubmitLoadingState();
    initSoftNav();
    const notifBell = initNotificationBell();
    initRealtime(notifBell);
    initHaloWidget();
});

// Fades a flash/toast out and removes it from the DOM 5s after it
// appears — shared by the initial page-load flashes above and by
// initSoftNav()'s appendFreshFlashes() below (a flash that shows up
// after a soft-submitted form, without ever going through a real page
// load, needs the exact same treatment or it never disappears).
function attachFlashDismiss(el) {
    setTimeout(() => {
        el.classList.add('flash-exit');
        el.addEventListener('animationend', () => el.remove(), { once: true });
    }, 5000);
}

// Copies sidebar nav-link state (unread-count badges, and which link
// is "active") from a freshly-fetched document onto the live page's
// sidebar, which a soft nav/refresh never re-fetches or replaces
// itself (see swapContent() below — only .content is swapped, the
// sidebar stays the exact same DOM nodes throughout the session so
// its own scroll position/hover state etc. isn't disturbed by every
// navigation). Links are matched by href since that's the one thing
// guaranteed identical between the live and freshly-rendered sidebar.
function patchSidebarNav(freshDoc) {
    document.querySelectorAll('.nav-link[href]').forEach((link) => {
        const href = link.getAttribute('href');
        const freshLink = freshDoc.querySelector(`.nav-link[href="${href}"]`);
        if (!freshLink) return;

        const liveBadge = link.querySelector('.nav-badge');
        const freshBadge = freshLink.querySelector('.nav-badge');
        if (liveBadge && !freshBadge) {
            liveBadge.remove();
        } else if (freshBadge && !liveBadge) {
            link.appendChild(freshBadge.cloneNode(true));
        } else if (freshBadge && liveBadge) {
            liveBadge.textContent = freshBadge.textContent;
        }

        link.classList.toggle('active', freshLink.classList.contains('active'));
    });
}

// Copies the topbar's eyebrow + heading (base.html's {% block eyebrow %}
// / {% block heading %}) from a freshly-fetched document onto the live
// page. Same reason this needs its own patch function as
// patchSidebarNav() above: the topbar lives outside <main class="content">
// (deliberately — see base.html), so swapContent() replacing .content
// alone never touches it, and it was just sitting there still showing
// whatever page the last *real* load rendered. That's what left the
// page title/eyebrow stuck on (say) "Reports" after soft-navigating to
// Record Sale, Scan Receipt, Partner Inquiries, AI Assistant, etc. —
// .content, the sidebar's active link, and document.title all updated
// correctly, only this was left behind.
function patchTopbar(freshDoc) {
    const liveEyebrow = document.querySelector('.topbar .eyebrow');
    const freshEyebrow = freshDoc.querySelector('.topbar .eyebrow');
    if (liveEyebrow && freshEyebrow) liveEyebrow.textContent = freshEyebrow.textContent;

    const liveHeading = document.querySelector('.topbar h1');
    const freshHeading = freshDoc.querySelector('.topbar h1');
    if (liveHeading && freshHeading) liveHeading.textContent = freshHeading.textContent;
}

// Copies a page's own {% block head %} (base.html wraps it in
// <!--page-head-start-->/<!--page-head-end--> comment markers,
// specifically so this can find it) from a freshly-fetched document
// into the live page's <head>. Every page that defines this block puts
// its own layout-critical <style> there — Record Sale's Type/Payment
// segmented-control styling, Reports'/Record Sale's .rb-mode-opt
// buttons, Scan Receipt's .scan-grid layout, AI Assistant's
// .chat-shell layout, Partner Inquiries' table/icon styling, Branch
// Performance's .bp-table column widths, and more — none of which live
// in the shared static/css/style.css, all of which swapContent()
// replacing .content alone never touches, since <head> isn't part of
// .content. A soft nav to any of those pages left them relying on
// whatever page was last *actually* loaded for their <head> — usually
// nothing matching at all — so their segmented controls, grids, and
// icons rendered as bare, unstyled markup until a real reload finally
// rendered that page's own <style> block. Re-syncing this on every
// swapContent() call fixes that the same way patchTopbar() does for
// the eyebrow/heading.
function patchPageHead(freshDoc) {
    function markers(doc) {
        let start = null;
        let end = null;
        doc.head.childNodes.forEach((node) => {
            if (node.nodeType !== Node.COMMENT_NODE) return;
            const text = node.data.trim();
            if (text === 'page-head-start') start = node;
            if (text === 'page-head-end') end = node;
        });
        return { start, end };
    }

    const live = markers(document);
    const fresh = markers(freshDoc);
    if (!live.start || !live.end || !fresh.start || !fresh.end) return;

    while (live.start.nextSibling && live.start.nextSibling !== live.end) {
        live.start.nextSibling.remove();
    }

    let node = fresh.start.nextSibling;
    while (node && node !== fresh.end) {
        document.head.insertBefore(document.importNode(node, true), live.end);
        node = node.nextSibling;
    }
}

// Re-runs everything that turns freshly-swapped-in .content markup
// into working, interactive UI — the exact same list softRefresh()
// (realtime pushes) and initSoftNav() (link/form navigation) both
// need after replacing .content wholesale with a server-rendered
// fragment that hasn't been through any of this yet.
function refreshDynamicContent() {
    document.querySelectorAll('.fill-bar[data-pct]').forEach((el) => {
        const pct = parseFloat(el.dataset.pct) || 0;
        el.style.width = pct + '%';
    });
    initSmartTables();
    initSmartLists();
    initDispatchQtyWarnings();
    initCustomSelects();
    // initConfirmDialogs() is intentionally NOT re-called here: it's
    // delegated on `document` once at page load (see its own comment),
    // so it already covers any form[data-confirm] that just got
    // swapped in — re-attaching per element isn't necessary.
    revealContent();
}

// Replaces the live .content with freshDoc's .content, if it has one.
// Returns false (and touches nothing) when it doesn't — a page outside
// the signed-in app shell (the login screen after a session expires
// mid-navigation, most notably) never renders a .content at all, and
// swapping nothing in would leave the visible page silently pretending
// a request that actually landed somewhere else still succeeded.
// Callers fall back to a real navigation (window.location) when this
// returns false.
function swapContent(freshDoc) {
    const freshContent = freshDoc.querySelector('.content');
    const liveContent = document.querySelector('.content');
    if (!freshContent || !liveContent) return false;
    liveContent.replaceWith(freshContent);
    if (freshDoc.title) document.title = freshDoc.title;
    patchSidebarNav(freshDoc);
    patchTopbar(freshDoc);
    patchPageHead(freshDoc);
    refreshDynamicContent();
    // Deferred two animation frames rather than run inline here: a page
    // script that measures its own layout at creation time (every
    // Chart.js instance — new Chart(canvas, ...) reads the canvas's
    // container size synchronously, once, when it's built) can catch the
    // browser mid-reflow if it runs in the same tick as the .content
    // replaceWith() above, before the swapped-in grid/cards have settled
    // into their final size. That's what let a freshly-drawn chart come
    // out squashed or stretched on a soft nav — the fixed-height
    // .chart-canvas-wrap the CSS forces it into afterwards doesn't fix a
    // canvas whose *internal* drawing-buffer resolution was already
    // measured wrong. Two rAFs is the standard "wait for a layout+paint
    // to actually happen" trick: the first fires before the frame the
    // browser paints the new .content in, the second only after that
    // frame has been committed, guaranteeing every size read inside
    // runPageScripts() reflects the real, settled layout.
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            runPageScripts(freshDoc);
        });
    });
    return true;
}

// Re-runs the page's own {% block scripts %} (base.html wraps that
// block's output in #page-scripts, deliberately kept outside .content —
// see the comment there) every time swapContent() replaces .content. A
// soft nav only ever *fetches* that markup — DOMParser never executes any
// <script> it parses — and whatever's already sitting in the live page
// from the last real load doesn't retroactively apply to whatever new
// .content just got swapped in. That's what left every page whose
// behavior lives in {% block scripts %} looking inert after a sidebar
// click: Branch Performance/Reports' charts never drew, Record Sale's
// Type/Payment switches and product→unit cascade never got wired up,
// Partner Inquiries' "Edit remarks" button never opened its modal — all
// of it worked again only after a real reload actually ran that script.
function runPageScripts(freshDoc) {
    const freshScripts = freshDoc.getElementById('page-scripts');
    const liveScripts = document.getElementById('page-scripts');
    if (!freshScripts || !liveScripts) return;

    liveScripts.innerHTML = '';

    const oldScripts = Array.prototype.slice.call(freshScripts.querySelectorAll('script'));

    // Runs the page's scripts strictly one at a time, in document order —
    // a parser-inserted <script src> blocks the parser until it's fetched
    // *and* executed before the next <script> runs, even an inline one;
    // that's what a chart page's inline init code is relying on when it
    // assumes `Chart` (from an earlier <script src="chart.umd.min.js">)
    // is already defined by the time it runs on a real page load.
    //
    // A previous version of this function tried to recreate that by
    // setting `.async = false` on each dynamically-created script, but
    // that guarantee only ever applies to *src* scripts relative to each
    // other — a dynamically-inserted *inline* script has nothing to
    // fetch, so per spec it executes the instant it's appended, not
    // "once every earlier script has actually finished" like a parser
    // guarantees. That's what let a page's first-ever soft-nav visit
    // this session (the one time chart.umd.min.js hadn't already been
    // fetched and cached) run the chart-init inline script before
    // Chart.js had loaded: `typeof Chart` was still 'undefined', the
    // script's own `if (typeof Chart === 'undefined') return;` guard
    // bailed out, and no chart ever got created — invisible unless you
    // knew to check, since nothing throws. Chaining each script off the
    // previous *src* script's load/error event (inline scripts have
    // nothing to wait for, so they run and immediately hand off to the
    // next one) restores the ordering a real page load gives for free.
    function runNext(index) {
        if (index >= oldScripts.length) return;
        const oldScript = oldScripts[index];
        const newScript = document.createElement('script');
        Array.prototype.forEach.call(oldScript.attributes, (attr) => {
            newScript.setAttribute(attr.name, attr.value);
        });

        if (oldScript.src) {
            newScript.onload = () => runNext(index + 1);
            newScript.onerror = () => runNext(index + 1);
            liveScripts.appendChild(newScript);
            return;
        }

        // Inline script — the actual page behavior. Re-inserted verbatim,
        // its top-level `const`/`let` bindings would land straight in the
        // page's global lexical environment, which (unlike the <script>
        // element itself) isn't cleared out by the innerHTML reset above
        // — so revisiting the same page a second time in one tab, without
        // a real reload in between, would throw "Identifier '...' has
        // already been declared" on the very re-declaration this is
        // trying to make. Wrapping in its own IIFE resets that scope on
        // every run, the same as a fresh <script> on an actual page load.
        newScript.textContent = '(function () {\n' + oldScript.textContent + '\n})();';
        liveScripts.appendChild(newScript);
        runNext(index + 1);
    }

    runNext(0);
}

// .flashes lives outside .content (see base.html) specifically so a
// .content swap never touches it — flashes need to survive a swap
// unbothered, not get wiped and reappear mid-fade. That also means a
// swap alone won't surface a flash a soft-submitted form's redirect
// target queued (e.g. "Product added.") — this pulls those in
// separately, clones them into the live toast stack, and starts their
// own dismiss timer, same as any flash rendered on a real page load.
function appendFreshFlashes(freshDoc) {
    const freshFlashes = freshDoc.querySelectorAll('.flashes .flash');
    if (!freshFlashes.length) return;
    let liveContainer = document.querySelector('.flashes');
    if (!liveContainer) {
        liveContainer = document.createElement('div');
        liveContainer.className = 'flashes';
        liveContainer.setAttribute('role', 'status');
        liveContainer.setAttribute('aria-live', 'polite');
        document.body.appendChild(liveContainer);
    }
    freshFlashes.forEach((el) => {
        const clone = el.cloneNode(true);
        liveContainer.appendChild(clone);
        attachFlashDismiss(clone);
    });
}

// ---------------------------------------------------------------
// Soft navigation: clicking a sidebar link, or any other same-app
// link/form inside .content, no longer throws away and re-fetches the
// *entire* page (sidebar included) the way a normal navigation does —
// it fetches just the target page's HTML in the background and swaps
// .content, the same trick softRefresh() already uses for realtime
// pushes. The sidebar itself is never torn down and rebuilt, so its
// own scroll position and hover/focus state ride out every click
// untouched — the visible effect the "whole sidebar reloads" complaint
// was describing.
//
// Forms get the same treatment on submit: instead of a full-page
// POST-redirect-GET cycle, this fetches the form's action (fetch()
// follows the redirect automatically, landing on the same page the
// browser would have), swaps .content with the result, and pulls in
// whatever flash message that redirect queued (see appendFreshFlashes)
// — so "Product added." still shows up exactly like before, but the
// admin never loses their scroll position or sees the page go blank
// and repaint from scratch for what's otherwise a one-line change.
//
// Deliberately NOT intercepted (left as plain, real navigations):
//   - anything not same-origin, a hash link, mailto:/tel:, or marked
//     download
//   - a link/form targeting a new tab/window (target="_blank") — every
//     PDF/Excel/CSV download and receipt link in the app already uses
//     this (see reports.html's Download buttons, and the various
//     Receipt/Export links), since a file download isn't HTML this
//     could swap into .content in the first place
//   - GET forms (none in this app today, but a search form built this
//     way tomorrow shouldn't silently break)
//   - anything opted out with data-no-soft-nav / data-no-soft-submit —
//     used on the sign-out form, which deliberately leaves the signed-
//     in app shell entirely
// and as a last resort, any response that isn't HTML containing a real
// .content (checked via swapContent()'s own return value) falls back
// to a real navigation there and then, rather than silently doing
// nothing — this is what makes it safe to leave every other edge case
// (a session that expired mid-click, an endpoint that turns out to
// serve a file after all) to that one fallback instead of having to
// enumerate every such case up front.
function initSoftNav() {
    let navToken = 0;

    function isInternalNavigableLink(link) {
        if (!link || !link.href) return false;
        if (link.target && link.target !== '_self') return false;
        if (link.hasAttribute('download')) return false;
        if (link.dataset.noSoftNav !== undefined) return false;
        if (link.getAttribute('href').charAt(0) === '#') return false;
        let url;
        try {
            url = new URL(link.href, window.location.href);
        } catch (e) {
            return false;
        }
        if (url.origin !== window.location.origin) return false;
        if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
        return true;
    }

    function isSoftSubmittableForm(form, submitter) {
        if (form.dataset.noSoftSubmit !== undefined) return false;
        if ((form.method || 'get').toLowerCase() !== 'post') return false;
        const target = (submitter && submitter.formTarget) || form.target;
        if (target && target !== '_self') return false;
        let url;
        try {
            url = new URL(form.action, window.location.href);
        } catch (e) {
            return false;
        }
        if (url.origin !== window.location.origin) return false;
        return true;
    }

    // Every navigation (link or form) funnels through here. `request`
    // is a function that performs the actual fetch and returns a
    // Promise<Response> — a GET for a link, a POST with the form's
    // data for a form — so the two call sites below only differ in
    // how they build that one request.
    function go(request, url, historyMode) {
        const myToken = ++navToken;
        const scrollY = window.scrollY;

        request()
            .then((r) => {
                const contentType = r.headers.get('Content-Type') || '';
                if (contentType.indexOf('text/html') === -1) {
                    return Promise.reject(new Error('not html'));
                }
                return r.text().then((html) => ({ html, finalUrl: r.url }));
            })
            .then(({ html, finalUrl }) => {
                // A second soft-nav started (another click/submit) while
                // this one was still in flight — let that newer one win
                // and drop this now-stale response instead of clobbering
                // whatever it already rendered.
                if (myToken !== navToken) return;
                // Harmless no-op if a form submit never showed it (a
                // plain link navigation, say) — see showLoadingOverlay().
                hideLoadingOverlay();

                const fresh = new DOMParser().parseFromString(html, 'text/html');
                if (!swapContent(fresh)) {
                    // finalUrl reflects wherever the request actually
                    // ended up (e.g. redirected to /login by an expired
                    // session) — landing there directly instead of
                    // re-requesting the original url avoids bouncing
                    // through the same redirect a second time.
                    window.location.href = finalUrl;
                    return;
                }
                appendFreshFlashes(fresh);

                if (historyMode === 'push') {
                    history.pushState({ softNav: true }, '', finalUrl);
                } else if (historyMode === 'replace') {
                    history.replaceState({ softNav: true }, '', finalUrl);
                }
                if (historyMode !== 'pop') {
                    window.scrollTo({ top: 0, behavior: 'auto' });
                } else {
                    window.scrollTo({ top: scrollY, behavior: 'auto' });
                }
            })
            .catch(() => {
                if (myToken !== navToken) return;
                hideLoadingOverlay();
                window.location.href = url;
            });
    }

    document.addEventListener('click', (e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const link = e.target.closest('a[href]');
        if (!link) return;
        if (!link.closest('.sidebar') && !link.closest('.content')) return;
        if (!isInternalNavigableLink(link)) return;

        const url = new URL(link.href, window.location.href);
        if (url.pathname === window.location.pathname && url.search === window.location.search) {
            // Same page (only the #hash, if any, differs) — let the
            // browser's own default handling (e.g. jumping to an
            // in-page anchor) happen rather than "navigating" to a
            // no-op and losing that behavior.
            return;
        }

        e.preventDefault();
        go(() => fetch(link.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } }), link.href, 'push');
    });

    document.addEventListener('submit', (e) => {
        if (e.defaultPrevented) return;
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (!form.closest('.content')) return;
        const submitter = e.submitter;
        if (!isSoftSubmittableForm(form, submitter)) return;

        e.preventDefault();
        const formData = new FormData(form, submitter);
        go(
            () => fetch(form.action, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            }),
            form.action,
            'push',
        );
    });

    window.addEventListener('popstate', () => {
        go(() => fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } }), window.location.href, 'pop');
    });
}

// ---------------------------------------------------------------
// Custom select: progressively enhances every <select> in the app
// with a styled trigger + dropdown panel instead of relying on the
// browser's native picker — on a phone, that native picker is a
// full-screen OS-drawn control (iOS's spinning wheel, Android's
// oversized list) that CSS can never reach or resize, which is what
// read as "the dropdown is too big" on mobile. This builds a
// compact, anchored panel instead, styled to match every other
// input in the app, on both mobile and desktop.
//
// The real <select> stays in the DOM exactly where it was — same
// name/id/required/disabled attributes, same position in its form —
// just visually replaced: it's positioned invisibly *on top of* the
// trigger button (see .cs-select select in style.css) so a screen
// reader / the browser's own required-field validation still finds
// a real, correctly-positioned control there, with pointer-events
// disabled so clicks pass through to the button underneath instead
// of popping the native picker. It's still the actual source of
// truth for the field's submitted value; this never removes or
// clones the select — only changes how it's shown and interacted
// with. An individual select can opt out with a `data-cs-skip`
// attribute (see admin/partner_inquiries.html's tiny inline status
// dropdown, which is deliberately a bare native picker behind a
// custom-styled dot, not a form field this component's box-shaped
// trigger would fit well next to).
//
// Existing code elsewhere in the app sets these selects' .value
// directly (edit-item modals, the product/unit autocombos on Record
// Sale/Production/Request Stock) or rebuilds their whole <option>
// list with innerHTML/appendChild (those same autocombos, as the
// person picks a product). Rather than auditing and editing every
// one of those call sites, this hooks into two things that catch
// all of them automatically, from anywhere, including code that
// runs after this file:
//   - a property override on .value that re-syncs the trigger's
//     label any time anything sets it
//   - a MutationObserver watching each select's <option> list and
//     its disabled attribute, so a select whose options get rebuilt
//     after this ran still ends up with a matching custom panel
//
// Called again after softRefresh() swaps in fresh .content HTML
// (plain, unenhanced <select> markup from the server) — see
// initRealtime() below — the same way initSmartTables()/
// initSmartLists() are. dataset.csEnhanced makes re-running this on
// the *same* elements a no-op, so calling it more than once is safe.
function initCustomSelects(root) {
    (root || document).querySelectorAll('select').forEach(enhanceSelect);
}

function enhanceSelect(select) {
    if (select.dataset.csEnhanced || select.multiple || select.dataset.csSkip !== undefined) return;
    select.dataset.csEnhanced = '1';

    const wrap = document.createElement('div');
    wrap.className = 'cs-select';
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'cs-trigger';
    // Not a separate tab stop: the real (invisible) <select> above it
    // stays the thing that receives focus and keyboard input, exactly
    // as it did before this ran, so Tab order through the form is
    // unchanged and native required-field validation still targets a
    // real, focusable control.
    trigger.tabIndex = -1;
    trigger.setAttribute('aria-hidden', 'true');
    trigger.innerHTML =
        '<span class="cs-trigger-label"></span>' +
        '<svg class="cs-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<path d="m6 9 6 6 6-6"/></svg>';
    wrap.appendChild(trigger);

    const panel = document.createElement('div');
    panel.className = 'cs-panel';
    panel.setAttribute('role', 'listbox');
    wrap.appendChild(panel);

    const labelEl = trigger.querySelector('.cs-trigger-label');
    let activeIndex = -1;

    function optionEls() {
        return Array.prototype.slice.call(panel.querySelectorAll('.cs-option'));
    }

    function isOpen() {
        return wrap.classList.contains('cs-open');
    }

    function position() {
        // Flip the panel above the trigger when there isn't room below
        // — the same reasoning as every other floating panel in the
        // app (e.g. the product-search combos), just generalized here
        // since a <select> can live anywhere: low in a long form, near
        // the bottom of a modal, etc.
        wrap.classList.remove('cs-open-up');
        const rect = trigger.getBoundingClientRect();
        const spaceBelow = window.innerHeight - rect.bottom;
        const panelHeight = panel.offsetHeight || 280;
        if (spaceBelow < panelHeight && rect.top > spaceBelow) {
            wrap.classList.add('cs-open-up');
        }
    }

    function open() {
        if (select.disabled || isOpen()) return;
        rebuildPanel();
        wrap.classList.add('cs-open');
        trigger.setAttribute('aria-expanded', 'true');
        activeIndex = select.selectedIndex;
        highlightActive();
        position();
        const activeEl = optionEls()[activeIndex];
        if (activeEl && activeEl.scrollIntoView) activeEl.scrollIntoView({ block: 'nearest' });
    }

    function close() {
        if (!isOpen()) return;
        wrap.classList.remove('cs-open', 'cs-open-up');
        trigger.setAttribute('aria-expanded', 'false');
        activeIndex = -1;
    }

    function highlightActive() {
        optionEls().forEach(function (el, i) {
            el.classList.toggle('cs-active', i === activeIndex);
        });
    }

    function moveActive(delta) {
        const opts = optionEls();
        if (!opts.length) return;
        let next = activeIndex;
        for (let step = 0; step < opts.length; step++) {
            next += delta;
            if (next < 0) next = opts.length - 1;
            if (next > opts.length - 1) next = 0;
            if (!select.options[next].disabled) break;
        }
        activeIndex = next;
        highlightActive();
        const el = opts[activeIndex];
        if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    }

    function selectIndex(i) {
        if (i < 0 || i >= select.options.length || select.options[i].disabled) return;
        select.selectedIndex = i;
        sync();
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function rebuildPanel() {
        const opts = Array.prototype.slice.call(select.options);
        if (!opts.length) {
            panel.innerHTML = '<div class="cs-option-empty">No options</div>';
            return;
        }
        // Built as real DOM nodes with textContent (not an HTML string)
        // so option text is never re-parsed as markup -- an option whose
        // text came from user-entered data (a supplier/customer/material
        // name, etc.) cannot inject HTML this way. Do not switch this
        // back to panel.innerHTML = opts.map(...).join('') without
        // escaping first.
        panel.innerHTML = '';
        opts.forEach(function (opt, i) {
            const el = document.createElement('div');
            el.className = 'cs-option' + (opt.disabled ? ' cs-option-disabled' : '');
            el.setAttribute('role', 'option');
            el.dataset.index = i;
            el.textContent = opt.textContent.trim() || ' ';
            panel.appendChild(el);
        });
        optionEls().forEach(function (el) {
            el.addEventListener('click', function () {
                const i = parseInt(el.dataset.index, 10);
                selectIndex(i);
                close();
            });
            // Same defensive preventDefault as the product-search
            // combo's own menu (production.html's .ac-combo-menu):
            // without it, the mousedown's default focus handling can
            // close the panel a beat before the click handler above
            // gets to run, so the click never registers.
            el.addEventListener('mousedown', function (e) { e.preventDefault(); });
        });
        syncSelectedMarker();
    }

    function syncSelectedMarker() {
        optionEls().forEach(function (el, i) {
            el.classList.toggle('cs-selected', i === select.selectedIndex);
        });
    }

    function syncTrigger() {
        const opt = select.options[select.selectedIndex];
        const text = opt ? opt.textContent.trim() : '';
        labelEl.textContent = text || ' ';
        labelEl.classList.toggle('cs-placeholder', !!opt && select.selectedIndex === 0 && opt.value === '');
        wrap.classList.toggle('cs-disabled', select.disabled);
    }

    function sync() {
        syncTrigger();
        syncSelectedMarker();
    }

    // ---- .value interceptor: catches every future `select.value = x`
    // from anywhere (this app's existing edit-modal/autocombo code
    // included), so the trigger label never drifts out of sync with
    // the real control it's standing in for. ----
    const valueDescriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    if (valueDescriptor && valueDescriptor.configurable) {
        Object.defineProperty(select, 'value', {
            configurable: true,
            enumerable: true,
            get: function () { return valueDescriptor.get.call(select); },
            set: function (v) {
                valueDescriptor.set.call(select, v);
                sync();
            },
        });
    }

    // ---- catches option-list rebuilds (innerHTML/appendChild) and
    // disabled toggles from any existing or future script. ----
    new MutationObserver(function () {
        rebuildPanel();
        syncTrigger();
    }).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });

    // ---- native events: a real 'change' still fires when the select
    // itself handles input directly (native type-ahead while hidden-
    // but-focused) — keep the trigger in sync and close the panel
    // either way. ----
    select.addEventListener('change', function () { sync(); close(); });
    select.addEventListener('focus', function () { wrap.classList.add('cs-focus'); });
    select.addEventListener('blur', function () { wrap.classList.remove('cs-focus'); close(); });

    trigger.addEventListener('mousedown', function (e) { e.preventDefault(); });
    trigger.addEventListener('click', function () {
        if (select.disabled) return;
        select.focus({ preventScroll: true });
        if (isOpen()) close(); else open();
    });

    wrap.addEventListener('keydown', function (e) {
        if (select.disabled) return;
        if (e.key === 'Escape') { close(); return; }
        if (e.key === 'Tab') { close(); return; }
        if (!isOpen()) {
            if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                open();
            }
            return;
        }
        if (e.key === 'ArrowDown') { e.preventDefault(); moveActive(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1); }
        else if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            if (activeIndex >= 0) { selectIndex(activeIndex); close(); }
        }
    });

    document.addEventListener('click', function (e) {
        if (isOpen() && !wrap.contains(e.target)) close();
    });

    window.addEventListener('resize', function () { if (isOpen()) position(); });

    rebuildPanel();
    sync();
}

// Unified confirm-dialog pattern for every "are you sure?" form in the
// app: give the <form> a data-confirm="Message" attribute and this one
// delegated (capture-phase) listener handles the rest — no per-page
// onsubmit="return confirm(...)" or bespoke button-click JS needed. See
// requests.html, request_detail.html, users.html, and package_detail.html
// for the template side of this (package_detail.html's message still
// varies per-row, built in Jinja, but lands in the same attribute).
//
// A toggle form (.../toggle) that doesn't set data-confirm explicitly
// still gets a sensible default built from its submit button's label
// ("Deactivate?" / "Reactivate?"), preserving the old behavior for any
// toggle form that hasn't been given an explicit message.
//
// Delegated on `document` (rather than looping over forms and attaching
// one listener each) so this keeps working after main.js's own
// softRefresh() swaps in fresh .content HTML from the server — no
// re-init call needed the way the old per-form version required.
function initConfirmDialogs() {
    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;

        let message = form.dataset.confirm;
        if (!message && form.matches('form[action*="/toggle"]')) {
            const btn = form.querySelector('button[type="submit"]');
            const label = btn ? btn.textContent.trim() : 'do this';
            message = `${label}?`;
        }

        if (message && !confirm(message)) {
            e.preventDefault();
        }
    }, true);
}

// A full-screen, centered "Loading…" dialog (see .loading-overlay in
// style.css) shown for the duration of a form submission. Built once,
// lazily, and appended straight to <body> — not .content — so it
// works identically on every page that loads this file (including
// standalone ones like login.html that don't extend base.html) and,
// more importantly, survives a soft-nav content swap: swapContent()
// (see initSoftNav() above) only ever replaces .content, so an
// overlay living outside it isn't torn down and rebuilt by that swap
// the way anything inside .content would be.
let loadingOverlayEl = null;

function getLoadingOverlay() {
    if (loadingOverlayEl) return loadingOverlayEl;
    const el = document.createElement('div');
    el.className = 'loading-overlay';
    el.hidden = true;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.innerHTML =
        '<div class="loading-dialog">' +
        '<div class="loading-dialog-spinner"></div>' +
        '<div class="loading-dialog-text">Loading…</div>' +
        '</div>';
    document.body.appendChild(el);
    loadingOverlayEl = el;
    return el;
}

// Local/fast requests can resolve in well under a frame, which made
// the dialog flash on and immediately back off — too quick to
// register as having happened at all. Enforcing a small minimum
// visible time fixes that without slowing down anything: a request
// that's genuinely slower than this just isn't affected (hide already
// runs past the minimum by the time it's called), only a fast one
// gets held open a beat longer than strictly necessary. Also long
// enough to comfortably clear the CSS entrance animation's own 0.25s
// duration (see .loading-dialog-in in style.css) before a hide() can
// ever be applied.
const LOADING_OVERLAY_MIN_VISIBLE_MS = 400;
// Must match .loading-overlay-exit's own animation-duration in
// style.css — this is how long hide() waits, after starting that
// class's exit animation, before actually setting hidden = true.
const LOADING_OVERLAY_EXIT_MS = 180;
let loadingOverlayShownAt = 0;
// Bumped on every show, checked before a delayed hide actually applies
// — the overlay visually blocks clicks to everything under it, so two
// overlapping submissions can't happen from a mouse click, but a
// keyboard-triggered back/forward navigation still could while an
// earlier hide is mid-delay. Without this, that stale timeout would
// close the dialog a newer request just reopened.
let loadingOverlayToken = 0;

// Plain CSS animations (see .loading-overlay/.loading-overlay-exit in
// style.css) rather than Motion (window.Motion — still used above for
// the sidebar theme-toggle bounce and revealContent(), where nothing
// ever cuts the animation short) — this dialog's own lifecycle can
// end at any moment via a real page navigation (a plain, non-soft-nav
// form submit unloads the page almost immediately), and Motion's
// animate() rejects its controls with an AbortError when that
// happens, unreliably enough that even always attaching a rejection
// handler didn't keep it from surfacing as an unhandled rejection.
// Toggling a class and letting CSS run the animation sidesteps that
// category of failure entirely — there's no promise to reject.
function showLoadingOverlay() {
    loadingOverlayShownAt = Date.now();
    loadingOverlayToken += 1;
    const el = getLoadingOverlay();
    el.classList.remove('loading-overlay-exit');
    el.hidden = false;
}

function hideLoadingOverlay() {
    if (!loadingOverlayEl || loadingOverlayEl.hidden) return;
    const myToken = loadingOverlayToken;

    const doHide = () => {
        // Superseded by a newer show() while this was mid-delay —
        // leave it alone, that newer call owns the overlay's state now.
        if (myToken !== loadingOverlayToken) return;
        loadingOverlayEl.classList.add('loading-overlay-exit');
        setTimeout(() => {
            if (myToken === loadingOverlayToken) {
                loadingOverlayEl.hidden = true;
                loadingOverlayEl.classList.remove('loading-overlay-exit');
            }
        }, LOADING_OVERLAY_EXIT_MS);
    };

    // LOADING_OVERLAY_MIN_VISIBLE_MS > LOADING_OVERLAY_ENTER_MS by
    // construction, so doHide() never fires before the CSS entrance
    // animation (see style.css) has already finished on its own.
    const remaining = LOADING_OVERLAY_MIN_VISIBLE_MS - (Date.now() - loadingOverlayShownAt);
    if (remaining > 0) {
        setTimeout(doHide, remaining);
    } else {
        doHide();
    }
}

// ---------------------------------------------------------------
// Modal overlays (Add material, Edit product, Log usage, Remarks,
// etc.) — every one across the admin app shares the same shape: a
// position:fixed backdrop toggled via inline style.display, wrapping
// one .card panel, dismissed by a Cancel button, clicking the
// backdrop, or Escape. These two functions are the one place that
// actually shows/hides one, so every page gets the same animated
// open/close instead of each hand-rolling its own — see
// wireOverlay() below for the common "one open button, one cancel
// button" case, and openOverlay()/closeOverlay() directly for pages
// whose open trigger needs to run its own logic first (populating an
// edit form from a clicked row, say).
//
// anime.js (window.anime — see static/js/anime.js) rather than
// Motion here, same as the loading dialog's original version — but
// unlike that dialog, an overlay is only ever opened/closed by a
// stable, in-page interaction (a button click), never by something
// that might unload the page mid-animation the way a form submission
// could, so animate()'s own promise isn't a reliability concern here.
// It's still never awaited below regardless — the close delay is a
// plain setTimeout matching the exit duration, exactly like the
// loading dialog, so there's nothing to go wrong even if that changes
// later.
// Attached to window (not just a local const) so a page needing to
// sequence its own follow-up work after a close animation finishes —
// see users.html's tempPasswordOverlay, which removes itself from the
// DOM entirely once closed, not just hides — can time it against the
// same duration closeOverlay() itself uses, rather than a second
// independently-maintained number that could drift out of sync.
window.OVERLAY_EXIT_MS = 140;
const OVERLAY_EXIT_MS = window.OVERLAY_EXIT_MS;

function openOverlay(overlay) {
    if (typeof overlay === 'string') overlay = document.getElementById(overlay);
    if (!overlay) return;
    overlay.style.display = 'flex';
    if (!window.anime || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const panel = overlay.querySelector('.card, .pi-modal');
    // anime.js's animate(targets, params) takes ONE params object with
    // the property keyframes and settings (duration, ease, …) merged
    // together — unlike Motion's animate(target, keyframes, options)
    // three-argument shape used elsewhere in this file. A duration/
    // ease passed as a separate third argument here is silently
    // ignored (falls back to anime's own 1000ms default) rather than
    // erroring, which is what actually happened here originally.
    window.anime.animate(overlay, { opacity: [0, 1], duration: 160, ease: 'outQuad' });
    if (panel) {
        window.anime.animate(panel, { opacity: [0, 1], scale: [0.94, 1], duration: 220, ease: 'outBack' });
    }
}

function closeOverlay(overlay) {
    if (typeof overlay === 'string') overlay = document.getElementById(overlay);
    if (!overlay || overlay.style.display === 'none') return;
    if (window.anime && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        const panel = overlay.querySelector('.card, .pi-modal');
        window.anime.animate(overlay, { opacity: [1, 0], duration: OVERLAY_EXIT_MS, ease: 'inQuad' });
        if (panel) {
            window.anime.animate(panel, { opacity: [1, 0], scale: [1, 0.94], duration: OVERLAY_EXIT_MS, ease: 'inQuad' });
        }
        setTimeout(() => { overlay.style.display = 'none'; }, OVERLAY_EXIT_MS);
    } else {
        overlay.style.display = 'none';
    }
}

// The common case: one button opens the overlay as-is (no per-click
// setup needed) and one Cancel button, the backdrop, or Escape closes
// it. Previously duplicated verbatim across pages (each defining its
// own copy, and each registering its own document-level Escape
// listener) — now the one shared version, called the same way from
// any page (materials.html alone now wires four separate overlays:
// add/edit material, add supplier, plus Suppliers' own edit/history
// modals since the two pages merged into one).
function wireOverlay(overlayId, openBtnId, cancelBtnId) {
    const overlay = document.getElementById(overlayId);
    const openBtn = openBtnId ? document.getElementById(openBtnId) : null;
    const cancelBtn = cancelBtnId ? document.getElementById(cancelBtnId) : null;
    if (!overlay || !openBtn) return;

    openBtn.addEventListener('click', () => openOverlay(overlay));
    if (cancelBtn) cancelBtn.addEventListener('click', () => closeOverlay(overlay));
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeOverlay(overlay);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && overlay.style.display !== 'none') closeOverlay(overlay);
    });
}

// Shows the loading overlay the instant a form is actually submitted,
// app-wide — no per-form opt-in needed. Delegated on `document` in
// the bubble phase, so a capture-phase listener that cancels the
// submission first (initConfirmDialogs()'s confirm() dialog) or a
// form's own bubble-phase listener closer to it in the tree (login.html's
// field validation, initDispatchQtyWarnings()'s over/under-limit
// checks) has already had its chance to call preventDefault() by the
// time this runs — checking e.defaultPrevented here means a cancelled
// submit never shows a dialog for a request that was never actually
// sent.
//
// MUST be registered before initSoftNav() (see the DOMContentLoaded
// call site) — initSoftNav()'s own submit listener also calls
// e.preventDefault() on nearly every form in the app, to take over
// with its own fetch, which e.defaultPrevented can't tell apart from
// an actual cancellation. Running first means that hasn't happened
// yet by the time this checks it.
//
// Hiding it again is initSoftNav()'s job (see go() above): a plain,
// non-soft-nav submission just lets the browser's own navigation tear
// the whole page — overlay included — down on its own, but a
// successful soft-nav swap only replaces .content, so go() explicitly
// hides this once its response comes back.
function initSubmitLoadingState() {
    document.addEventListener('submit', (e) => {
        if (e.defaultPrevented) return;
        if (!(e.target instanceof HTMLFormElement)) return;
        showLoadingOverlay();
    });
}

function initDispatchQtyWarnings() {
    document.querySelectorAll('.dispatch-qty-input').forEach((input) => {
        const requested = parseInt(input.dataset.requestedQty, 10);
        const form = input.closest('form');
        if (Number.isNaN(requested)) return;

        function sync() {
            const val = parseInt(input.value, 10);
            const overLimit = !Number.isNaN(val) && val > requested;
            const differs = !Number.isNaN(val) && val !== requested && !overLimit;
            input.classList.toggle('dispatch-qty-invalid', overLimit);
            input.classList.toggle('dispatch-qty-diff', differs);
            input.setCustomValidity(overLimit ? `Can't dispatch more than the ${requested} requested.` : '');
        }

        input.addEventListener('input', sync);
        sync();

        if (form) {
            form.addEventListener('submit', (e) => {
                const val = parseInt(input.value, 10);

                if (!Number.isNaN(val) && val > requested) {
                    e.preventDefault();
                    input.reportValidity();
                    return;
                }

                if (Number.isNaN(val) || val === requested) return;

                const ok = confirm(
                    `This branch requested ${requested}. You're about to dispatch ${val} instead — less than what was asked for. Continue?`
                );
                if (!ok) e.preventDefault();
            });
        }
    });
}

function initSmartTables() {
    document.querySelectorAll('[data-smart-table]').forEach((container) => {
        const input = container.querySelector('.smart-search-input');

        const filterSelect = container.querySelector('.smart-filter-select');
        const filterKey = filterSelect ? (filterSelect.dataset.filterKey || 'filter') : null;
        const table = container.querySelector('.smart-table');
        if (!table) return;

        const tbody = table.querySelector('tbody');
        const rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        const noMatch = container.querySelector('.smart-no-match');
        const pagination = container.querySelector('.smart-pagination');
        const pageInfo = container.querySelector('.smart-page-info');
        const prevBtn = container.querySelector('[data-page-prev]');
        const nextBtn = container.querySelector('[data-page-next]');
        const countTarget = container.dataset.countTarget ? document.getElementById(container.dataset.countTarget) : null;
        const countLabel = container.dataset.countLabel || 'row';
        const pageSize = parseInt(container.dataset.pageSize, 10) || 10;

        const rowText = new Map();
        rows.forEach((r) => {
            const visible = r.textContent.replace(/\s+/g, ' ').trim().toLowerCase();
            const extra = (r.dataset.search || '').toLowerCase();
            rowText.set(r, extra && extra !== visible ? visible + ' ' + extra : visible);
        });

        // Opt-in (table-collapsible, see style.css's @media (max-width:
        // 680px) rules) — each row's mobile card starts collapsed to
        // just its .mobile-inline-anchor/.mobile-inline-action, with a
        // "Details" bar at the bottom to reveal its .mobile-detail
        // cells (Qty/Price/Total/Payment on Record Sale's recent-sales
        // list) instead of dumping all of them at once.
        if (table.classList.contains('table-collapsible')) {
            rows.forEach((row) => {
                const toggleBtn = row.querySelector('.row-toggle-btn');
                if (!toggleBtn) return;
                toggleBtn.addEventListener('click', () => {
                    const expanded = row.classList.toggle('row-expanded');
                    toggleBtn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                });
            });
        }

        let filtered = rows.slice();
        let page = 1;

        function render() {
            const q = input ? input.value.trim().toLowerCase() : '';
            const fval = filterSelect ? filterSelect.value : '';
            filtered = rows.filter((r) => {
                const matchesSearch = !q || rowText.get(r).indexOf(q) !== -1;
                const matchesFilter = !fval || (r.dataset[filterKey] || '') === fval;
                return matchesSearch && matchesFilter;
            });

            const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
            if (page > totalPages) page = totalPages;

            rows.forEach((r) => { r.style.display = 'none'; });
            const start = (page - 1) * pageSize;
            filtered.slice(start, start + pageSize).forEach((r) => { r.style.display = ''; });

            table.style.display = filtered.length ? '' : 'none';
            if (noMatch) noMatch.style.display = filtered.length ? 'none' : '';

            if (pagination) {
                pagination.style.display = filtered.length > pageSize ? 'flex' : 'none';
                if (pageInfo) pageInfo.textContent = 'Page ' + page + ' of ' + totalPages;
                if (prevBtn) prevBtn.disabled = page <= 1;
                if (nextBtn) nextBtn.disabled = page >= totalPages;
            }

            if (countTarget) {
                countTarget.textContent = filtered.length + ' ' + countLabel + (filtered.length !== 1 ? 's' : '');
            }
        }

        if (input) {
            input.addEventListener('input', () => { page = 1; render(); });
        }
        if (filterSelect) {
            filterSelect.addEventListener('change', () => { page = 1; render(); });
        }
        if (prevBtn) {
            prevBtn.addEventListener('click', () => { if (page > 1) { page--; render(); } });
        }
        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
                if (page < totalPages) { page++; render(); }
            });
        }

        render();
    });
}

// Pagination-only counterpart to initSmartTables() for pages that show a
// paginated list of cards/blocks rather than a <table> — e.g. a partner's
// inquiry history, or the shipment blocks on Receive Stock. There's no
// search here on purpose (see the two call sites): each item's content
// isn't a flat row of short cell text, so a text search box would be
// awkward to use, and pagination alone already caps how much renders at
// once. Container needs data-smart-list, data-page-size, and
// data-list-item-selector (the CSS selector for one "item" inside it);
// pagination controls follow the same .smart-pagination markup used by
// initSmartTables() so the two share styling.
function initSmartLists() {
    document.querySelectorAll('[data-smart-list]').forEach((container) => {
        const itemSelector = container.dataset.listItemSelector || '.smart-list-item';
        const items = Array.prototype.slice.call(container.querySelectorAll(itemSelector));
        const pagination = container.querySelector('.smart-pagination');
        if (!items.length) {
            if (pagination) pagination.style.display = 'none';
            return;
        }

        const pageSize = parseInt(container.dataset.pageSize, 10) || 10;
        const pageInfo = container.querySelector('.smart-page-info');
        const prevBtn = container.querySelector('[data-page-prev]');
        const nextBtn = container.querySelector('[data-page-next]');
        const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
        let page = 1;

        function render() {
            const start = (page - 1) * pageSize;
            items.forEach((el, i) => {
                el.style.display = (i >= start && i < start + pageSize) ? '' : 'none';
            });
            if (pagination) {
                pagination.style.display = items.length > pageSize ? 'flex' : 'none';
                if (pageInfo) pageInfo.textContent = 'Page ' + page + ' of ' + totalPages;
                if (prevBtn) prevBtn.disabled = page <= 1;
                if (nextBtn) nextBtn.disabled = page >= totalPages;
            }
        }

        if (prevBtn) prevBtn.addEventListener('click', () => { if (page > 1) { page--; render(); } });
        if (nextBtn) {
            nextBtn.addEventListener('click', () => { if (page < totalPages) { page++; render(); } });
        }

        render();
    });
}

function initNotificationBell() {
    const wrap = document.getElementById('notifWrap');
    const toggle = document.getElementById('notifToggle');
    const panel = document.getElementById('notifPanel');
    const list = document.getElementById('notifList');
    const badge = document.getElementById('notifBadge');
    const clearBtn = document.getElementById('notifClear');
    if (!wrap || !toggle || !panel || !list || !badge) return null;

    const STORAGE_KEY = 'notifications:' + (wrap.dataset.user || 'anon');
    const MAX_ITEMS = 30;

    function load() {
        try {
            return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
        } catch (e) {
            return [];
        }
    }

    function save(items) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
        } catch (e) {

        }
    }

    function timeAgo(ts) {
        const diffMins = Math.floor(Math.max(0, Date.now() - ts) / 60000);
        if (diffMins < 1) return 'just now';
        if (diffMins < 60) return diffMins + 'm ago';
        const diffHrs = Math.floor(diffMins / 60);
        if (diffHrs < 24) return diffHrs + 'h ago';
        return Math.floor(diffHrs / 24) + 'd ago';
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function render() {
        const items = load();
        const unread = items.filter((n) => !n.read).length;

        if (unread > 0) {
            badge.textContent = unread > 9 ? '9+' : String(unread);
            badge.style.display = 'flex';
        } else {
            badge.style.display = 'none';
        }

        if (!items.length) {
            list.innerHTML = '<div class="notif-empty">No notifications yet.</div>';
            return;
        }

        list.innerHTML = items.map((n) => (
            '<div class="notif-item ' + (n.read ? '' : 'unread') + '">' +
            '<span class="notif-dot ' + (n.level || 'info') + '"></span>' +
            '<span class="notif-body">' + escapeHtml(n.message) +
            '<span class="notif-time">' + timeAgo(n.ts) + '</span>' +
            '</span>' +
            '</div>'
        )).join('');
    }

    function add(payload) {
        if (!payload || !payload.message) return;
        const items = load();
        items.unshift({ message: payload.message, level: payload.level || 'info', ts: Date.now(), read: false });
        save(items.slice(0, MAX_ITEMS));
        render();
    }

    function markAllRead() {
        save(load().map((n) => ({ ...n, read: true })));
        render();
    }

    function openPanel() {
        panel.classList.add('open');
        panel.setAttribute('aria-hidden', 'false');
        toggle.setAttribute('aria-expanded', 'true');
        markAllRead();
    }

    function closePanel() {
        panel.classList.remove('open');
        panel.setAttribute('aria-hidden', 'true');
        toggle.setAttribute('aria-expanded', 'false');
    }

    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        if (panel.classList.contains('open')) closePanel(); else openPanel();
    });

    document.addEventListener('click', (e) => {
        if (!wrap.contains(e.target)) closePanel();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closePanel();
    });

    if (clearBtn) {
        clearBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            save([]);
            render();
        });
    }

    render();
    return { add };
}

// Halo — the floating AI assistant widget (see its markup + CSS in
// base.html). Self-contained like initNotificationBell() above: its DOM
// lives outside .content, so this only ever runs once per real page load
// (DOMContentLoaded), and the closure state below (history, open/closed,
// unread) survives every soft nav in between exactly the way the
// notification bell's own state does.
function initHaloWidget() {
    const widget = document.getElementById('haloWidget');
    const fab = document.getElementById('haloFabBtn');
    const panel = document.getElementById('haloPanel');
    const log = document.getElementById('haloLog');
    const form = document.getElementById('haloForm');
    const input = document.getElementById('haloInput');
    const sendBtn = document.getElementById('haloSend');
    if (!widget || !fab || !panel || !log || !form || !input || !sendBtn) return;

    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = csrfMeta ? csrfMeta.content : '';
    const history = [];
    let empty = document.getElementById('haloEmpty');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // Same light markdown subset as the full chat page (ai/chat.html) —
    // the model occasionally uses **/* despite being told to write plain
    // text. Escaped first, so this never introduces real HTML/script
    // injection, it only turns already-escaped markers into tags.
    function formatAssistantText(text) {
        let escaped = escapeHtml(text);
        escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        escaped = escaped.replace(/(^|\s)\*([^*\s][^*]*?)\*(?=\s|$)/g, '$1<em>$2</em>');
        escaped = escaped.replace(/\n/g, '<br>');
        return escaped;
    }

    function isOpen() {
        return widget.classList.contains('open');
    }

    function openPanel() {
        widget.classList.add('open');
        widget.classList.remove('has-unread');
        panel.hidden = false;
        panel.classList.add('opening');
        window.setTimeout(() => panel.classList.remove('opening'), 200);
        fab.setAttribute('aria-expanded', 'true');
        input.focus();
        log.scrollTop = log.scrollHeight;
    }

    function closePanel() {
        widget.classList.remove('open');
        panel.hidden = true;
        fab.setAttribute('aria-expanded', 'false');
    }

    fab.addEventListener('click', () => {
        if (isOpen()) closePanel(); else openPanel();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isOpen()) closePanel();
    });

    function addMessage(role, text) {
        if (empty) { empty.remove(); empty = null; }

        const row = document.createElement('div');
        row.className = 'halo-row ' + role;

        const bubble = document.createElement('div');
        bubble.className = 'halo-msg';
        if (role === 'user') {
            bubble.textContent = text;
        } else {
            bubble.innerHTML = formatAssistantText(text);
        }
        row.appendChild(bubble);
        log.appendChild(row);
        log.scrollTop = log.scrollHeight;

        if (window.Motion && !reduceMotion) {
            const anim = window.Motion.animate(
                row,
                { opacity: [0, 1], transform: ['translateY(6px)', 'translateY(0px)'] },
                { duration: 0.22, easing: [0.16, 1, 0.3, 1] }
            );
            if (anim && typeof anim.then === 'function') anim.then(null, () => { });
        }

        // Only reachable for an assistant/error reply that arrives after
        // the user closed the panel while a request was in flight — the
        // panel has to be open to send a message in the first place.
        if (role !== 'user' && !isOpen()) {
            widget.classList.add('has-unread');
        }

        return row;
    }

    function addTypingIndicator() {
        if (empty) { empty.remove(); empty = null; }
        const row = document.createElement('div');
        row.className = 'halo-row assistant';
        row.innerHTML = '<div class="halo-msg"><div class="halo-typing-dots"><span></span><span></span><span></span></div></div>';
        log.appendChild(row);
        log.scrollTop = log.scrollHeight;
        return row;
    }

    function sendMessage(message) {
        if (!message) return;

        addMessage('user', message);
        const sentHistory = history.slice();
        history.push({ role: 'user', text: message });

        input.value = '';
        input.style.height = 'auto';
        input.disabled = true;
        sendBtn.disabled = true;

        const thinking = addTypingIndicator();

        fetch('/ai/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify({ message: message, history: sentHistory })
        })
            .then((r) => r.json().then((data) => ({ ok: r.ok, data: data })))
            .then((res) => {
                thinking.remove();
                if (res.ok && res.data.reply) {
                    addMessage('assistant', res.data.reply);
                    history.push({ role: 'model', text: res.data.reply });
                } else {
                    addMessage('error', res.data.error || 'Something went wrong.');
                }
            })
            .catch(() => {
                thinking.remove();
                addMessage('error', 'Could not reach Halo. Check your connection and try again.');
            })
            .finally(() => {
                input.disabled = false;
                sendBtn.disabled = false;
                if (isOpen()) input.focus();
            });
    }

    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 100) + 'px';
    });

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            form.requestSubmit();
        }
    });

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        sendMessage(input.value.trim());
    });

    if (empty) {
        empty.addEventListener('click', (e) => {
            const btn = e.target.closest('.halo-suggestion');
            if (!btn) return;
            sendMessage(btn.dataset.q);
        });
    }
}

function initRealtime(notifBell) {
    if (typeof io === 'undefined') return;

    const ROUTE_SCOPES = [
        { match: /^\/admin\/production\/?$/, scopes: ['production', 'inventory', 'movement_logs'] },
        { match: /^\/admin\/requests\/?$/, scopes: ['requests'] },
        { match: /^\/admin\/branch-stock\/?$/, scopes: ['inventory'] },
        { match: /^\/admin\/movement-logs\/?$/, scopes: ['movement_logs'] },
        { match: /^\/admin\/products\/?$/, scopes: ['products'] },
        { match: /^\/admin\/branches\/?$/, scopes: ['branches'] },
        { match: /^\/admin\/partners\/?$/, scopes: ['partners'] },
        { match: /^\/admin\/packages(\/\d+)?\/?$/, scopes: ['packages'] },
        { match: /^\/admin\/partners\/inquiries\/?$/, scopes: ['partner_inquiries'] },
        { match: /^\/admin\/users\/?$/, scopes: ['users'] },
        { match: /^\/ai\/drafts\/?$/, scopes: ['ai_drafts'] },
        { match: /^\/admin\/?$/, scopes: ['requests', 'inventory', 'movement_logs', 'production'] },
        { match: /^\/branch\/inventory\/?$/, scopes: ['inventory'] },
        { match: /^\/branch\/request-stock\/?$/, scopes: ['requests'] },
        { match: /^\/branch\/receive-stock\/?$/, scopes: ['requests', 'inventory'] },
        { match: /^\/branch\/record-sale\/?$/, scopes: ['inventory', 'sales'] },
        { match: /^\/branch\/sales-history\/?$/, scopes: ['sales'] },
        { match: /^\/branch\/?$/, scopes: ['requests', 'inventory', 'sales'] },
    ];

    function currentScopes() {
        const path = window.location.pathname;
        const hit = ROUTE_SCOPES.find((r) => r.match.test(path));
        return hit ? hit.scopes : [];
    }

    function activeFieldInsideContent() {
        const el = document.activeElement;
        if (!el) return false;
        if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && el.tagName !== 'SELECT') return false;
        return !!el.closest('.content');
    }

    let pendingRefresh = false;
    let suppressUntil = 0;

    // Whether the user has typed/selected anything, anywhere in .content,
    // that a soft-refresh would silently discard. activeFieldInsideContent()
    // alone isn't enough: it only reflects the field focused at the exact
    // instant a refresh is attempted, and there's a brief window while
    // focus is moving between two fields (e.g. Tab from one input to the
    // next) where neither the old nor the new field reads as "focused"
    // yet. A refresh landing in that gap replaces the whole .content DOM
    // with a fresh server render, wiping every field the user had already
    // filled in and visibly jumping the page under them. Tracking "has
    // anything changed" independently of focus closes that gap.
    let contentDirty = false;

    document.addEventListener('input', (e) => {
        if (e.target && e.target.closest && e.target.closest('.content')) {
            contentDirty = true;
        }
    }, true);
    document.addEventListener('change', (e) => {
        if (e.target && e.target.closest && e.target.closest('.content')) {
            contentDirty = true;
        }
    }, true);

    document.addEventListener('submit', (e) => {
        if (e.target && e.target.closest && e.target.closest('.content')) {
            suppressUntil = Date.now() + 2500;
            contentDirty = false;
        }
    }, true);

    function softRefresh() {
        if (Date.now() < suppressUntil) return;
        if (activeFieldInsideContent() || contentDirty) {
            pendingRefresh = true;
            return;
        }

        fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
            .then((html) => {
                const fresh = new DOMParser().parseFromString(html, 'text/html');
                swapContent(fresh);
                contentDirty = false;
            })
            .catch(() => {

            });
    }

    function refreshBadgesOnly() {

        fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
            .then((html) => {
                const fresh = new DOMParser().parseFromString(html, 'text/html');
                patchSidebarNav(fresh);
            })
            .catch(() => { });
    }

    document.addEventListener('focusout', () => {
        // Deferred to the next tick: at the instant focusout fires, the
        // browser hasn't necessarily settled document.activeElement onto
        // wherever focus is actually landing next (e.g. Tab to the next
        // field in the same form) — checking synchronously here risks
        // treating an in-form focus move as "left the form entirely."
        setTimeout(() => {
            if (pendingRefresh && !activeFieldInsideContent() && !contentDirty) {
                pendingRefresh = false;
                softRefresh();
            }
        }, 0);
    }, true);

    const socket = io();
    socket.on('data_changed', (payload) => {
        const scopes = (payload && payload.scopes) || [];
        const mine = currentScopes();
        if (scopes.some((s) => mine.includes(s))) {
            softRefresh();
        } else if (scopes.includes('requests') || scopes.includes('partner_inquiries') || scopes.includes('ai_drafts')) {
            refreshBadgesOnly();
        }
    });
    socket.on('bell_notification', (payload) => {
        if (notifBell) notifBell.add(payload);
    });
}

function initPhClock() {
    const el = document.getElementById('phClock');
    if (!el) return;

    const formatter = new Intl.DateTimeFormat('en-PH', {
        timeZone: 'Asia/Manila',
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
    });

    function tick() {
        el.textContent = formatter.format(new Date());
    }

    tick();
    setInterval(tick, 1000);
}
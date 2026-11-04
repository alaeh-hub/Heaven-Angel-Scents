// Soft navigation: swap page content without a full reload
// (Part of the app's main script; loaded in order by base.html.)

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
function refreshDynamicContent(skipReveal) {
    safeInit(initFillBars, 'fillBars');
    safeInit(initSmartTables, 'smartTables');
    safeInit(initSmartLists, 'smartLists');
    safeInit(initDispatchQtyWarnings, 'dispatchQtyWarnings');
    safeInit(initCustomSelects, 'customSelects');
    safeInit(initDatePickers, 'datePickers');
    // initConfirmDialogs() is intentionally NOT re-called here: it's
    // delegated on `document` once at page load (see its own comment),
    // so it already covers any form[data-confirm] that just got
    // swapped in — re-attaching per element isn't necessary.
    // revealContent()'s whole-content fade-in (every .content child
    // popping to opacity:0 then staggering back in) and
    // initStatCountUp()'s "animate every stat tile up from 0" are both
    // right for an actual page load/navigation, but skipReveal (passed
    // for a same-path tab/filter soft nav — see go()'s preserveScroll)
    // turns them off: fading the whole page out and back in, and every
    // number on it visibly re-counting from zero, just because one
    // location tab was clicked, is indistinguishable from a real
    // reload — exactly the feeling tabs shouldn't give. The swapped-in
    // server-rendered numbers/markup are already correct as-is without
    // either animation.
    if (!skipReveal) {
        safeInit(revealContent, 'revealContent');
        safeInit(initStatCountUp, 'statCountUp');
        safeInit(initEntranceStagger, 'entranceStagger');
    }
}

// Replaces the live .content with freshDoc's .content, if it has one.
// Returns false (and touches nothing) when it doesn't — a page outside
// the signed-in app shell (the login screen after a session expires
// mid-navigation, most notably) never renders a .content at all, and
// swapping nothing in would leave the visible page silently pretending
// a request that actually landed somewhere else still succeeded.
// Callers fall back to a real navigation (window.location) when this
// returns false.
function swapContent(freshDoc, skipReveal) {
    const freshContent = freshDoc.querySelector('.content');
    const liveContent = document.querySelector('.content');
    if (!freshContent || !liveContent) return false;
    liveContent.replaceWith(freshContent);
    if (freshDoc.title) document.title = freshDoc.title;
    patchSidebarNav(freshDoc);
    patchTopbar(freshDoc);
    patchPageHead(freshDoc);
    refreshDynamicContent(skipReveal);
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
        // The fetched page carries its own per-response nonce, which the live
        // page's CSP header doesn't know. Stamp this page's nonce instead
        // (from <meta name="csp-nonce">, which the browser doesn't hide).
        const liveNonce = document.querySelector('meta[name="csp-nonce"]');
        if (liveNonce) newScript.nonce = liveNonce.content;

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
function getFlashContainer() {
    let liveContainer = document.querySelector('.flashes');
    if (!liveContainer) {
        liveContainer = document.createElement('div');
        liveContainer.className = 'flashes';
        liveContainer.setAttribute('role', 'status');
        liveContainer.setAttribute('aria-live', 'polite');
        document.body.appendChild(liveContainer);
    }
    return liveContainer;
}

// Pops a toast from the client side, in the same stack and style as
// server flashes — used for live bell notifications (see initRealtime())
// so they're seen as they arrive rather than only as a +1 on the bell.
// level is the bell's "info" | "success" | "warning"; info uses the
// plain .flash look. textContent, never innerHTML: the message can
// carry user-entered text (a partner's company name, an announcement).
function showToast(message, level) {
    if (!message) return;
    const el = document.createElement('div');
    el.className = 'flash' + (level === 'success' || level === 'warning' ? ' flash-' + level : '');
    el.textContent = message;
    getFlashContainer().appendChild(el);
    attachFlashDismiss(el);
}

function appendFreshFlashes(freshDoc) {
    const freshFlashes = freshDoc.querySelectorAll('.flashes .flash');
    if (!freshFlashes.length) return;
    const liveContainer = getFlashContainer();
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
    function go(request, url, historyMode, preserveScroll) {
        const myToken = ++navToken;
        const scrollY = window.scrollY;
        navProgress.start();

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
                navProgress.done();

                const fresh = new DOMParser().parseFromString(html, 'text/html');
                if (!swapContent(fresh, preserveScroll)) {
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
                if (historyMode === 'pop' || preserveScroll) {
                    // 'pop' (back/forward) always restores where the user
                    // was. preserveScroll is for a same-path soft nav
                    // (e.g. a tab/filter link that only changes the query
                    // string, like Low Stock's location tabs) — that's
                    // updating the view the user is already looking at,
                    // not going somewhere else, so snapping to the top
                    // the way a real page load would is exactly what
                    // reads as "the page just reloaded" even though
                    // nothing actually did.
                    window.scrollTo({ top: scrollY, behavior: 'auto' });
                } else {
                    window.scrollTo({ top: 0, behavior: 'auto' });
                }
            })
            .catch(() => {
                if (myToken !== navToken) return;
                hideLoadingOverlay();
                navProgress.done();
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
        if (isSamePageNoOpLink(link)) {
            // e.g. clicking the sidebar tab that's already open.
            // Nothing on the page needs to change, so this must be a
            // true no-op: previously this fell through to the
            // browser's own default click behavior, which is a full
            // page reload since the href matches the current URL
            // exactly. That reload reset everything — scroll
            // position, and (since the sidebar only stays expanded
            // via CSS :hover/:focus-within, see
            // initSidebarNavTooltips() above) the sidebar visibly
            // snapped back to its collapsed rail, looking like the
            // click had "closed" it.
            e.preventDefault();
            return;
        }
        if (url.pathname === window.location.pathname && url.search === window.location.search) {
            // Same page, but the link targets a real #hash (an
            // in-page anchor) — let the browser's own default
            // handling do the jump instead of "navigating" to a
            // no-op and losing that behavior.
            return;
        }

        e.preventDefault();
        // Same path, different query string — a tab/filter link (Low
        // Stock's location tabs, Requests' status tabs, ...) rather than
        // a real navigation to a different page. Keep the scroll
        // position for those; see go()'s preserveScroll param.
        const preserveScroll = url.pathname === window.location.pathname;
        go(() => fetch(link.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } }), link.href, 'push', preserveScroll);
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
        // Opt-in for a form that only adds/removes one line of an
        // in-page list (formulas.html's packaging-materials cart, a
        // status-update dropdown, ...) rather than navigating anywhere —
        // snapping to the top and repainting the whole page after every
        // such action is what reads as "the page just reloaded" even
        // though nothing actually did, same reasoning as the tab/filter
        // links above.
        const preserveScroll = form.hasAttribute('data-preserve-scroll');
        go(
            () => fetch(form.action, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            }),
            form.action,
            'push',
            preserveScroll,
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
// attribute — nothing in the app uses it any more (the Partner
// Inquiries status picker is now an enhanced select with
// data-cs-variant="status"; see the .cs-variant-status rules in
// style.css).
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
// Stock-movement types in the order they happen (make, ship, receive,
// sell, correct), so the Ledger movement legend reads the same way every
// time instead of in whatever order the data happened to come back.
var MOVEMENT_TYPE_ORDER = ['PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'REFILL', 'FREEBIE', 'ADJUSTMENT', 'DAMAGE',
    'PACKAGE_ORDER', 'PACKAGE_RETURN', 'SALE_VOID'];


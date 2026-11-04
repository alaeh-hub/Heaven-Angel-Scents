// Theme toggle, fill bars, count-up, entrance motion, login splash, DOMContentLoaded boot
// (Part of the app's main script; loaded in order by base.html.)

function initThemeToggle() {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;

    const root = document.documentElement;

    btn.addEventListener('click', () => {
        const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        try {
            localStorage.setItem('theme', next);
        } catch (e) {

        }

        // The new theme spreads out in a circle from the button. One
        // snapshot is clipped on the compositor, which is far cheaper than
        // transitioning the colours of every element on the page.
        if (typeof document.startViewTransition === 'function' && !PREFERS_REDUCED_MOTION.matches) {
            const r = btn.getBoundingClientRect();
            const x = r.left + r.width / 2;
            const y = r.top + r.height / 2;
            const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
            root.classList.add('vt-theme');
            const transition = document.startViewTransition(() => {
                root.setAttribute('data-theme', next);
            });
            transition.ready.then(() => {
                root.animate(
                    { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
                    { duration: 560, easing: 'cubic-bezier(0.65, 0, 0.35, 1)', pseudoElement: '::view-transition-new(root)' }
                );
            }).catch(() => { });
            transition.finished.finally(() => root.classList.remove('vt-theme'));
        } else {
            root.classList.add('theme-transitioning');
            root.setAttribute('data-theme', next);
            const isTouch = window.matchMedia('(hover: none), (pointer: coarse)').matches;
            window.setTimeout(() => root.classList.remove('theme-transitioning'), isTouch ? 180 : 400);
        }

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

// Sets each fill-bar to its data-pct width so the CSS transition on
// .fill-bar (see style.css) actually animates it filling in, instead of
// the bar just appearing at its final width. Setting width to 0% first
// and waiting a frame (rather than jumping straight to the target
// width) is what gives the transition a "from" state to animate out of
// — without it, the browser has nothing to interpolate between and the
// bar would still just snap into place.
function initFillBars() {
    document.querySelectorAll('.fill-bar[data-pct]').forEach((el) => {
        const pct = parseFloat(el.dataset.pct) || 0;
        el.style.setProperty('--pct', '0');
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                el.style.setProperty('--pct', String(Math.min(Math.max(pct, 0), 100) / 100));
            });
        });
    });
}

// Counts a stat-tile's number up from 0 to its rendered value instead
// of it just sitting there static — a state-transition cue that a
// dashboard value has landed, not decoration. Only touches values that
// are a plain integer with no other characters (branch/request/alert
// counts): anything with a currency symbol, comma, or decimal (peso-
// formatted totals) is left alone rather than risk re-formatting it
// wrong. Runs once per element (data-countup-done) since revealContent()
// re-fires on every soft nav and would otherwise re-count a tile that
// never actually changed value.
function initStatCountUp() {
    if (!window.anime || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    document.querySelectorAll('.stat-value').forEach((el) => {
        if (el.dataset.countupDone) return;
        const raw = el.textContent.trim();
        if (!/^\d+$/.test(raw)) return;
        const target = parseInt(raw, 10);
        if (!target) return;

        el.dataset.countupDone = '1';
        const counter = { val: 0 };
        window.anime.animate(counter, {
            val: target,
            duration: 700,
            ease: 'outExpo',
            onUpdate: () => { el.textContent = String(Math.round(counter.val)); },
            onComplete: () => { el.textContent = String(target); },
        });
    });
}

// Stat tiles spring up one after another and table rows slide in down the
// page, so a freshly loaded screen builds top to bottom instead of
// appearing all at once (state transition: new data just landed).
// Inline transform/opacity are cleared when each finishes so the tiles'
// and rows' own CSS hover lift keeps working. Capped at the first 14 rows
// (anything below the fold is never seen animating) and, like
// initStatCountUp(), skipped under reduced motion and on tab-style soft
// navs (refreshDynamicContent's skipReveal).
function initEntranceStagger() {
    // Intentionally empty. Tiles and rows used to slide in one by one on top of
    // the page transition and the content reveal, so a freshly opened table
    // moved three ways at once. One reveal per page now (see revealContent).
}

function revealContent() {
    // Soft navigations already cross-fade through the browser's view transition
    // (style.css), so the content must not animate a second time. Browsers
    // without view transitions get a plain opacity fade, no movement.
    if (typeof document.startViewTransition === 'function') return;
    if (!window.Motion || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const content = document.querySelector('.content');
    if (!content) return;

    var revealAnim = window.Motion.animate(content, { opacity: [0, 1] }, { duration: 0.18, easing: 'ease-out' });
    if (revealAnim && typeof revealAnim.then === 'function') {
        revealAnim.then(null, function () { });
    }
}

// Holds #loginSplash (see base.html, shown once right after a
// successful sign-in) on screen briefly, then fades it out via the
// .login-splash-exit CSS class and removes it from the DOM entirely
// — not just hidden — so it doesn't linger as dead markup sitting on
// top of nothing. Plain CSS animation + setTimeout, same reasoning as
// showLoadingOverlay() above: nothing here needs a promise-based
// animation library, and remove() after a fixed delay is simpler.
//
// When motion is allowed, the logo-reveal video plays first and the
// splash exits when it ends. Clicking or pressing any key skips it,
// and a hard cap guarantees the app is never stuck behind a stalled
// video. If autoplay is refused, it falls back to the static wordmark.
const LOGIN_SPLASH_HOLD_MS = 2400;
const LOGIN_SPLASH_REDUCED_HOLD_MS = 600;
const LOGIN_SPLASH_EXIT_MS = 350;

function initLoginSplash() {
    const el = document.getElementById('loginSplash');
    if (!el) return;

    let done = false;
    const dismiss = () => {
        if (done) return;
        done = true;
        document.removeEventListener('keydown', dismiss);
        el.classList.add('login-splash-exit');
        setTimeout(() => el.remove(), LOGIN_SPLASH_EXIT_MS);
    };

    // Clicking or pressing any key skips it; otherwise it leaves on its own
    // once the reveal has played and held for a beat.
    el.addEventListener('click', dismiss);
    document.addEventListener('keydown', dismiss);
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setTimeout(dismiss, reduceMotion ? LOGIN_SPLASH_REDUCED_HOLD_MS : LOGIN_SPLASH_HOLD_MS);
}

// Runs `fn`, logging and swallowing any exception instead of letting it
// propagate. Every init below is called from one synchronous
// DOMContentLoaded listener (and, via refreshDynamicContent(), from
// every soft-nav content swap) — without this, a single page-specific
// widget throwing (a report page's own table markup tripping up
// initSmartTables(), say) would abort every init call still queued
// after it, including initSoftNav() itself. That doesn't just break
// the widget that threw — it silently leaves click/submit interception
// never registered for the rest of that page load, so every link and
// form on it (tabs very much included) falls back to the browser's
// own real, full-reload navigation instead of a soft one. Isolating
// each init this way means one broken widget stays broken on its own,
// instead of quietly taking soft nav down with it.
// The mascot (static/js/mascot.js). Both of its slots — Halo's avatar
// and Halo's greeting — are [data-mascot] elements in the shell rather
// than in .content, so they survive a soft navigation untouched and
// this only needs to run on a real page load. (The sign-in screen
// mounts its own, in login.html; it is not part of this shell.)
function initMascots() {
    if (!window.Mascot) return;
    window.Mascot.initAll();
}

function safeInit(fn, label) {
    try {
        return fn();
    } catch (err) {
        console.error('[init:' + label + ']', err);
        return undefined;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    safeInit(initLoginSplash, 'loginSplash');
    safeInit(initNavAccordion, 'navAccordion');
    safeInit(initMobileSidebar, 'mobileSidebar');
    safeInit(initSidebarNavTooltips, 'sidebarNavTooltips');
    safeInit(initSidebarPinToggle, 'sidebarPinToggle');
    safeInit(initSidebarHoverIntent, 'sidebarHoverIntent');
    safeInit(initSidebarIndicator, 'sidebarIndicator');
    safeInit(initSidebarSearch, 'sidebarSearch');
    safeInit(initTopbarSearch, 'topbarSearch');
    safeInit(registerServiceWorker, 'serviceWorker');
    safeInit(initSidebarKeyboardNav, 'sidebarKeyboardNav');
    safeInit(initSidebarScrollShadow, 'sidebarScrollShadow');
    safeInit(initThemeToggle, 'themeToggle');
    safeInit(initSettings, 'settings');
    safeInit(initPhClock, 'phClock');
    safeInit(initTopbarMotion, 'topbarMotion');
    safeInit(initTodaySalesChip, 'todaySalesChip');
    safeInit(revealContent, 'revealContent');
    safeInit(initStatCountUp, 'statCountUp');
    safeInit(initEntranceStagger, 'entranceStagger');
    safeInit(initFillBars, 'fillBars');
    safeInit(initMascots, 'mascots');

    safeInit(() => document.querySelectorAll('.flash').forEach(attachFlashDismiss), 'flashDismiss');

    safeInit(initConfirmDialogs, 'confirmDialogs');
    safeInit(initConfirmModal, 'confirmModal');

    safeInit(initSmartTables, 'smartTables');
    safeInit(initSmartLists, 'smartLists');
    safeInit(initDispatchQtyWarnings, 'dispatchQtyWarnings');
    safeInit(initCustomSelects, 'customSelects');
    safeInit(initDatePickers, 'datePickers');
    safeInit(watchForNewFormControls, 'watchFormControls');
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
    safeInit(initSubmitLoadingState, 'submitLoadingState');
    safeInit(initGenerateFileFeedback, 'generateFileFeedback');
    safeInit(initSoftNav, 'softNav');
    const notifBell = safeInit(initNotificationBell, 'notificationBell');
    safeInit(() => initRealtime(notifBell), 'realtime');
    safeInit(initHaloWidget, 'haloWidget');
});


// Flash messages, badges, sidebar/topbar patching, today's-sales chip
// (Part of the app's main script; loaded in order by base.html.)

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

// Brief scale bounce on a badge whose count just went up (a new stock
// request, a new inquiry) — the same feedback treatment the theme
// toggle already uses on click, applied here to a real, sparse state
// change rather than every render.
// Animates the standalone CSS `scale` property rather than `transform`,
// so it composes with whatever positioning transform the badge already
// has from CSS (e.g. .nav-badge's translateY(-50%), which the collapsed
// sidebar rail swaps for translateY(0)) instead of replacing it. Plain
// WAAPI with no fill, so nothing is left behind as an inline style once
// it ends — an earlier Motion-based version pinned the expanded rail's
// translateY(-50%) inline after every pulse, knocking the badge half
// out of view on the collapsed rail.
function pulseBadge(el) {
    if (!el || typeof el.animate !== 'function'
        || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    el.animate(
        { scale: ['1', '1.35', '1'] },
        { duration: 400, easing: 'cubic-bezier(0.34, 1.56, 0.64, 1)' }
    );
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
        // A group parent and its first page share an href; keep them apart.
        const kind = link.classList.contains('nav-sub') ? '.nav-sub' : ':not(.nav-sub)';
        const freshLink = freshDoc.querySelector(`.nav-link${kind}[href="${href}"]`);
        if (!freshLink) return;

        const liveBadge = link.querySelector('.nav-badge');
        const freshBadge = freshLink.querySelector('.nav-badge');
        if (liveBadge && !freshBadge) {
            liveBadge.remove();
        } else if (freshBadge && !liveBadge) {
            const added = freshBadge.cloneNode(true);
            link.appendChild(added);
            pulseBadge(added);
        } else if (freshBadge && liveBadge) {
            const before = parseInt(liveBadge.textContent, 10) || 0;
            const after = parseInt(freshBadge.textContent, 10) || 0;
            liveBadge.textContent = freshBadge.textContent;
            if (after > before) pulseBadge(liveBadge);
        }

        link.classList.toggle('active', freshLink.classList.contains('active'));
    });
    syncSidebarIndicator();
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

    // The chip keeps its live odometer DOM; only its values are updated.
    const freshChip = freshDoc.getElementById('todaySalesChip');
    if (freshChip && freshChip.dataset.summary) {
        try { applyTodaySales(JSON.parse(freshChip.dataset.summary)); } catch (e) { }
    }
}

const PREFERS_REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');

// Gold hairline along the top bar's bottom edge while a soft navigation
// is in flight. Waits a beat before showing so instant loads don't
// flicker it; finishing sweeps it to full width and fades it out.
const navProgress = (() => {
    let showTimer = null;
    let shown = false;

    function el() { return document.getElementById('topbarProgress'); }

    function start() {
        const bar = el();
        if (!bar) return;
        clearTimeout(showTimer);
        showTimer = setTimeout(() => {
            shown = true;
            bar.classList.remove('is-done');
            bar.style.removeProperty('--from');
            void bar.offsetWidth;
            bar.classList.add('is-loading');
        }, 120);
    }

    function done() {
        const bar = el();
        clearTimeout(showTimer);
        if (!bar || !shown) return;
        shown = false;
        const m = /matrix\(([^,]+)/.exec(getComputedStyle(bar).transform);
        bar.style.setProperty('--from', m ? m[1] : '0.3');
        bar.classList.remove('is-loading');
        void bar.offsetWidth;
        bar.classList.add('is-done');
    }

    return { start, done };
})();

// One-time entrance for the top bar's items on a full page load (never
// on soft navigation, which keeps the same bar), plus the "content is
// scrolled underneath" shadow, driven by an IntersectionObserver on a
// marker above the sticky bar instead of a scroll listener.
function initTopbarMotion() {
    const bar = document.getElementById('topbar');
    if (!bar) return;

    if (!PREFERS_REDUCED_MOTION.matches) {
        const items = bar.querySelectorAll('.topbar-left > :not(.topbar-title), .topbar-right > *');
        items.forEach((item, i) => item.style.setProperty('--tb-i', String(i)));
        bar.classList.add('is-entering');
        setTimeout(() => bar.classList.remove('is-entering'), 900 + items.length * 50);
    }

    const sentinel = document.getElementById('topbarSentinel');
    if (sentinel && typeof IntersectionObserver === 'function') {
        new IntersectionObserver(([entry]) => {
            bar.classList.toggle('is-scrolled', !entry.isIntersecting);
        }).observe(sentinel);
    }
}

// Odometer: each digit is a 0-9 column that slides to its value, so a
// changing figure rolls instead of blinking. Transform-only, and only
// the digits whose value changed actually move.
function setOdometer(el, text, opts) {
    if (!el) return;
    text = String(text);
    const animate = !(opts && opts.instant) && !PREFERS_REDUCED_MOTION.matches;
    const prev = el.dataset.odoText;
    if (prev === text) return;

    const sameShape = prev !== undefined && prev.length === text.length &&
        prev.replace(/\d/g, '0') === text.replace(/\d/g, '0');

    if (!sameShape) {
        el.textContent = '';
        el.classList.add('odo');
        Array.from(text).forEach((ch, i) => {
            if (/\d/.test(ch)) {
                const cell = document.createElement('span');
                cell.className = 'odo-cell';
                const strip = document.createElement('span');
                strip.className = 'odo-strip';
                for (let d = 0; d <= 9; d++) {
                    const n = document.createElement('span');
                    n.textContent = d;
                    strip.appendChild(n);
                }
                cell.appendChild(strip);
                // A new shape starts every digit at 0 and rolls up to its value.
                strip.style.transform = 'translateY(0)';
                cell.style.setProperty('--odo-i', String(text.length - i));
                el.appendChild(cell);
            } else {
                const sym = document.createElement('span');
                sym.className = 'odo-sym';
                sym.textContent = ch;
                el.appendChild(sym);
            }
        });
        if (animate) void el.offsetWidth;
    }

    const strips = el.querySelectorAll('.odo-strip');
    let s = 0;
    el.classList.toggle('odo-animate', animate);
    Array.from(text).forEach((ch) => {
        if (!/\d/.test(ch)) return;
        strips[s++].style.transform = `translateY(${-Number(ch) * 10}%)`;
    });
    el.dataset.odoText = text;
}

// Top bar "Today" chip: re-fetched (small JSON, not a page render) when
// a sale is recorded/voided anywhere this user can see, when the tab
// comes back into view, and every few minutes so it rolls over at
// midnight even on a quiet day.
let todaySalesInFlight = false;

function refreshTodaySales() {
    if (todaySalesInFlight || !document.getElementById('todaySalesChip')) return;
    todaySalesInFlight = true;
    fetch('/api/today-sales', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((data) => applyTodaySales(data))
        .catch(() => { })
        .finally(() => { todaySalesInFlight = false; });
}

function applyTodaySales(data, opts) {
    const chip = document.getElementById('todaySalesChip');
    if (!chip || !data) return;
    const instant = !!(opts && opts.instant);
    const last = chip._todayLast;
    const changed = !last || last.revenue !== data.revenue || last.count !== data.count;

    setOdometer(chip.querySelector('[data-today-odo="revenue_display"]'), data.revenue_display, { instant });
    setOdometer(chip.querySelector('[data-today-odo="revenue_compact"]'), data.revenue_compact, { instant });
    setOdometer(chip.querySelector('[data-today-odo="count"]'), data.count, { instant });
    const word = chip.querySelector('[data-today-field="count_word"]');
    if (word) word.textContent = data.count === 1 ? 'sale' : 'sales';

    const trendEl = chip.querySelector('[data-today-field="trend"]');
    if (trendEl) {
        const t = data.trend;
        const wasHidden = trendEl.hidden;
        trendEl.hidden = t === null || t === undefined;
        if (!trendEl.hidden) {
            trendEl.className = 'today-chip-trend ' + (t > 0 ? 'up' : t < 0 ? 'down' : 'flat');
            trendEl.textContent = (t > 0 ? '▲ ' : t < 0 ? '▼ ' : '• ') + Math.abs(t) + '%';
            if (wasHidden && !instant) trendEl.classList.add('is-new');
        }
    }

    const countLabel = data.count + ' sale' + (data.count === 1 ? '' : 's');
    chip.title = "Today's sales · " + data.scope;
    chip.setAttribute('aria-label', "Today's sales for " + data.scope + ': ' + data.revenue_display +
        ' from ' + countLabel);
    chip.dataset.summary = JSON.stringify(data);
    chip._todayLast = { revenue: data.revenue, count: data.count };

    if (last && changed && !instant && !PREFERS_REDUCED_MOTION.matches) {
        chip.classList.remove('is-updated');
        void chip.offsetWidth;
        chip.classList.add('is-updated');
    }
}

function initTodaySalesChip() {
    const chip = document.getElementById('todaySalesChip');
    if (!chip) return;
    chip.addEventListener('animationend', (e) => {
        if (e.target === chip) chip.classList.remove('is-updated');
    });

    // First paint: lay the odometers out at zero, then roll them up to
    // today's figures once the top bar has entered.
    let data = null;
    try { data = JSON.parse(chip.dataset.summary); } catch (e) { }
    if (data) {
        if (PREFERS_REDUCED_MOTION.matches) {
            applyTodaySales(data, { instant: true });
        } else {
            const zeroed = Object.assign({}, data, {
                revenue_display: data.revenue_display.replace(/\d/g, '0'),
                revenue_compact: data.revenue_compact.replace(/\d/g, '0'),
                count: Number(String(data.count).replace(/\d/g, '0')),
            });
            applyTodaySales(zeroed, { instant: true });
            chip._todayLast = null;
            setTimeout(() => {
                applyTodaySales(data);
                chip._todayLast = { revenue: data.revenue, count: data.count };
            }, 260);
        }
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') refreshTodaySales();
    });
    setInterval(() => {
        if (document.visibilityState === 'visible') refreshTodaySales();
    }, 5 * 60 * 1000);
}


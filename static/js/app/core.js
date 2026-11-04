// CSRF helper, same-page link check, sidebar scroll/gutter/title helpers
// (Part of the app's main script; loaded in order by base.html.)

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

// True when `link` points to exactly the page already showing — same
// pathname + query string, no #hash target — i.e. a click that
// shouldn't do anything at all: not reload, not close the mobile
// sidebar overlay, nothing. (A link that only differs by #hash is
// deliberately NOT treated as a no-op here — that's a real in-page
// anchor jump and should still happen.) Shared by initMobileSidebar()
// (skip closing the sidebar for it) and initSoftNav() (skip
// navigating for it) below.
function isSamePageNoOpLink(link) {
    let url;
    try {
        url = new URL(link.href, window.location.href);
    } catch (e) {
        return false;
    }
    return url.pathname === window.location.pathname &&
        url.search === window.location.search &&
        !url.hash;
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

// Measures the scrollbar gutter .sidebar reserves on each side
// (scrollbar-gutter: stable both-edges) and exposes it as
// --sidebar-gutter, which style.css's --sidebar-w-collapsed adds back
// so the rail's icons sit exactly centred. The gutter's real width
// depends on the browser and zoom level, so CSS can't know it. Runs
// immediately (this script loads at the end of <body>) to settle the
// rail width before first paint, and again on resize for zoom changes.
(function initSidebarGutter() {
    const sidebar = document.getElementById('sidebar');
    const brand = sidebar && sidebar.querySelector('.brand');
    // base.html's head script adds html.sidebar-booting (transitions frozen);
    // lift it two frames after the first measurement has been applied.
    const settle = () => requestAnimationFrame(() => requestAnimationFrame(
        () => document.documentElement.classList.remove('sidebar-booting')));
    if (!brand) { settle(); return; }

    function measure() {
        const cs = getComputedStyle(sidebar);
        // The reserved scrollbar gutter is whatever the scroll box gives up
        // beside its content; independent of how wide the rail is laid out.
        const gutter = sidebar.offsetWidth - sidebar.clientWidth
            - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth);
        if (gutter >= 0) {
            document.documentElement.style.setProperty('--sidebar-gutter', gutter + 'px');
            try { localStorage.setItem('sidebarGutter', String(gutter)); } catch (e) { }
        }
    }

    measure();
    settle();
    window.addEventListener('resize', measure);
})();

// Long table values are cut to one line with an ellipsis (style.css,
// the .table-fixed desktop rules). On hover, whichever element under
// the pointer is actually truncated gets a native title tooltip with
// its full text, so nothing is lost. Delegated from document so it
// covers rows added later (soft nav, JS-rendered modal tables), and
// re-checked every hover so it tracks resizes. Elements that already
// carry their own title are left untouched.
(function initTruncatedCellTitles() {
    document.addEventListener('mouseover', (e) => {
        const cell = e.target.closest && e.target.closest('table.table-fixed td, table.table-fixed th');
        if (!cell) return;
        for (let el = e.target; el && el !== cell.parentElement; el = el.parentElement) {
            const own = el.hasAttribute('title') && !el.dataset.autoTitle;
            if (own) return;
            const truncated = el.scrollWidth > el.clientWidth + 1;
            if (truncated) {
                el.title = el.textContent.replace(/\s+/g, ' ').trim();
                el.dataset.autoTitle = '1';
                return;
            }
            if (el.dataset.autoTitle) {
                el.removeAttribute('title');
                delete el.dataset.autoTitle;
            }
        }
    });
})();


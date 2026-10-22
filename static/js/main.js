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
        const chrome = parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth)
            + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
        const gutter = (sidebar.getBoundingClientRect().width - chrome
            - brand.getBoundingClientRect().width) / 2;
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
        link.addEventListener('click', () => {
            // Clicking the tab that's already open shouldn't close the
            // menu — see isSamePageNoOpLink()'s own comment for why
            // this needs to check that at all instead of always
            // closing on any sidebar link click.
            if (isSamePageNoOpLink(link)) return;
            closeMenu();
        });
    });

    window.addEventListener('resize', () => {
        if (window.innerWidth > 760) closeMenu();
    });
}

function initSidebarNavTooltips() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    // Collapsed nav-link labels are only clipped out of view, not
    // display: none (see style.css), so a screen reader still gets the
    // text, but there's no visible label to read at a glance. Mirror
    // each one into a native title tooltip as a fallback for anyone who
    // mouses over the rail without lingering long enough to trigger the
    // hover-expand.
    sidebar.querySelectorAll('.nav-link').forEach((link) => {
        // Wrap the raw label text node in its own span so style.css can
        // slide/fade it on expand without touching the icon or badge.
        link.childNodes.forEach((node) => {
            if (node.nodeType !== Node.TEXT_NODE || !node.textContent.trim()) return;
            const span = document.createElement('span');
            span.className = 'nav-text';
            span.textContent = node.textContent.trim();
            link.replaceChild(span, node);
        });

        const clone = link.cloneNode(true);
        const badge = clone.querySelector('.nav-badge');
        if (badge) badge.remove();
        link.title = clone.textContent.trim();
    });

    // Stagger index for the expand animation (see style.css's
    // --reveal-delay), top to bottom, capped so long menus don't lag.
    sidebar.querySelectorAll('.nav-label, .nav-link:not(.nav-sub) .nav-text').forEach((el, i) => {
        el.style.setProperty('--i', String(Math.min(i, 14)));
    });
}

const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

function isEditableTarget(el) {
    return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
}

// Wires up the brand header's pin button (and Ctrl/⌘+B) — the only way
// to hold the sidebar expanded. Toggles html.sidebar-pinned, which every
// collapsed-rail rule in style.css is scoped with
// html:not(.sidebar-pinned), and persists the choice to localStorage.
// The inline <head> script in base.html applies that stored choice (or
// a touch/coarse-pointer default) before first paint.
function initSidebarPinToggle() {
    const btn = document.getElementById('sidebarPinToggle');
    if (!btn) return;

    const root = document.documentElement;
    const shortcut = IS_MAC ? '⌘B' : 'Ctrl+B';

    function sync() {
        const pinned = root.classList.contains('sidebar-pinned');
        const label = pinned ? 'Collapse sidebar' : 'Keep sidebar open';
        btn.setAttribute('aria-pressed', pinned ? 'true' : 'false');
        btn.setAttribute('aria-label', label);
        btn.title = label + ' (' + shortcut + ')';
    }

    function toggle() {
        const pinned = !root.classList.contains('sidebar-pinned');
        root.classList.toggle('sidebar-pinned', pinned);
        try {
            localStorage.setItem('sidebarPinned', String(pinned));
        } catch (e) { }
        sync();
        requestAnimationFrame(syncSidebarIndicator);
    }

    sync();
    btn.addEventListener('click', toggle);

    document.addEventListener('keydown', (e) => {
        if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
        if (e.key.toLowerCase() !== 'b' || isEditableTarget(e.target)) return;
        if (window.innerWidth <= 760) return;
        e.preventDefault();
        toggle();
    });
}

// Expands the collapsed rail only once the pointer has rested on it for
// a beat, so sweeping the cursor past the left edge doesn't fling the
// whole menu open, and keeps it open through a brief slip off the edge.
// Mouse/pen only — touch defaults to pinned (see base.html).
function initSidebarHoverIntent() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    const OPEN_DELAY = 140;
    const CLOSE_DELAY = 200;
    let timer = null;

    function schedule(peek, delay) {
        clearTimeout(timer);
        timer = setTimeout(() => sidebar.classList.toggle('is-peeking', peek), delay);
    }

    sidebar.addEventListener('pointerenter', (e) => {
        if (e.pointerType === 'touch') return;
        schedule(true, sidebar.classList.contains('is-peeking') ? 0 : OPEN_DELAY);
    });

    sidebar.addEventListener('pointerleave', (e) => {
        if (e.pointerType === 'touch') return;
        schedule(false, CLOSE_DELAY);
    });
}

// Positions the shared sliding highlight (#navIndicator) on the active
// nav link. Called on load, after every soft nav (patchSidebarNav()),
// when the search filter changes which rows are visible, and on resize.
function syncSidebarIndicator() {
    const sidebar = document.getElementById('sidebar');
    const indicator = document.getElementById('navIndicator');
    if (!sidebar || !indicator) return;

    // Groups only really fold away while the sidebar is pinned open (or on
    // the phone drawer) — on the icon rail they always show (style.css).
    const foldable = document.documentElement.classList.contains('sidebar-pinned')
        || !window.matchMedia('(min-width: 761px)').matches;
    const link = Array.from(sidebar.querySelectorAll('.nav-link.active'))
        .find((l) => l.getClientRects().length > 0
            && (sidebar.classList.contains('is-filtering') || !foldable || !l.closest('.nav-group.is-collapsed')));
    if (!link) {
        indicator.classList.add('is-hidden');
        return;
    }

    const top = link.getBoundingClientRect().top - sidebar.getBoundingClientRect().top
        + sidebar.scrollTop - sidebar.clientTop;
    indicator.style.setProperty('--y', top + 'px');
    indicator.style.setProperty('--h', link.offsetHeight + 'px');
    indicator.classList.remove('is-hidden');
    sidebar.classList.add('has-indicator');

    if (!indicator.classList.contains('is-ready')) {
        // First placement lands without animating; every later move glides.
        requestAnimationFrame(() => requestAnimationFrame(() => indicator.classList.add('is-ready')));
    }
}

function initSidebarIndicator() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    syncSidebarIndicator();

    const active = sidebar.querySelector('.nav-link.active:not(.nav-sub)');
    if (active) {
        const s = sidebar.getBoundingClientRect();
        const r = active.getBoundingClientRect();
        const footer = sidebar.querySelector('.sidebar-footer');
        const bottomLimit = s.bottom - (footer ? footer.offsetHeight : 0);
        if (r.top < s.top || r.bottom > bottomLimit) {
            sidebar.scrollTop += r.top - s.top - (s.height - r.height) / 3;
        }
    }

    // Anything above the active row changing height (the header easing
    // between layouts, a badge appearing, fonts settling) shifts it.
    let raf = 0;
    const resync = () => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(syncSidebarIndicator);
    };
    window.addEventListener('resize', resync);
    if (typeof ResizeObserver === 'function') {
        const ro = new ResizeObserver(resync);
        sidebar.querySelectorAll('.brand, .nav-search, .nav-group').forEach((el) => ro.observe(el));
    }
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(syncSidebarIndicator);
    }
}

// "Jump to…" filter at the top of the menu. Matches every word typed
// against a link's label and its section name, highlights the hit, and
// hides sections left empty. Enter opens the top result, ↓ moves into
// the list, Esc clears then leaves. Ctrl/⌘+K focuses it from anywhere,
// which also expands the collapsed rail (it's :focus-visible).
function initSidebarSearch() {
    const sidebar = document.getElementById('sidebar');
    const input = document.getElementById('sidebarSearch');
    if (!sidebar || !input) return;

    const field = input.closest('.nav-search-field');
    const empty = document.getElementById('sidebarSearchEmpty');
    const emptyQuery = document.getElementById('sidebarSearchEmptyQuery');
    const kbd = document.getElementById('sidebarSearchKbd');
    if (kbd) kbd.textContent = IS_MAC ? '⌘K' : 'Ctrl K';

    const groups = Array.from(sidebar.querySelectorAll('.nav-group'));
    const entries = Array.from(sidebar.querySelectorAll('.nav-group .nav-link')).map((link) => {
        const textEl = link.querySelector('.nav-text');
        const group = link.closest('.nav-group');
        const groupLabel = group.querySelector('.nav-label');
        const label = textEl ? textEl.textContent : '';
        return {
            link,
            textEl,
            label,
            haystack: (label + ' ' + (groupLabel ? groupLabel.textContent : '')).toLowerCase(),
        };
    });

    function highlight(entry, query) {
        if (!entry.textEl) return;
        const i = query ? entry.label.toLowerCase().indexOf(query) : -1;
        if (i < 0) {
            entry.textEl.textContent = entry.label;
            return;
        }
        const mark = document.createElement('mark');
        mark.textContent = entry.label.slice(i, i + query.length);
        entry.textEl.replaceChildren(entry.label.slice(0, i), mark, entry.label.slice(i + query.length));
    }

    function visibleLinks() {
        return entries.map((e) => e.link).filter((l) => l.getClientRects().length > 0);
    }

    function apply() {
        const raw = input.value.trim();
        const query = raw.toLowerCase();
        const tokens = query.split(/\s+/).filter(Boolean);
        let first = null;

        entries.forEach((entry) => {
            // Group parents give way to their individual pages while searching.
            const match = tokens.every((t) => entry.haystack.includes(t))
                && !(tokens.length && entry.link.classList.contains('has-subs'));
            entry.link.classList.toggle('is-filtered-out', !match);
            entry.link.classList.remove('is-search-target');
            highlight(entry, query);
            if (match && !first) first = entry.link;
        });

        groups.forEach((group) => {
            group.classList.toggle('is-filtered-out', !group.querySelector('.nav-link:not(.is-filtered-out)'));
        });

        if (first && query) first.classList.add('is-search-target');
        if (empty) empty.hidden = !!first;
        if (emptyQuery) emptyQuery.textContent = raw;
        if (field) field.classList.toggle('has-value', !!input.value);
        sidebar.classList.toggle('is-filtering', tokens.length > 0);
        syncSidebarIndicator();
    }

    function reset() {
        if (!input.value) return;
        input.value = '';
        apply();
    }

    input.addEventListener('input', apply);

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const target = sidebar.querySelector('.nav-link.is-search-target') || visibleLinks()[0];
            if (!target || !input.value.trim()) return;
            target.click();
            reset();
            input.blur();
        } else if (e.key === 'ArrowDown') {
            const links = visibleLinks();
            if (links.length) {
                e.preventDefault();
                links[0].focus();
            }
        } else if (e.key === 'Escape') {
            e.stopPropagation();
            if (input.value) reset();
            else input.blur();
        }
    });

    // Never leave the rail showing a filtered menu once the user is done
    // with it: clear after picking a result or once focus leaves the menu.
    sidebar.addEventListener('click', (e) => {
        if (e.target.closest('.nav-link')) setTimeout(reset, 0);
    });
    sidebar.addEventListener('focusout', (e) => {
        if (!e.relatedTarget || !sidebar.contains(e.relatedTarget)) reset();
    });

    document.addEventListener('keydown', (e) => {
        if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.key.toLowerCase() !== 'k') return;
        e.preventDefault();
        if (window.innerWidth <= 760 && !sidebar.classList.contains('open')) {
            const menuBtn = document.getElementById('menuToggle');
            if (menuBtn) menuBtn.click();
        }
        input.focus();
        input.select();
    });
}

// Arrow keys move between visible menu links (Home/End jump to the
// ends; ↑ from the first link returns to the search field), the same as
// a native list, instead of tabbing through two dozen links one by one.
function initSidebarKeyboardNav() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    const search = document.getElementById('sidebarSearch');

    sidebar.addEventListener('keydown', (e) => {
        const current = e.target.closest && e.target.closest('.nav-link');
        if (!current || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;

        const links = Array.from(sidebar.querySelectorAll('.nav-link'))
            .filter((l) => l.getClientRects().length > 0);
        const i = links.indexOf(current);
        let next = null;
        if (e.key === 'ArrowDown') next = links[i + 1];
        else if (e.key === 'ArrowUp') next = i === 0 ? search : links[i - 1];
        else if (e.key === 'Home') next = links[0];
        else if (e.key === 'End') next = links[links.length - 1];
        if (!next) return;
        e.preventDefault();
        next.focus();
        next.scrollIntoView({ block: 'nearest' });
    });
}

// Gives the sticky footer a lift shadow only while menu items are
// scrolled underneath it.
function initSidebarScrollShadow() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    function update() {
        const below = sidebar.scrollHeight - sidebar.clientHeight - sidebar.scrollTop > 1;
        sidebar.classList.toggle('can-scroll-down', below);
    }

    update();
    sidebar.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    if (typeof ResizeObserver === 'function') {
        const ro = new ResizeObserver(update);
        sidebar.querySelectorAll('.nav-group').forEach((g) => ro.observe(g));
    }
}

// Sidebar groups (Overview, Catalog & Supply, …) as drop-downs: each
// .nav-label becomes a toggle and its links are wrapped so they can ease
// open/closed (see "Collapsible nav groups" in style.css). The group
// holding the current page opens on load and on every soft nav; other
// groups default to closed (the first, Overview, to open) and remember
// the user's choice in localStorage ('navGroups').
function initNavAccordion() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    let saved = {};
    try {
        saved = JSON.parse(localStorage.getItem('navGroups') || '{}') || {};
    } catch (e) { }
    const persist = () => {
        try { localStorage.setItem('navGroups', JSON.stringify(saved)); } catch (e) { }
    };
    const exclusive = () => {
        try { return localStorage.getItem('navExclusive') === 'true'; } catch (e) { return false; }
    };

    const groups = [];
    sidebar.querySelectorAll('.nav-group').forEach((group, i) => {
        const label = group.querySelector(':scope > .nav-label');
        if (!label) return;

        const items = document.createElement('div');
        items.className = 'nav-items';
        items.id = 'navItems' + i;
        const inner = document.createElement('div');
        inner.className = 'nav-items-inner';
        while (label.nextSibling) inner.appendChild(label.nextSibling);
        items.appendChild(inner);
        group.appendChild(items);

        label.setAttribute('role', 'button');
        label.setAttribute('tabindex', '0');
        label.setAttribute('aria-controls', items.id);

        const key = label.textContent.trim();
        const entry = { group, inner, label, key };
        groups.push(entry);

        entry.hasActive = () => !!inner.querySelector('.nav-link.active');
        entry.set = (open) => {
            group.classList.toggle('is-collapsed', !open);
            label.setAttribute('aria-expanded', String(open));
            syncSidebarIndicator();
        };
        entry.badges = () => {
            let n = 0;
            inner.querySelectorAll('.nav-link:not(.nav-sub) .nav-badge').forEach((b) => {
                n += parseInt(b.textContent, 10) || 0;
            });
            if (n > 0) label.setAttribute('data-badge', n > 99 ? '99+' : String(n));
            else label.removeAttribute('data-badge');
            // Page count, shown faintly on a closed group (grouped pages
            // count once, via their parent link).
            label.setAttribute('data-count', String(inner.querySelectorAll('.nav-link:not(.nav-sub)').length));
        };

        entry.set(entry.hasActive() || (key in saved ? !!saved[key] : i === 0));
        entry.badges();

        const toggle = () => {
            const open = group.classList.contains('is-collapsed');
            saved[key] = open;
            // "One group open at a time" (Settings): opening one closes the rest.
            if (open && exclusive()) {
                groups.forEach((o) => {
                    if (o !== entry) {
                        saved[o.key] = false;
                        o.set(false);
                    }
                });
            }
            persist();
            entry.set(open);
        };
        label.addEventListener('click', toggle);
        label.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggle();
                return;
            }
            // Arrow keys / Home / End move between group headers.
            const heads = groups.map((g) => g.label).filter((l) => l.getClientRects().length > 0);
            const at = heads.indexOf(label);
            let to = null;
            if (e.key === 'ArrowDown') to = heads[at + 1];
            else if (e.key === 'ArrowUp') to = heads[at - 1];
            else if (e.key === 'Home') to = heads[0];
            else if (e.key === 'End') to = heads[heads.length - 1];
            if (to) {
                e.preventDefault();
                to.focus();
            }
        });
    });

    // Driven by the Settings dialog (initSettings).
    const setAll = (open) => {
        groups.forEach((g) => {
            // The current page's group stays open so the page never "vanishes".
            const keep = open || g.hasActive();
            saved[g.key] = keep;
            g.set(keep);
        });
        persist();
    };
    document.addEventListener('nav:expand-all', () => setAll(true));
    document.addEventListener('nav:collapse-all', () => setAll(false));
    document.addEventListener('nav:exclusive', () => {
        if (!exclusive()) return;
        // Turning it on tidies up: keep only the current page's group open.
        groups.forEach((g) => {
            if (!g.hasActive()) {
                saved[g.key] = false;
                g.set(false);
            }
        });
        persist();
    });

    // Soft nav moves .active between links (and badge counts can change):
    // open the group of the new page, and refresh the counts.
    let queued = false;
    let activeMoved = false;
    new MutationObserver((mutations) => {
        if (mutations.some((m) => m.type === 'attributes' && m.target.classList.contains('nav-link'))) activeMoved = true;
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => {
            queued = false;
            groups.forEach((g) => {
                if (activeMoved && g.group.classList.contains('is-collapsed') && g.hasActive()) g.set(true);
                else if (activeMoved && exclusive() && !g.hasActive() && !g.group.classList.contains('is-collapsed')) g.set(false);
                g.badges();
            });
            activeMoved = false;
        });
    }).observe(sidebar, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class'] });
}

// Settings dialog (sidebar footer → Settings): light/dark mode plus the
// interface style (html[data-style], defined in static/css/themes.css).
// Both are per-browser, stored in localStorage; base.html's head script
// re-applies them before first paint.
function initSettings() {
    const overlay = document.getElementById('settingsOverlay');
    const opener = document.getElementById('settingsOpen');
    if (!overlay || !opener) return;

    const root = document.documentElement;
    const STYLES = ['classic', 'neobrutalism', 'minimalism', 'glassmorphism',
        'skeuomorphism', 'neumorphism', 'claymorphism', 'cyberpunk', 'terminal',
        'paper', 'vaporwave', 'pixel', 'aero', 'aurora', 'bauhaus'];
    const store = (key, value) => {
        try { localStorage.setItem(key, value); } catch (e) { }
    };
    const modeBtns = overlay.querySelectorAll('[data-mode]');
    const styleBtns = overlay.querySelectorAll('[data-style-option]');
    let lastFocus = null;

    function sync() {
        const mode = root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        const style = root.getAttribute('data-style') || 'classic';
        modeBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
        styleBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.styleOption === style)));
    }

    // ---- Tabs (General / Appearance / Backup) ----
    const tabs = Array.from(overlay.querySelectorAll('.settings-tab'));
    const panels = Array.from(overlay.querySelectorAll('.settings-panel'));
    function showTab(name, focus) {
        if (!tabs.some((t) => t.dataset.tab === name)) name = 'general';
        tabs.forEach((t) => {
            const on = t.dataset.tab === name;
            t.setAttribute('aria-selected', String(on));
            t.tabIndex = on ? 0 : -1;
            if (on && focus) t.focus();
        });
        panels.forEach((p) => { p.hidden = p.dataset.panel !== name; });
        const body = overlay.querySelector('.settings-body');
        if (body) body.scrollTop = 0;
        store('settingsTab', name);
    }
    tabs.forEach((t, i) => {
        t.addEventListener('click', () => showTab(t.dataset.tab));
        t.addEventListener('keydown', (e) => {
            const horizontal = window.matchMedia('(max-width: 640px)').matches;
            const next = horizontal ? 'ArrowRight' : 'ArrowDown';
            const prev = horizontal ? 'ArrowLeft' : 'ArrowUp';
            let to = null;
            if (e.key === next) to = tabs[(i + 1) % tabs.length];
            else if (e.key === prev) to = tabs[(i - 1 + tabs.length) % tabs.length];
            else if (e.key === 'Home') to = tabs[0];
            else if (e.key === 'End') to = tabs[tabs.length - 1];
            if (to) { e.preventDefault(); showTab(to.dataset.tab, true); }
        });
    });

    // Re-measure anything sized from the old style's fonts/padding (the
    // sidebar's active-link indicator, charts) once the new style is live.
    function relayout() {
        requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    }

    function setStyle(style) {
        if (!STYLES.includes(style)) return;
        if ((root.getAttribute('data-style') || 'classic') === style) { sync(); return; }
        store('uiStyle', style);
        const apply = () => {
            root.setAttribute('data-style', style);
            sync();
        };
        // One snapshot cross-fade on the compositor instead of every
        // element restyling in view at once (these styles swap fonts,
        // borders, radii and shadows, which CSS transitions can't tween).
        if (typeof document.startViewTransition === 'function' && !PREFERS_REDUCED_MOTION.matches) {
            root.classList.add('vt-style');
            const t = document.startViewTransition(apply);
            t.finished.finally(() => { root.classList.remove('vt-style'); relayout(); });
        } else {
            apply();
            relayout();
        }
    }

    function setMode(mode) {
        if (root.getAttribute('data-theme') === mode) { sync(); return; }
        store('theme', mode);
        if (typeof document.startViewTransition === 'function' && !PREFERS_REDUCED_MOTION.matches) {
            root.classList.add('vt-style');
            const t = document.startViewTransition(() => { root.setAttribute('data-theme', mode); sync(); });
            t.finished.finally(() => root.classList.remove('vt-style'));
        } else {
            root.setAttribute('data-theme', mode);
            sync();
        }
    }

    function open(tab) {
        lastFocus = document.activeElement;
        sync();
        let want = typeof tab === 'string' ? tab : null;
        if (!want) {
            // A due backup (the dot on Settings) lands on the Backup tab.
            if (opener.hasAttribute('data-alert') && tabs.some((t) => t.dataset.tab === 'backup')) want = 'backup';
            else { try { want = localStorage.getItem('settingsTab'); } catch (e) { } }
        }
        showTab(want || 'general');
        overlay.hidden = false;
        const selected = tabs.find((t) => t.getAttribute('aria-selected') === 'true');
        (selected || overlay.querySelector('.settings-close')).focus();
    }

    function close() {
        overlay.hidden = true;
        if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
    }

    opener.addEventListener('click', () => open());
    // The account link may be handled by soft nav, which would leave the
    // dialog open over the new page.
    overlay.querySelectorAll('a').forEach((a) => a.addEventListener('click', () => { overlay.hidden = true; }));
    overlay.querySelector('#settingsClose').addEventListener('click', close);
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(); });
    modeBtns.forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
    styleBtns.forEach((b) => b.addEventListener('click', () => setStyle(b.dataset.styleOption)));
    overlay.querySelector('#settingsReset').addEventListener('click', () => setStyle('classic'));

    // Sidebar group behaviour (handled by initNavAccordion via events).
    const exclusiveBtn = overlay.querySelector('#navExclusive');
    const syncExclusive = () => {
        let on = false;
        try { on = localStorage.getItem('navExclusive') === 'true'; } catch (e) { }
        exclusiveBtn.setAttribute('aria-checked', String(on));
    };
    syncExclusive();
    exclusiveBtn.addEventListener('click', () => {
        const on = exclusiveBtn.getAttribute('aria-checked') !== 'true';
        store('navExclusive', String(on));
        syncExclusive();
        document.dispatchEvent(new Event('nav:exclusive'));
    });
    // ---- Backup + restore (admins only) --------------------------------
    // Shared status helper: text with a busy/ok/bad mark that slides in
    // each time it changes, plus an optional progress bar (a number is a
    // percentage, null is "working, unknown length", false hides it).
    const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    const makeUi = (textEl, barEl, warnEl) => ({
        say(text, tone, busy) {
            if (textEl.textContent !== text || textEl.dataset.tone !== (tone || '')) {
                textEl.textContent = text;
                textEl.dataset.tone = tone || '';
                textEl.classList.remove('is-swap');
                void textEl.offsetWidth;            // restart the animation
                textEl.classList.add('is-swap');
            }
            textEl.classList.toggle('is-ok', tone === 'ok');
            textEl.classList.toggle('is-bad', tone === 'bad');
            textEl.classList.toggle('is-busy', !!busy);
        },
        warn(text) {
            if (!warnEl) return;
            warnEl.hidden = !text;
            if (!text) return;
            if (warnEl.textContent !== text) {
                warnEl.textContent = text;
                warnEl.classList.remove('is-swap');
                void warnEl.offsetWidth;
                warnEl.classList.add('is-swap');
            }
            warnEl.classList.add('is-bad');
        },
        bar(pct) {
            if (!barEl) return;
            barEl.hidden = pct === false;
            barEl.classList.toggle('is-indeterminate', pct === null);
            barEl.firstElementChild.style.width = typeof pct === 'number' ? pct + '%' : '';
        },
    });
    const getJson = (url) => fetch(url, { headers: { Accept: 'application/json' } }).then((r) => {
        if (r.redirected || r.status === 401 || r.status === 403) throw new Error('signed-out');
        return r.json();
    });

    // Backup: email a full backup, then watch for the result.
    const backupBtn = overlay.querySelector('#backupNow');
    if (backupBtn) {
        const ui = makeUi(overlay.querySelector('#backupStatus'), overlay.querySelector('#backupBar'),
            overlay.querySelector('#backupWarn'));
        const toEl = overlay.querySelector('#backupTo');
        let baseline = null;     // latest backup log id before we started
        let timer = 0;

        // ---- schedule: how often, automatic on/off, what time ----
        const daysEl = overlay.querySelector('#backupDays');
        const hourEl = overlay.querySelector('#backupHour');
        const autoEl = overlay.querySelector('#backupAuto');
        const noteEl = overlay.querySelector('#scheduleNote');
        const schedUi = makeUi(overlay.querySelector('#scheduleStatus'), null, null);
        let syncing = false;
        let schedTimer = 0;

        const explain = (sch, runSoon, nextRun) => {
            const every = sch.days === 7 ? 'week' : (sch.days === 30 ? 'month' : sch.days + ' days');
            if (sch.auto) {
                noteEl.textContent = runSoon
                    ? 'A backup is due, so the next automatic one starts within a few minutes.'
                    : 'Next automatic backup: ' + nextRun + '. A manual backup restarts the count.';
            } else {
                noteEl.textContent = 'You will get a reminder here once a backup is more than 1 ' + every
                    + ' old' + (sch.days === 7 ? '' : ' (' + sch.days + ' days)')
                    + '. Weekly suits a shop that records sales every day; a month can lose a lot if something goes wrong.';
            }
        };
        function syncSchedule(d) {
            if (!d || !d.schedule) return;
            syncing = true;
            daysEl.value = String(d.schedule.days);
            hourEl.value = String(d.schedule.hour);
            autoEl.setAttribute('aria-checked', String(d.schedule.auto));
            hourEl.disabled = !d.schedule.auto;
            const wrap = hourEl.closest('.cs-select');
            if (wrap) wrap.classList.toggle('cs-disabled', !d.schedule.auto);
            explain(d.schedule, d.schedule.run_soon, d.schedule.next_run);
            syncing = false;
        }
        const saveSchedule = () => {
            if (syncing) return;
            const sch = {
                days: parseInt(daysEl.value, 10),
                hour: parseInt(hourEl.value, 10),
                auto: autoEl.getAttribute('aria-checked') === 'true',
            };
            schedUi.say('Saving\u2026', '', true);
            fetch(backupBtn.dataset.scheduleUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken, Accept: 'application/json' },
                body: JSON.stringify(sch),
            }).then((r) => r.json().then((b) => ({ ok: r.ok, b })))
                .then(({ ok, b }) => {
                    if (!ok) {
                        schedUi.say(b.error || 'Could not save the schedule.', 'bad');
                        refresh();                       // put the controls back to what is saved
                        return;
                    }
                    hourEl.disabled = !sch.auto;
                    const wrap = hourEl.closest('.cs-select');
                    if (wrap) wrap.classList.toggle('cs-disabled', !sch.auto);
                    explain(sch, b.run_soon, b.next_run);
                    schedUi.say('Saved', 'ok');
                    window.clearTimeout(schedTimer);
                    schedTimer = window.setTimeout(() => schedUi.say('', ''), 2500);
                })
                .catch(() => schedUi.say('Could not reach the server.', 'bad'));
        };
        daysEl.addEventListener('change', saveSchedule);
        hourEl.addEventListener('change', saveSchedule);
        autoEl.addEventListener('click', () => {
            const on = autoEl.getAttribute('aria-checked') !== 'true';
            autoEl.setAttribute('aria-checked', String(on));
            autoEl.classList.remove('is-pulse');
            hourEl.disabled = !on;
            saveSchedule();
        });

        // ---- reminder banner (outside the dialog) + the dot on Settings ----
        const banner = document.getElementById('backupReminder');
        function clearReminder() {
            opener.removeAttribute('data-alert');
            opener.removeAttribute('title');
            const el = document.getElementById('backupReminder');
            if (!el) return;
            el.classList.add('is-leaving');
            window.setTimeout(() => el.remove(), 450);
        }
        if (banner) {
            banner.addEventListener('click', (e) => {
                const btn = e.target.closest('[data-reminder]');
                if (!btn) return;
                const what = btn.dataset.reminder;
                if (what === 'snooze') {
                    fetch(backupBtn.dataset.snoozeUrl, {
                        method: 'POST', headers: { 'X-CSRFToken': csrfToken, Accept: 'application/json' },
                    }).catch(() => { });
                    clearReminder();
                } else if (what === 'now') {
                    open();
                    window.setTimeout(() => { if (!backupBtn.disabled) backupBtn.click(); }, 350);
                } else if (what === 'auto') {
                    open();
                    window.setTimeout(() => {
                        autoEl.scrollIntoView({ block: 'center', behavior: 'smooth' });
                        autoEl.classList.add('is-pulse');
                        autoEl.focus({ preventScroll: true });
                    }, 300);
                }
            });
        }

        const idle = (d) => {
            ui.bar(false);
            backupBtn.disabled = false;
            if (d.last_ok) ui.say('Last backup: ' + d.last_ok.at + ' (' + d.last_ok.details + ').', 'ok');
            else ui.say('No backup has been sent yet.');
            ui.warn(d.last_failed
                ? (d.last_ok ? 'A later attempt failed' : 'The last attempt failed') + ' (' + d.last_failed.at + '): ' + d.last_failed.details
                : '');
        };
        const describe = (d) => {
            syncSchedule(d);
            if (!d.configured) {
                backupBtn.disabled = true;
                ui.bar(false);
                ui.say('Email is not set up yet. Add the mail settings to .env first.', 'bad');
                return;
            }
            if (d.recipient && toEl) toEl.textContent = 'to ' + d.recipient;
            if (d.running) {
                backupBtn.disabled = true;
                ui.say((d.phase || 'Working') + '\u2026', '', true);
                ui.bar(null);
                watch();
            } else {
                idle(d);
            }
        };
        const refresh = () => getJson(backupBtn.dataset.statusUrl).then((d) => {
            baseline = d.latest_id;
            describe(d);
        }).catch(() => { });

        // Poll until the server has logged a new result.
        const watch = () => {
            if (timer) return;
            const started = Date.now();
            let misses = 0;
            timer = window.setInterval(() => {
                getJson(backupBtn.dataset.statusUrl).then((d) => {
                    misses = 0;
                    if (d.running) {
                        ui.say((d.phase || 'Working') + '\u2026', '', true);
                    } else if (d.latest_id !== baseline) {
                        window.clearInterval(timer);
                        timer = 0;
                        baseline = d.latest_id;
                        const newest = d.last_failed && (!d.last_ok || d.last_failed.id > d.last_ok.id);
                        if (newest) {
                            backupBtn.disabled = false;
                            ui.bar(false);
                            ui.say('Backup failed: ' + d.last_failed.details, 'bad');
                            ui.warn('');
                        } else {
                            idle(d);
                            ui.say('Backup sent ' + d.last_ok.at + ' (' + d.last_ok.details + ').', 'ok');
                            clearReminder();
                            syncSchedule(d);
                        }
                    }
                }).catch(() => {
                    if (++misses >= 5) {
                        window.clearInterval(timer);
                        timer = 0;
                        backupBtn.disabled = false;
                        ui.bar(false);
                        ui.say('Lost contact with the server. Check your inbox in a few minutes.', 'bad');
                    }
                });
                if (Date.now() - started > 300000) {
                    window.clearInterval(timer);
                    timer = 0;
                    backupBtn.disabled = false;
                    ui.bar(false);
                    ui.say('Still working. Check your inbox in a few minutes.');
                }
            }, 1500);
        };

        backupBtn.addEventListener('click', () => {
            backupBtn.disabled = true;
            ui.warn('');
            ui.say('Starting the backup\u2026', '', true);
            ui.bar(null);
            fetch(backupBtn.dataset.emailUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrfToken, Accept: 'application/json' },
            }).then((r) => r.json().then((body) => ({ ok: r.ok, body })))
                .then(({ ok, body }) => {
                    if (!ok) {
                        ui.bar(false);
                        ui.say(body.error || 'Could not start the backup.', 'bad');
                        backupBtn.disabled = false;
                        return;
                    }
                    watch();
                })
                .catch(() => {
                    ui.bar(false);
                    ui.say('Could not reach the server.', 'bad');
                    backupBtn.disabled = false;
                });
        });

        opener.addEventListener('click', refresh);
        refresh();
    }

    // Restore: confirm with password + phrase, upload in 4 MB pieces, then
    // follow the server's progress until it is finished.
    const restoreBtn = overlay.querySelector('#restoreNow');
    if (restoreBtn) {
        const ui = makeUi(overlay.querySelector('#restoreStatus'), overlay.querySelector('#restoreBar'));
        const fileEl = overlay.querySelector('#restoreFile');
        const pwEl = overlay.querySelector('#restorePassword');
        const phraseEl = overlay.querySelector('#restorePhrase');
        const CHUNK = 4 * 1024 * 1024;
        const fail = (text) => {
            ui.bar(false);
            ui.say(text, 'bad');
            restoreBtn.disabled = false;
        };
        const finished = (text) => {
            ui.bar(100);
            ui.say(text, 'ok');
            // Restored accounts/passwords may be identical, which keeps this session
            // valid, so sign out for real (POST /logout) instead of only redirecting.
            const signout = document.createElement('div');
            signout.className = 'signout-overlay';
            signout.setAttribute('role', 'alertdialog');
            signout.setAttribute('aria-live', 'assertive');
            signout.setAttribute('aria-label', 'Signing you out');
            signout.innerHTML = '<div class="signout-dialog"><div class="signout-spinner" aria-hidden="true"></div>'
                + '<strong>Signing you out…</strong>'
                + '<span>The restore is complete. Please sign in again.</span></div>';
            document.body.appendChild(signout);
            const minShown = new Promise((resolve) => window.setTimeout(resolve, 1800));
            const loggedOut = fetch('/logout', { method: 'POST', headers: { 'X-CSRFToken': csrfToken }, credentials: 'same-origin' })
                .catch(() => {});
            Promise.all([minShown, loggedOut]).then(() => { window.location.href = '/login'; });
        };

        const follow = () => {
            ui.bar(null);
            const started = Date.now();
            let misses = 0;
            const timer = window.setInterval(() => {
                getJson(restoreBtn.dataset.statusUrl).then((d) => {
                    misses = 0;
                    if (d.running) {
                        ui.say((d.phase || 'Working') + '\u2026', '', true);
                    } else if (d.done) {
                        window.clearInterval(timer);
                        if (d.error) fail(d.error);
                        else finished('Restore complete. ' + d.summary + ' Signing you out\u2026');
                    }
                }).catch((err) => {
                    // Signed out = the restored data has no session for us: it worked.
                    if (err && err.message === 'signed-out') {
                        window.clearInterval(timer);
                        finished('Restore complete. Signing you out\u2026');
                    } else if (++misses >= 6) {
                        window.clearInterval(timer);
                        fail('Lost contact with the server. Wait a minute, then refresh the page.');
                    }
                });
                if (Date.now() - started > 900000) window.clearInterval(timer);
            }, 2000);
        };

        const sendChunks = (file) => {
            const piece = (offset) => {
                const end = Math.min(offset + CHUNK, file.size);
                ui.say('Uploading the backup\u2026 ' + Math.round((end / file.size) * 100) + '%', '', true);
                ui.bar(Math.round((end / file.size) * 100));
                return fetch(restoreBtn.dataset.chunkUrl + '?offset=' + offset + '&total=' + file.size, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/octet-stream', 'X-CSRFToken': csrfToken, Accept: 'application/json' },
                    body: file.slice(offset, end),
                }).then((r) => r.json().then((b) => ({ ok: r.ok, b })))
                    .then(({ ok, b }) => {
                        if (!ok) return fail(b.error || 'The upload was refused.');
                        if (end < file.size) return piece(end);
                        ui.say('Restoring\u2026', '', true);
                        follow();
                    });
            };
            return piece(0);
        };

        restoreBtn.addEventListener('click', () => {
            const file = fileEl.files && fileEl.files[0];
            if (!file) return fail('Choose the backup file first.');
            if (!pwEl.value) return fail('Enter your password.');
            if (phraseEl.value !== 'RESTORE') return fail('Type RESTORE exactly to confirm.');
            restoreBtn.disabled = true;
            ui.say('Checking your password\u2026', '', true);
            ui.bar(null);
            fetch(restoreBtn.dataset.verifyUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken, Accept: 'application/json' },
                body: JSON.stringify({ password: pwEl.value, phrase: phraseEl.value }),
            }).then((r) => r.json().then((b) => ({ ok: r.ok, b })))
                .then(({ ok, b }) => {
                    if (!ok) return fail(b.error || 'Could not confirm.');
                    pwEl.value = '';
                    return sendChunks(file);
                })
                .catch(() => fail('Could not reach the server.'));
        });
    }

    overlay.querySelector('#navExpandAll').addEventListener('click', () => document.dispatchEvent(new Event('nav:expand-all')));
    overlay.querySelector('#navCollapseAll').addEventListener('click', () => document.dispatchEvent(new Event('nav:collapse-all')));

    // Esc closes; Tab stays inside the dialog while it is open.
    document.addEventListener('keydown', (e) => {
        if (overlay.hidden) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            close();
        } else if (e.key === 'Tab') {
            const items = Array.from(overlay.querySelectorAll('button')).filter((b) => b.offsetParent !== null);
            if (!items.length) return;
            const first = items[0];
            const last = items[items.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    });
}

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
        el.style.width = '0%';
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                el.style.width = pct + '%';
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
const LOGIN_SPLASH_HOLD_MS = 650;
const LOGIN_SPLASH_EXIT_MS = 350;
const LOGIN_SPLASH_VIDEO_MAX_MS = 4500;

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
    const video = document.getElementById('loginSplashVideo');
    // Swap to the static wordmark (the markup starts in video mode).
    const holdStatic = () => {
        el.classList.remove('login-splash--video');
        if (video) video.pause();
        setTimeout(dismiss, LOGIN_SPLASH_HOLD_MS);
    };

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!video || reduceMotion || typeof video.play !== 'function') {
        holdStatic();
        return;
    }

    video.addEventListener('ended', dismiss);
    video.addEventListener('error', dismiss, true);
    el.addEventListener('click', dismiss);
    document.addEventListener('keydown', dismiss);
    setTimeout(dismiss, LOGIN_SPLASH_VIDEO_MAX_MS);

    const playing = video.play();
    if (playing && typeof playing.catch === 'function') {
        playing.catch(() => {
            // Autoplay blocked (e.g. power-saver mode) — show the
            // static wordmark instead of a black screen.
            if (!done) holdStatic();
        });
    }
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
    safeInit(initSidebarKeyboardNav, 'sidebarKeyboardNav');
    safeInit(initSidebarScrollShadow, 'sidebarScrollShadow');
    safeInit(initThemeToggle, 'themeToggle');
    safeInit(initSettings, 'settings');
    safeInit(initPhClock, 'phClock');
    safeInit(initTopbarMotion, 'topbarMotion');
    safeInit(initTodaySalesChip, 'todaySalesChip');
    safeInit(revealContent, 'revealContent');
    safeInit(initStatCountUp, 'statCountUp');
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

function sortMovementTypes(types) {
    function rank(t) {
        var i = MOVEMENT_TYPE_ORDER.indexOf(t);
        return i === -1 ? MOVEMENT_TYPE_ORDER.length : i;
    }
    return types.slice().sort(function (a, b) { return rank(a) - rank(b); });
}

function titleCaseLabel(text) {
    return String(text).toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
}

// HTML legend for a Chart.js chart, rendered into `container`. Chart.js's
// canvas legend wraps into ragged centred rows on a phone and eats into
// the plot's fixed height; this one is a real grid (see .chart-legend in
// style.css) under the canvas. Each chip still toggles its dataset on and
// off, same as clicking the built-in legend.
function renderChartLegend(chart, container) {
    if (!container) return;
    container.innerHTML = '';
    chart.data.datasets.forEach(function (ds, i) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'chart-legend-item';
        btn.setAttribute('aria-pressed', 'true');
        var swatch = document.createElement('span');
        swatch.className = 'chart-legend-swatch';
        swatch.style.background = ds.borderColor;
        var label = document.createElement('span');
        label.className = 'chart-legend-label';
        label.textContent = ds.label;
        btn.appendChild(swatch);
        btn.appendChild(label);
        btn.addEventListener('click', function () {
            var visible = chart.isDatasetVisible(i);
            chart.setDatasetVisibility(i, !visible);
            btn.setAttribute('aria-pressed', String(!visible));
            chart.update();
        });
        container.appendChild(btn);
    });
}

// ---- Dashboard chart kit (Boss-Insights style) -------------------------
// Shared by the admin and branch Reports pages: compact peso formatting,
// SVG sparklines for KPI tiles, up/down delta chips, a donut with its total
// in the middle plus a "name / amount / share" list, and one set of Chart.js
// defaults so every graph reads as one system.
function compactPeso(value) {
    var n = Number(value) || 0;
    var abs = Math.abs(n);
    var sign = n < 0 ? '-' : '';
    if (abs < 1000) return sign + '₱' + abs.toLocaleString('en-PH', { maximumFractionDigits: abs % 1 ? 2 : 0 });
    var f = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 });
    return sign + '₱' + f.format(abs);
}

function fullPeso(value) {
    return '₱' + Number(value || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pctChange(current, previous) {
    if (!previous) return null;
    return ((current - previous) / Math.abs(previous)) * 100;
}

function chartTheme() {
    var css = getComputedStyle(document.documentElement);
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    var accent = css.getPropertyValue('--accent').trim();
    return {
        dark: dark,
        ink: css.getPropertyValue('--ink').trim(),
        soft: css.getPropertyValue('--ink-soft').trim(),
        faint: css.getPropertyValue('--ink-faint').trim(),
        grid: css.getPropertyValue('--border').trim(),
        surface: css.getPropertyValue('--surface').trim(),
        accent: accent,
        sales: dark ? '#4fb3bf' : '#1f93a1',
        cost: dark ? '#ff7a8a' : '#e0475b',
        profit: accent,
        cream: dark ? '#d8d3c4' : '#a9a28c',
        series: dark
            ? [accent, '#4fb3bf', '#d8d3c4', '#ff7a8a', '#9085e9', '#199e70', '#d95926', '#3d9fd6']
            : [accent, '#1f93a1', '#a9a28c', '#e0475b', '#4a3aa7', '#1baf7a', '#eb6834', '#1f78b4']
    };
}

// Chart.js defaults + the "total in the middle" doughnut plugin. Safe to
// call on every data load.
function applyChartDefaults() {
    var t = chartTheme();
    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.color = t.soft;
    Chart.defaults.borderColor = t.grid;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.elements.line.borderWidth = 2.5;
    Chart.defaults.elements.line.tension = 0.4;
    Chart.defaults.elements.point.radius = 0;
    Chart.defaults.elements.point.hoverRadius = 5;
    Chart.defaults.elements.bar.borderRadius = 6;
    Chart.defaults.elements.bar.borderSkipped = false;
    Chart.defaults.interaction = { mode: 'index', intersect: false };
    var tip = Chart.defaults.plugins.tooltip;
    tip.backgroundColor = t.surface;
    tip.titleColor = t.ink;
    tip.bodyColor = t.soft;
    tip.borderColor = t.dark ? 'rgba(255,245,220,0.18)' : 'rgba(23,20,13,0.16)';
    tip.borderWidth = 1;
    tip.padding = 10;
    tip.cornerRadius = 10;
    tip.boxPadding = 4;
    tip.usePointStyle = true;
    if (!Chart.registry.plugins.get('donutCenter')) {
        Chart.register({
            id: 'donutCenter',
            afterDraw: function (chart) {
                var opts = chart.options.plugins && chart.options.plugins.donutCenter;
                if (!opts || !opts.title) return;
                var meta = chart.getDatasetMeta(0);
                if (!meta || !meta.data.length) return;
                var ctx = chart.ctx;
                var x = meta.data[0].x, y = meta.data[0].y;
                var th = chartTheme();
                ctx.save();
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = th.ink;
                ctx.font = "700 22px 'Inter', sans-serif";
                ctx.fillText(opts.title, x, y - 8);
                ctx.fillStyle = th.faint;
                ctx.font = "500 12px 'Inter', sans-serif";
                ctx.fillText(opts.subtitle || 'Total', x, y + 14);
                ctx.restore();
            }
        });
    }
}

// Flat sparkline with a soft fill, as inline SVG (no axes, no library).
function sparklineSVG(values, color) {
    var w = 100, h = 30, pad = 2;
    if (!values || values.length < 2) values = [0, 0];
    var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
    var span = max - min || 1;
    var pts = values.map(function (v, i) {
        var x = (i / (values.length - 1)) * w;
        var y = h - pad - ((v - min) / span) * (h - pad * 2);
        return x.toFixed(2) + ',' + y.toFixed(2);
    });
    var line = 'M' + pts.join(' L');
    return '<svg class="kpi-spark-svg" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true">' +
        '<path d="' + line + ' L' + w + ',' + h + ' L0,' + h + ' Z" fill="' + color + '" opacity="0.14"/>' +
        '<path d="' + line + '" fill="none" stroke="' + color + '" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>' +
        '</svg>';
}

// Fills one KPI tile (markup: .kpi-tile > .kpi-value, .kpi-meta, .kpi-spark).
//   o.value      text for the big number (title gets o.title)
//   o.delta      change vs previous period, or null (a % unless o.points)
//   o.points     true when delta is in percentage points (margin tiles)
//   o.goodWhenUp false for costs: an increase is shown red
//   o.note       text beside the chip, e.g. "17.1% of total sales"
//   o.series     values for the sparkline; o.color its colour
function fillKpiTile(el, o) {
    if (!el) return;
    var val = el.querySelector('.kpi-value');
    val.textContent = o.value;
    if (o.title) val.title = o.title;
    var meta = el.querySelector('.kpi-meta');
    var chip = '';
    var d = o.delta;
    if (d !== null && d !== undefined && isFinite(d)) {
        var up = d > 0, flat = Math.abs(d) < 0.05;
        var good = flat ? null : (up === (o.goodWhenUp !== false));
        var cls = flat ? 'flat' : (good ? 'good' : 'bad');
        var arrow = flat ? '→' : (up ? '↑' : '↓');
        chip = '<span class="kpi-chip kpi-chip-' + cls + '" title="vs previous period">' + arrow + ' ' +
            Math.abs(d).toFixed(1) + (o.points ? ' pts' : '%') + '</span>';
    }
    meta.innerHTML = chip;
    if (o.note) {
        var note = document.createElement('span');
        note.className = 'kpi-note';
        note.textContent = o.note;
        meta.appendChild(note);
    }
    var spark = el.querySelector('.kpi-spark');
    if (spark) spark.innerHTML = o.series ? sparklineSVG(o.series, o.color) : '';
}

// "Dot  Name  Amount  Share%" rows under a doughnut.
function renderShareList(container, items, colors, formatValue) {
    if (!container) return;
    var total = items.reduce(function (a, b) { return a + b.value; }, 0);
    container.innerHTML = '';
    if (!items.length) {
        container.innerHTML = '<div class="share-empty">No data yet</div>';
        return;
    }
    items.forEach(function (it, i) {
        var row = document.createElement('div');
        row.className = 'share-row';
        var dot = document.createElement('span');
        dot.className = 'share-dot';
        dot.style.background = colors[i % colors.length];
        var name = document.createElement('span');
        name.className = 'share-name';
        name.textContent = it.name;
        var amt = document.createElement('span');
        amt.className = 'share-amount';
        amt.textContent = formatValue(it.value);
        var pct = document.createElement('span');
        pct.className = 'share-pct';
        pct.textContent = (total ? (it.value / total) * 100 : 0).toFixed(1) + '%';
        row.appendChild(dot); row.appendChild(name); row.appendChild(amt); row.appendChild(pct);
        container.appendChild(row);
    });
}

// Common options for a smooth line chart.
function lineChartOptions(t, yFormat) {
    return {
        plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: function (c) { return ' ' + c.dataset.label + '  ' + (yFormat ? yFormat(c.parsed.y) : c.parsed.y); } } }
        },
        scales: {
            y: { beginAtZero: true, border: { display: false }, grid: { color: t.grid }, ticks: { padding: 8, callback: function (v) { return yFormat ? yFormat(v) : v; } } },
            x: { border: { display: false }, grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 18 } }
        }
    };
}

// Keeps a donut readable with many outlets: the biggest (max - 1) rows stay,
// the rest fold into one "Other (n)" slice, flagged other:true so callers can
// give it a neutral colour instead of recycling a palette colour.
function topWithOther(rows, max) {
    var sorted = rows.slice().sort(function (a, b) { return b.value - a.value; });
    if (sorted.length <= max) return sorted;
    var head = sorted.slice(0, max - 1);
    var tail = sorted.slice(max - 1);
    head.push({
        name: 'Other (' + tail.length + ')',
        value: tail.reduce(function (a, r) { return a + r.value; }, 0),
        other: true
    });
    return head;
}

// One colour per row: palette order, with the folded "Other" slice neutral.
function rowColors(rows, palette, neutral) {
    return rows.map(function (r, i) { return r.other ? neutral : palette[i % palette.length]; });
}

function shortDateLabel(iso) {
    var d = new Date(iso + 'T00:00:00');
    return isNaN(d) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function initCustomSelects(root) {
    (root || document).querySelectorAll('select').forEach(enhanceSelect);
    (root || document).querySelectorAll('input[list]').forEach(enhanceDatalistInput);
}

// Replaces the browser's own <datalist> suggestion popup (e.g. Record
// Sale's customer name) with the same custom panel the selects use. The
// input keeps its name/id/value; picking a suggestion fires the same
// input + change events typing would.
function enhanceDatalistInput(input) {
    if (input.dataset.dlEnhanced) return;
    const dl = document.getElementById(input.getAttribute('list'));
    if (!dl) return;
    input.dataset.dlEnhanced = '1';
    input.removeAttribute('list');

    const wrap = document.createElement('div');
    wrap.className = 'cs-select';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    const panel = document.createElement('div');
    panel.className = 'cs-panel';
    panel.setAttribute('role', 'listbox');
    wrap.appendChild(panel);

    let items = [];
    let active = -1;

    const close = () => {
        wrap.classList.remove('cs-open');
        active = -1;
    };
    const mark = () => {
        Array.from(panel.children).forEach((el, i) => el.classList.toggle('cs-active', i === active));
        const el = panel.children[active];
        if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    };
    const pick = (value) => {
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        close();
    };
    const render = () => {
        const q = input.value.trim().toLowerCase();
        items = Array.from(dl.options).map((o) => o.value)
            .filter((v) => v && v.toLowerCase() !== q && v.toLowerCase().includes(q))
            .slice(0, 8);
        panel.replaceChildren();
        items.forEach((v) => {
            const el = document.createElement('div');
            el.className = 'cs-option';
            el.setAttribute('role', 'option');
            el.textContent = v;
            el.addEventListener('mousedown', (e) => e.preventDefault());
            el.addEventListener('click', () => pick(v));
            panel.appendChild(el);
        });
        active = -1;
        if (items.length) wrap.classList.add('cs-open');
        else close();
    };

    input.addEventListener('focus', render);
    input.addEventListener('input', render);
    input.addEventListener('blur', close);
    input.addEventListener('keydown', (e) => {
        if (!wrap.classList.contains('cs-open')) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            active = (active + 1) % items.length;
            mark();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            active = (active - 1 + items.length) % items.length;
            mark();
        } else if (e.key === 'Enter' && active >= 0) {
            e.preventDefault();
            pick(items[active]);
        } else if (e.key === 'Escape') {
            close();
        }
    });
}

function enhanceSelect(select) {
    if (select.dataset.csEnhanced || select.multiple || select.dataset.csSkip !== undefined) return;
    select.dataset.csEnhanced = '1';

    const wrap = document.createElement('div');
    wrap.className = 'cs-select';
    // Optional look (e.g. "status": a coloured pill with a dot per
    // option and a one-line description under each choice).
    if (select.dataset.csVariant) wrap.classList.add('cs-variant-' + select.dataset.csVariant);
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
    // The status picker's panel is moved to <body> while open: a page
    // reveal animation leaves a transform on the cards, and a transformed
    // ancestor re-anchors position:fixed to itself instead of the viewport.
    const floating = wrap.classList.contains('cs-variant-status');
    if (floating) panel.classList.add('cs-panel-floating');

    const labelEl = trigger.querySelector('.cs-trigger-label');
    let triggerDot = null;
    let activeIndex = -1;

    function optionEls() {
        return Array.prototype.slice.call(panel.querySelectorAll('.cs-option'));
    }

    function isOpen() {
        return wrap.classList.contains('cs-open');
    }

    // The status picker lives in a scrolling table, whose overflow would
    // clip or scroll a normally-positioned panel, so its panel is fixed to
    // the viewport instead (and closed if the page scrolls under it).
    function positionFixed() {
        const r = trigger.getBoundingClientRect();
        const w = Math.min(272, window.innerWidth - 16);
        const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
        const below = window.innerHeight - r.bottom - 12;
        const above = r.top - 12;
        panel.style.width = w + 'px';
        panel.style.left = left + 'px';
        panel.style.right = 'auto';
        if (panel.scrollHeight > below && above > below) {
            wrap.classList.add('cs-open-up');
            panel.style.top = 'auto';
            panel.style.bottom = (window.innerHeight - r.top + 6) + 'px';
            panel.style.maxHeight = Math.min(320, above) + 'px';
        } else {
            wrap.classList.remove('cs-open-up');
            panel.style.bottom = 'auto';
            panel.style.top = (r.bottom + 6) + 'px';
            panel.style.maxHeight = Math.min(320, below) + 'px';
        }
    }

    function position() {
        if (wrap.classList.contains('cs-variant-status')) {
            positionFixed();
            return;
        }
        // Flip the panel above the trigger when there isn't room below
        // — the same reasoning as every other floating panel in the
        // app (e.g. the product-search combos), just generalized here
        // since a <select> can live anywhere: low in a long form, near
        // the bottom of a modal, etc.
        //
        // "Room" here is bounded by whichever is smaller: the viewport
        // edge, or the bottom/top of the card (or modal) the select
        // lives in. Viewport-only used to be the sole limit, so a long
        // option list on a select positioned mid-card (plenty of
        // *viewport* space below, since the page keeps scrolling) would
        // still open a tall panel that overhung onto whatever section
        // comes after — Products' "Bulk import / export" card right
        // under "Add a product", Production's own history table below
        // its two-column row, etc. A panel is capped to its own card's
        // remaining space instead and scrolls internally for the rest,
        // exactly like it already does against the viewport.
        wrap.classList.remove('cs-open-up');
        panel.style.maxHeight = '';
        const rect = trigger.getBoundingClientRect();
        const host = wrap.closest('.card, .modal, [role="dialog"]');
        const hostRect = host ? host.getBoundingClientRect() : null;
        const margin = 12;

        const viewportBelow = window.innerHeight - rect.bottom;
        const spaceBelow = hostRect
            ? Math.max(0, Math.min(viewportBelow, hostRect.bottom - rect.bottom - margin))
            : viewportBelow;

        const viewportAbove = rect.top;
        const spaceAbove = hostRect
            ? Math.max(0, Math.min(viewportAbove, rect.top - Math.max(0, hostRect.top) - margin))
            : viewportAbove;

        const defaultMax = parseFloat(getComputedStyle(panel).maxHeight) || 280;
        const panelHeight = panel.scrollHeight || defaultMax;

        if (spaceBelow < panelHeight && spaceAbove > spaceBelow) {
            wrap.classList.add('cs-open-up');
            panel.style.maxHeight = Math.max(120, Math.min(defaultMax, spaceAbove)) + 'px';
        } else {
            panel.style.maxHeight = Math.max(120, Math.min(defaultMax, spaceBelow)) + 'px';
        }

        // A narrow trigger's panel is allowed to be wider than it (see
        // .cs-panel's min-width); keep that wider panel on screen by
        // anchoring it to the trigger's right edge when it would spill.
        wrap.classList.remove('cs-align-right');
        if (panel.getBoundingClientRect().right > document.documentElement.clientWidth - 8) {
            wrap.classList.add('cs-align-right');
        }
    }

    function open() {
        if (select.disabled || isOpen()) return;
        rebuildPanel();
        if (floating) {
            document.body.appendChild(panel);
            panel.classList.add('cs-panel-open');
        }
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
        wrap.classList.remove('cs-open');
        panel.classList.remove('cs-panel-open');
        // Deliberately NOT clearing 'cs-open-up' (or the inline
        // max-height position() set) here — the panel is still
        // visible for the rest of its closing transform transition,
        // and removing 'cs-open-up' immediately flips it from
        // bottom-anchored back to its top-anchored default mid-flight,
        // which reads as the panel jumping down to the bottom of the
        // trigger for a split second before it fades out. position()
        // already resets both fresh at the start of every open().
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
        // Re-picking the current value of an auto-submitting select (a
        // status switch) must not post the form again.
        if (i === select.selectedIndex && select.hasAttribute('data-autosubmit')) return;
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
            // Optional per-option extras: data-tone paints a leading dot,
            // data-desc adds a muted second line.
            if (opt.dataset.tone) {
                const dot = document.createElement('span');
                dot.className = 'cs-dot';
                dot.dataset.tone = opt.dataset.tone;
                el.appendChild(dot);
            }
            const body = document.createElement('span');
            body.className = 'cs-option-body';
            const name = document.createElement('span');
            name.className = 'cs-option-name';
            name.textContent = opt.textContent.trim() || ' ';
            body.appendChild(name);
            if (opt.dataset.desc) {
                const desc = document.createElement('span');
                desc.className = 'cs-option-desc';
                desc.textContent = opt.dataset.desc;
                body.appendChild(desc);
            }
            el.appendChild(body);
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
        // Tone dot on the trigger itself, and the tone on the wrapper so
        // the variant's CSS can tint the whole pill.
        if (opt && opt.dataset.tone) {
            if (!triggerDot) {
                triggerDot = document.createElement('span');
                triggerDot.className = 'cs-dot';
                trigger.insertBefore(triggerDot, labelEl);
            }
            triggerDot.dataset.tone = opt.dataset.tone;
            wrap.dataset.tone = opt.dataset.tone;
        }
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

    // Page scripts rebuild rows that contain selects (e.g. Bulk Batches'
    // material list), so drop these global listeners once this select
    // has left the page instead of piling them up.
    function onDocumentClick(e) {
        if (!wrap.isConnected) { detachGlobalListeners(); return; }
        if (isOpen() && !wrap.contains(e.target) && !panel.contains(e.target)) close();
    }
    function onWindowResize() {
        if (!wrap.isConnected) { detachGlobalListeners(); return; }
        if (isOpen()) position();
    }
    function onAnyScroll(e) {
        if (!wrap.isConnected) { detachGlobalListeners(); return; }
        if (isOpen() && !panel.contains(e.target)) close();
    }
    function detachGlobalListeners() {
        document.removeEventListener('click', onDocumentClick);
        window.removeEventListener('resize', onWindowResize);
        window.removeEventListener('scroll', onAnyScroll, true);
        if (floating) panel.remove();
    }
    document.addEventListener('click', onDocumentClick);
    window.addEventListener('resize', onWindowResize);
    if (wrap.classList.contains('cs-variant-status')) window.addEventListener('scroll', onAnyScroll, true);

    rebuildPanel();
    sync();
}

// Selects and date inputs that page scripts create after load (rows
// added to a cart/ingredient list, a modal filled in on demand) would
// otherwise stay native, and on a phone a native <select> opens the
// OS's full-screen picker. Watching the whole page enhances them the
// moment they're inserted, so every dropdown in the app looks and
// behaves the same.
function watchForNewFormControls() {
    if (typeof MutationObserver !== 'function' || !document.body) return;
    const FIELD = 'select, input[list], input[type="date"], input[type="month"]';
    const roots = new Set();
    let scheduled = false;

    new MutationObserver((mutations) => {
        mutations.forEach((m) => {
            m.addedNodes.forEach((node) => {
                if (node.nodeType !== 1 || node.tagName === 'OPTION') return;
                if (node.matches(FIELD)) roots.add(node.parentNode);
                else if (node.querySelector(FIELD)) roots.add(node);
            });
        });
        if (!roots.size || scheduled) return;
        scheduled = true;
        queueMicrotask(() => {
            scheduled = false;
            roots.forEach((root) => {
                if (!root || !root.isConnected) return;
                initCustomSelects(root);
                initDatePickers(root);
            });
            roots.clear();
        });
    }).observe(document.body, { childList: true, subtree: true });
}

// Custom date/month picker — the same progressive-enhancement idea as
// initCustomSelects() above, applied to every <input type="date"> and
// <input type="month">. The browser's own picker is drawn by the OS
// (a different look on every browser/phone, none of it reachable from
// CSS), so this puts a styled trigger + calendar panel in front of it.
//
// The real input is kept exactly where it was, just made invisible and
// click-through (see .dp in style.css): it's still the form field that
// gets submitted, still the tab stop, still what `required`/min/max
// validation and every page script that reads or sets `.value` (the
// report builder's date range, Record Sale's backdated sale) talks to.
// Same two hooks as enhanceSelect() keep the trigger in sync with it:
// a `.value` property override for scripted changes, and a
// MutationObserver for min/max/disabled attribute changes.
//
// Opt out per field with `data-dp-skip`. Safe to re-run (softRefresh(),
// soft nav): dataset.dpEnhanced makes it a no-op on an already-enhanced
// input.
const DP_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
    'August', 'September', 'October', 'November', 'December'];
const DP_WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

function initDatePickers(root) {
    (root || document).querySelectorAll('input[type="date"], input[type="month"]').forEach(enhanceDateInput);
}

function enhanceDateInput(input) {
    if (input.dataset.dpEnhanced || input.dataset.dpSkip !== undefined) return;
    input.dataset.dpEnhanced = '1';
    const monthMode = input.type === 'month';

    const pad = (n) => String(n).padStart(2, '0');
    const toIso = (y, m, d) => monthMode ? `${y}-${pad(m + 1)}` : `${y}-${pad(m + 1)}-${pad(d)}`;
    // Parsed as plain numbers, never through `new Date(string)`, which
    // treats "YYYY-MM-DD" as UTC midnight and can land on the previous
    // day in any timezone west of UTC.
    function parse(value) {
        const m = monthMode ? /^(\d{4})-(\d{2})$/.exec(value || '') : /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
        return m ? { y: +m[1], m: +m[2] - 1, d: monthMode ? 1 : +m[3] } : null;
    }
    const today = new Date();
    const todayIso = toIso(today.getFullYear(), today.getMonth(), today.getDate());
    const inRange = (iso) => !((input.min && iso < input.min) || (input.max && iso > input.max));

    const wrap = document.createElement('div');
    wrap.className = 'dp';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'dp-trigger';
    // Not its own tab stop, same as .cs-trigger: the real input stays
    // the focusable control (see this function's header comment).
    trigger.tabIndex = -1;
    trigger.setAttribute('aria-hidden', 'true');
    trigger.innerHTML =
        '<span class="dp-trigger-label"></span>' +
        '<svg class="dp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' +
        '<rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>';
    wrap.appendChild(trigger);
    const labelEl = trigger.querySelector('.dp-trigger-label');

    const panel = document.createElement('div');
    panel.className = 'dp-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', monthMode ? 'Choose month' : 'Choose date');
    wrap.appendChild(panel);

    // view: the month (or, in month mode, the year) the panel shows.
    // cursor: the keyboard-highlighted cell, an ISO string like the value.
    let view = { y: today.getFullYear(), m: today.getMonth() };
    let cursor = null;

    const isOpen = () => wrap.classList.contains('dp-open');

    function syncTrigger() {
        const p = parse(input.value);
        if (p) {
            labelEl.textContent = monthMode
                ? `${DP_MONTHS[p.m]} ${p.y}`
                : `${DP_MONTHS[p.m].slice(0, 3)} ${p.d}, ${p.y}`;
            labelEl.classList.remove('dp-placeholder');
        } else {
            labelEl.textContent = input.placeholder || (monthMode ? 'Select month' : 'Select date');
            labelEl.classList.add('dp-placeholder');
        }
        wrap.classList.toggle('dp-disabled', input.disabled);
    }

    function cell(text, iso, extraClass) {
        const b = document.createElement('button');
        b.type = 'button';
        b.tabIndex = -1;
        b.className = 'dp-cell' + (extraClass ? ' ' + extraClass : '');
        b.textContent = text;
        b.dataset.iso = iso;
        if (!inRange(iso)) b.disabled = true;
        if (iso === input.value) b.classList.add('dp-selected');
        if (iso === (monthMode ? todayIso.slice(0, 7) : todayIso)) b.classList.add('dp-today');
        if (iso === cursor) b.classList.add('dp-cursor');
        return b;
    }

    function render() {
        panel.innerHTML = '';
        const head = document.createElement('div');
        head.className = 'dp-head';
        const title = document.createElement('span');
        title.className = 'dp-title';
        title.textContent = monthMode ? String(view.y) : `${DP_MONTHS[view.m]} ${view.y}`;
        const nav = (dir, label, path) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.tabIndex = -1;
            b.className = 'dp-nav';
            b.setAttribute('aria-label', label);
            b.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="${path}"/></svg>`;
            b.addEventListener('click', () => shiftView(dir));
            return b;
        };
        head.append(
            title,
            nav(-1, monthMode ? 'Previous year' : 'Previous month', 'm15 6-6 6 6 6'),
            nav(1, monthMode ? 'Next year' : 'Next month', 'm9 6 6 6-6 6'),
        );
        panel.appendChild(head);

        const grid = document.createElement('div');
        grid.className = monthMode ? 'dp-grid dp-grid-months' : 'dp-grid';
        if (monthMode) {
            DP_MONTHS.forEach((name, i) => grid.appendChild(cell(name.slice(0, 3), toIso(view.y, i), '')));
        } else {
            DP_WEEKDAYS.forEach((d) => {
                const w = document.createElement('span');
                w.className = 'dp-weekday';
                w.textContent = d;
                grid.appendChild(w);
            });
            // Leading/trailing days from the neighbouring months keep
            // every month a steady 6-row grid, so the panel never
            // changes height (and jumps) while paging through months.
            const first = new Date(view.y, view.m, 1);
            const start = new Date(view.y, view.m, 1 - first.getDay());
            for (let i = 0; i < 42; i += 1) {
                const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
                const outside = d.getMonth() !== view.m;
                grid.appendChild(cell(String(d.getDate()), toIso(d.getFullYear(), d.getMonth(), d.getDate()),
                    outside ? 'dp-outside' : ''));
            }
        }
        grid.addEventListener('click', (e) => {
            const b = e.target.closest('.dp-cell');
            if (b && !b.disabled) pick(b.dataset.iso);
        });
        panel.appendChild(grid);

        const foot = document.createElement('div');
        foot.className = 'dp-foot';
        const footBtn = (text, fn, disabled) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.tabIndex = -1;
            b.className = 'dp-foot-btn';
            b.textContent = text;
            b.disabled = !!disabled;
            b.addEventListener('click', fn);
            return b;
        };
        const nowIso = monthMode ? todayIso.slice(0, 7) : todayIso;
        foot.append(
            footBtn('Clear', () => pick(''), input.required),
            footBtn(monthMode ? 'This month' : 'Today', () => pick(nowIso), !inRange(nowIso)),
        );
        panel.appendChild(foot);

        // Keep every panel button from stealing focus off the real input
        // (which would blur it and close the panel before the click lands).
        panel.querySelectorAll('button').forEach((b) => b.addEventListener('mousedown', (e) => e.preventDefault()));
    }

    function shiftView(dir) {
        if (monthMode) view.y += dir;
        else {
            const d = new Date(view.y, view.m + dir, 1);
            view = { y: d.getFullYear(), m: d.getMonth() };
        }
        render();
    }

    function position() {
        wrap.classList.remove('dp-open-up');
        const rect = trigger.getBoundingClientRect();
        const below = window.innerHeight - rect.bottom;
        if (below < panel.offsetHeight + 16 && rect.top > below) wrap.classList.add('dp-open-up');
    }

    function open() {
        if (input.disabled || isOpen()) return;
        const p = parse(input.value);
        const base = p || parse(monthMode ? todayIso.slice(0, 7) : todayIso);
        view = { y: base.y, m: base.m };
        cursor = input.value || null;
        render();
        wrap.classList.add('dp-open');
        position();
    }

    function close() {
        if (!isOpen()) return;
        wrap.classList.remove('dp-open');
        cursor = null;
    }

    function pick(iso) {
        if (iso && !inRange(iso)) return;
        valueDescriptor.set.call(input, iso);
        syncTrigger();
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        close();
    }

    // Arrow keys walk the cursor a day (or, in month mode, a month) at a
    // time and a week (or a row of three months) vertically, paging the
    // view along with it, Enter picks it.
    function moveCursor(step) {
        const p = parse(cursor || input.value) || parse(monthMode ? todayIso.slice(0, 7) : todayIso);
        const d = monthMode ? new Date(p.y, p.m + step, 1) : new Date(p.y, p.m, p.d + step);
        cursor = toIso(d.getFullYear(), d.getMonth(), d.getDate());
        view = { y: d.getFullYear(), m: d.getMonth() };
        render();
    }

    const valueDescriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    Object.defineProperty(input, 'value', {
        configurable: true,
        enumerable: true,
        get() { return valueDescriptor.get.call(input); },
        set(v) {
            valueDescriptor.set.call(input, v);
            syncTrigger();
            if (isOpen()) render();
        },
    });

    new MutationObserver(() => { syncTrigger(); if (isOpen()) render(); })
        .observe(input, { attributes: true, attributeFilter: ['min', 'max', 'disabled', 'required', 'placeholder'] });

    input.addEventListener('focus', () => wrap.classList.add('dp-focus'));
    input.addEventListener('blur', () => { wrap.classList.remove('dp-focus'); close(); });
    input.addEventListener('change', syncTrigger);
    // Native typing into a hidden date field would edit it blind, so
    // every key goes through the panel instead.
    input.addEventListener('keydown', (e) => {
        if (input.disabled || e.key === 'Tab') { close(); return; }
        e.preventDefault();
        if (!isOpen()) {
            if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(e.key)) open();
            return;
        }
        const rowStep = monthMode ? 3 : 7;
        if (e.key === 'Escape') close();
        else if (e.key === 'ArrowLeft') moveCursor(-1);
        else if (e.key === 'ArrowRight') moveCursor(1);
        else if (e.key === 'ArrowUp') moveCursor(-rowStep);
        else if (e.key === 'ArrowDown') moveCursor(rowStep);
        else if (e.key === 'PageUp') shiftView(-1);
        else if (e.key === 'PageDown') shiftView(1);
        else if ((e.key === 'Enter' || e.key === ' ') && cursor) pick(cursor);
    });

    trigger.addEventListener('mousedown', (e) => e.preventDefault());
    trigger.addEventListener('click', () => {
        if (input.disabled) return;
        input.focus({ preventScroll: true });
        if (isOpen()) close(); else open();
    });
    document.addEventListener('click', (e) => { if (isOpen() && !wrap.contains(e.target)) close(); });
    window.addEventListener('resize', () => { if (isOpen()) position(); });

    syncTrigger();
}

// Replacements for inline on*= attributes, which the CSP no longer allows.
// Delegated on `document`, so they survive soft-nav content swaps:
//   data-call="fnName"            click -> window.fnName(element)
//   data-autosubmit (on <select>)  change -> submit its form
//   data-reveal-target="id" data-reveal-value="v"
//                                  change -> show #id only when value === v
function initDelegatedActions() {
    document.addEventListener('click', (e) => {
        const el = e.target.closest ? e.target.closest('[data-call]') : null;
        if (!el) return;
        const fn = window[el.dataset.call];
        if (typeof fn === 'function') fn(el);
    });
    document.addEventListener('change', (e) => {
        const el = e.target;
        if (!(el instanceof HTMLElement)) return;
        if (el.matches('select[data-autosubmit]') && el.form) el.form.submit();
        if (el.dataset.revealTarget) {
            const target = document.getElementById(el.dataset.revealTarget);
            if (target) target.style.display = el.value === el.dataset.revealValue ? 'block' : 'none';
        }
    });
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
    initDelegatedActions();
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

// Styled, floating variant of the confirm pattern above, for forms that
// deserve more than the browser's confirm() popup (currently Sign out):
// data-confirm-dialog="Title", optional data-confirm-detail="Body" and
// data-confirm-ok="Button label". Built lazily and appended to <body> so
// it survives soft-nav content swaps, like the loading overlay below.
function initConfirmModal() {
    let overlay = null;
    let pendingForm = null;
    let returnFocus = null;

    function build() {
        overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        overlay.hidden = true;
        overlay.innerHTML =
            '<div class="confirm-dialog" role="alertdialog" aria-modal="true" ' +
            'aria-labelledby="confirmDialogTitle" aria-describedby="confirmDialogDetail">' +
            '<h2 class="confirm-dialog-title" id="confirmDialogTitle"></h2>' +
            '<p class="confirm-dialog-detail" id="confirmDialogDetail"></p>' +
            '<div class="confirm-dialog-actions">' +
            '<button type="button" class="btn btn-ghost" data-confirm-cancel>Cancel</button>' +
            '<button type="button" class="btn btn-danger" data-confirm-accept></button>' +
            '</div></div>';
        document.body.appendChild(overlay);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.closest('[data-confirm-cancel]')) close();
            else if (e.target.closest('[data-confirm-accept]')) accept();
        });
    }

    function close() {
        if (!overlay || overlay.hidden) return;
        overlay.hidden = true;
        pendingForm = null;
        if (returnFocus && returnFocus.focus) returnFocus.focus();
    }

    function accept() {
        const form = pendingForm;
        overlay.hidden = true;
        pendingForm = null;
        // submit() skips the submit event, so this doesn't re-open the dialog.
        if (form) form.submit();
    }

    document.addEventListener('keydown', (e) => {
        if (!overlay || overlay.hidden) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            close();
        } else if (e.key === 'Tab') {
            const btns = overlay.querySelectorAll('button');
            const first = btns[0];
            const last = btns[btns.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    });

    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.dataset.confirmDialog) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        if (!overlay) build();
        pendingForm = form;
        returnFocus = document.activeElement;
        overlay.querySelector('.confirm-dialog-title').textContent = form.dataset.confirmDialog;
        const detail = overlay.querySelector('.confirm-dialog-detail');
        detail.textContent = form.dataset.confirmDetail || '';
        detail.hidden = !detail.textContent;
        overlay.querySelector('[data-confirm-accept]').textContent = form.dataset.confirmOk || 'Confirm';
        overlay.hidden = false;
        overlay.querySelector('[data-confirm-cancel]').focus();
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
//
// That "the browser tears the whole page down" assumption doesn't hold
// for a form (or submit button) targeting a new tab/window — e.g. the
// Reports page's Download PDF/Excel buttons, formtarget="_blank" so the
// file opens in a new tab while this one stays put. isSoftSubmittableForm()
// above already excludes those from soft-nav (target !== '_self'), which
// used to leave them showing the overlay here with nothing left to ever
// hide it again: the current page never navigates, so it just sat there
// reading "Loading…" forever after every report download. Skipped with
// the same target-resolution isSoftSubmittableForm() uses, so only a
// submit that's actually about to navigate *this* tab shows it.
function initSubmitLoadingState() {
    document.addEventListener('submit', (e) => {
        if (e.defaultPrevented) return;
        if (!(e.target instanceof HTMLFormElement)) return;
        const form = e.target;
        const target = (e.submitter && e.submitter.formTarget) || form.target;
        if (target && target !== '_self') return;
        showLoadingOverlay();
    });
}

// ---------------------------------------------------------------
// Receipt downloads, report PDF/Excel generation, and the product CSV
// export (every element with [data-generate-file] — see admin/reports.html,
// branch/reports.html, and the various Receipt/Export links across both
// admin and branch templates) all build a file server-side before
// sending anything back. Unlike a normal form submit, none of them
// navigate the current tab (they're target="_blank"/formtarget="_blank"
// specifically so initSoftNav() and initSubmitLoadingState() both
// leave them alone — see those functions' own comments), so there was
// nothing on screen between the click and the browser's own download
// indicator a moment later, which reads as "did that even do
// anything?" when generation takes more than an instant.
//
// Handled here with fetch instead of letting the browser follow the
// link/submit the form directly, so this dialog can show real
// progress once bytes start arriving and be dismissed exactly when
// the file is actually ready — not a guessed timeout. A response
// that isn't one of the file types these endpoints actually produce
// (e.g. generate_report() redirecting back to the report builder with
// a "no data matches those filters" flash) is treated as a normal
// page navigation instead of a download.
let generateOverlayEl = null;
let generateInFlight = false;
let generateOverlayShownAt = 0;
// A small/fast receipt can finish end-to-end well under a second,
// which made the dialog blip on and immediately back off — barely
// long enough to register as having happened. Same idea as
// LOADING_OVERLAY_MIN_VISIBLE_MS above, just held a beat longer since
// this one's showing a progress bar that's more noticeable mid-cut.
const GENERATE_OVERLAY_MIN_VISIBLE_MS = 900;

function getGenerateOverlay() {
    if (generateOverlayEl) return generateOverlayEl;
    const el = document.createElement('div');
    el.className = 'loading-overlay';
    el.hidden = true;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.innerHTML =
        '<div class="loading-dialog">' +
        '<div class="loading-dialog-text" id="genOverlayText">Preparing your file…</div>' +
        '<div class="gen-progress-track indeterminate" id="genProgressTrack">' +
        '<div class="gen-progress-fill" id="genProgressFill"></div>' +
        '</div>' +
        '</div>';
    document.body.appendChild(el);
    generateOverlayEl = el;
    return el;
}

function showGenerateOverlay() {
    generateOverlayShownAt = Date.now();
    const el = getGenerateOverlay();
    el.querySelector('#genProgressTrack').classList.add('indeterminate');
    el.querySelector('#genProgressFill').style.width = '';
    el.querySelector('#genOverlayText').textContent = 'Preparing your file…';
    el.classList.remove('loading-overlay-exit');
    el.hidden = false;
}

// Called as real response bytes come in (see runFileGeneration()) —
// switches the bar from the indeterminate sliding state (generation
// has no measurable progress; send_file only hands back a
// Content-Length once the file is already fully built) to a real,
// determinate width.
function setGenerateProgress(fraction) {
    if (!generateOverlayEl) return;
    generateOverlayEl.querySelector('#genProgressTrack').classList.remove('indeterminate');
    generateOverlayEl.querySelector('#genProgressFill').style.width = `${Math.round(Math.min(fraction, 1) * 100)}%`;
    generateOverlayEl.querySelector('#genOverlayText').textContent = 'Downloading…';
}

function hideGenerateOverlay() {
    if (!generateOverlayEl || generateOverlayEl.hidden) return;

    const doHide = () => {
        generateOverlayEl.classList.add('loading-overlay-exit');
        setTimeout(() => {
            generateOverlayEl.hidden = true;
            generateOverlayEl.classList.remove('loading-overlay-exit');
        }, LOADING_OVERLAY_EXIT_MS);
    };

    const remaining = GENERATE_OVERLAY_MIN_VISIBLE_MS - (Date.now() - generateOverlayShownAt);
    if (remaining > 0) {
        setTimeout(doHide, remaining);
    } else {
        doHide();
    }
}

function generateFileNameFromHeader(contentDisposition) {
    if (!contentDisposition) return null;
    const starMatch = /filename\*=UTF-8''([^;]+)/i.exec(contentDisposition);
    if (starMatch) {
        try { return decodeURIComponent(starMatch[1]); } catch (e) { /* fall through */ }
    }
    const match = /filename="?([^";]+)"?/i.exec(contentDisposition);
    return match ? match[1] : null;
}

function triggerBlobDownload(blob, filename) {
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename || 'download';
    document.body.appendChild(a);
    a.click();
    a.remove();
    if (window.Sfx) window.Sfx.play('download');
    // Not revoked immediately — some browsers start the actual save
    // asynchronously right after the click, and revoking too early can
    // cancel that in-flight save.
    setTimeout(() => URL.revokeObjectURL(blobUrl), 30000);
}

async function runFileGeneration(url) {
    if (generateInFlight) return;
    generateInFlight = true;
    showGenerateOverlay();
    try {
        const res = await fetch(url, { credentials: 'same-origin' });
        const contentType = res.headers.get('content-type') || '';
        const isFile = /application\/pdf|spreadsheetml|text\/csv/i.test(contentType);

        if (!res.ok || !isFile) {
            // Not a file — e.g. an expired session bouncing to /login,
            // or generate_report() redirecting back with a "no data
            // matches those filters" flash. fetch() already followed
            // any redirect, so res.url is wherever that landed.
            window.location.href = res.url || url;
            return;
        }

        const total = parseInt(res.headers.get('content-length') || '0', 10);
        const chunks = [];
        if (res.body && res.body.getReader && total) {
            const reader = res.body.getReader();
            let received = 0;
            setGenerateProgress(0);
            for (; ;) {
                const { done, value } = await reader.read();
                if (done) break;
                chunks.push(value);
                received += value.length;
                setGenerateProgress(received / total);
            }
        } else {
            chunks.push(new Uint8Array(await res.arrayBuffer()));
            setGenerateProgress(1);
        }

        const blob = new Blob(chunks, { type: contentType });
        const filename = generateFileNameFromHeader(res.headers.get('content-disposition'));
        triggerBlobDownload(blob, filename);
    } catch (err) {
        // Network hiccup, or a browser that doesn't support one of the
        // fetch/stream APIs used above — fall back to a real navigation
        // so the download still happens, just without the dialog.
        window.location.href = url;
    } finally {
        generateInFlight = false;
        hideGenerateOverlay();
    }
}

function initGenerateFileFeedback() {
    document.querySelectorAll('[data-generate-file]').forEach((el) => {
        if (el instanceof HTMLFormElement) {
            el.addEventListener('submit', (e) => {
                if (e.defaultPrevented) return;
                e.preventDefault();
                const params = new URLSearchParams(new FormData(el, e.submitter));
                const query = params.toString();
                runFileGeneration(query ? `${el.action}?${query}` : el.action);
            });
        } else {
            el.addEventListener('click', (e) => {
                e.preventDefault();
                runFileGeneration(el.href);
            });
        }
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

// Anchors a paginated table/list's own top edge to the same scroll
// position on every Prev/Next click, instead of leaving it to chance.
// Without this: paging to a much shorter page (e.g. a last page with
// one leftover row) shrinks the document underneath an unmoved scroll
// position, so the browser clamps scrollTop to the new, shorter max —
// which happens to land right back on the now-nearby pagination
// controls. Paging back to a taller page doesn't reverse that:
// scrollTop stays exactly where it was clamped to, now sitting
// somewhere mid-table on the taller page, with its rows and controls
// pushed down out of view below it — the user has to scroll down again
// to find them. Scrolling the container's top into view on every click
// (not just when it happens to be needed) keeps the anchor point the
// same regardless of which direction the row count just changed.
function scrollPaginatedContainerIntoView(container) {
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
    container.scrollIntoView({ behavior, block: 'start' });
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
                // data-count-distinct: count unique row data-count-key
                // values instead of rows (e.g. Products counts SKUs by
                // base code, so A1-85ML and A1-BULK are one).
                const count = 'countDistinct' in container.dataset
                    ? new Set(filtered.map((r) => r.dataset.countKey || r)).size
                    : filtered.length;
                countTarget.textContent = count + ' ' + countLabel + (count !== 1 ? 's' : '');
            }
        }

        if (input) {
            input.addEventListener('input', () => { page = 1; render(); });
        }
        if (filterSelect) {
            filterSelect.addEventListener('change', () => { page = 1; render(); });
        }
        if (prevBtn) {
            prevBtn.addEventListener('click', () => {
                if (page > 1) { page--; render(); scrollPaginatedContainerIntoView(container); }
            });
        }
        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
                if (page < totalPages) { page++; render(); scrollPaginatedContainerIntoView(container); }
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

        if (prevBtn) {
            prevBtn.addEventListener('click', () => {
                if (page > 1) { page--; render(); scrollPaginatedContainerIntoView(container); }
            });
        }
        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                if (page < totalPages) { page++; render(); scrollPaginatedContainerIntoView(container); }
            });
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
        pulseBadge(badge);
        if (!PREFERS_REDUCED_MOTION.matches) {
            toggle.classList.remove('is-ringing');
            void toggle.offsetWidth;
            toggle.classList.add('is-ringing');
        }
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
        { match: /^\/admin\/(materials|suppliers|formulas)\/?$/, scopes: ['materials'] },
        { match: /^\/admin\/bulk-batches\/?$/, scopes: ['materials', 'production'] },
        { match: /^\/admin\/partners\/\d+\/?$/, scopes: ['partners'] },
        { match: /^\/admin\/requests\/\d+\/?$/, scopes: ['requests'] },
        { match: /^\/admin\/(low-stock|discrepancies)\/?$/, scopes: ['inventory', 'movement_logs'] },
        { match: /^\/admin\/(sales-history|customers|credit-purchases|record-sale)\/?$/, scopes: ['sales'] },
        { match: /^\/ai\/drafts\/?$/, scopes: ['ai_drafts'] },
        { match: /^\/admin\/?$/, scopes: ['requests', 'inventory', 'movement_logs', 'production', 'sales', 'partner_inquiries', 'materials'] },
        { match: /^\/admin\/reports\/?$/, scopes: ['sales', 'inventory', 'movement_logs', 'production', 'materials', 'partner_inquiries', 'branches'] },
        { match: /^\/admin\/branch-performance\/?$/, scopes: ['sales', 'inventory', 'movement_logs', 'branches'] },
        { match: /^\/branch\/reports\/?$/, scopes: ['sales', 'inventory', 'movement_logs'] },
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

    // Search boxes and GET filter forms hold no data worth protecting, so
    // they must never block a live refresh.
    function isDataField(el) {
        if (!el || !el.closest) return false;
        if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && el.tagName !== 'SELECT') return false;
        if (el.type === 'search' || el.type === 'hidden' || el.type === 'button' || el.type === 'submit') return false;
        if (!el.closest('.content')) return false;
        // Only fields inside a POST form hold data worth protecting. Table
        // search boxes and filter dropdowns live outside forms.
        const form = el.closest('form');
        if (!form || (form.getAttribute('method') || 'get').toLowerCase() === 'get') return false;
        return true;
    }

    // The table search box and filters aren't protected from a refresh, so
    // carry their values across the content swap instead of resetting them.
    const VIEW_CONTROLS = '.smart-search-input, .smart-filter-select';

    function snapshotViewControls() {
        return Array.from(document.querySelectorAll(`.content ${VIEW_CONTROLS}`)).map((el) => el.value);
    }

    function restoreViewControls(values) {
        const els = document.querySelectorAll(`.content ${VIEW_CONTROLS}`);
        els.forEach((el, i) => {
            if (values[i] === undefined || el.value === values[i]) return;
            el.value = values[i];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        });
    }

    function activeFieldInsideContent() {
        return isDataField(document.activeElement);
    }

    let pendingRefresh = false;
    let suppressUntil = 0;
    let retryTimer = null;

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
    // Self-clearing: a field the user touched only counts while its value
    // still differs from what the server rendered, so cancelling a modal,
    // resetting a form or clearing the field unblocks refreshes again
    // (a sticky flag used to block them until a full page reload).
    const touchedFields = new WeakSet();

    function markTouched(e) {
        if (isDataField(e.target)) touchedFields.add(e.target);
    }
    document.addEventListener('input', markTouched, true);
    document.addEventListener('change', markTouched, true);

    function fieldDiffers(el) {
        if (el.type === 'checkbox' || el.type === 'radio') return el.checked !== el.defaultChecked;
        if (el.tagName === 'SELECT') return Array.from(el.options).some((o) => o.selected !== o.defaultSelected);
        return el.value !== el.defaultValue;
    }

    // Forms in .content the user has actually changed.
    function dirtyForms() {
        const content = document.querySelector('.content');
        if (!content) return [];
        const forms = new Set();
        content.querySelectorAll('input, textarea, select').forEach((el) => {
            if (touchedFields.has(el) && isDataField(el) && fieldDiffers(el)) forms.add(el.closest('form'));
        });
        return Array.from(forms);
    }

    function hasUnsavedInput() {
        return dirtyForms().length > 0;
    }

    document.addEventListener('submit', (e) => {
        if (e.target && e.target.closest && e.target.closest('.content')) {
            suppressUntil = Date.now() + 2500;
        }
    }, true);

    // Many sales can land in a burst (and every admin tab hears each one);
    // a full-page refetch per event would hammer the server, so coalesce
    // them into one refresh after things go quiet for a moment.
    let queuedRefresh = null;
    function queueSoftRefresh() {
        clearTimeout(queuedRefresh);
        queuedRefresh = setTimeout(softRefresh, 1500);
    }

    function scheduleRetry(ms) {
        clearTimeout(retryTimer);
        retryTimer = setTimeout(softRefresh, ms);
    }

    function softRefresh() {
        const wait = suppressUntil - Date.now();
        if (wait > 0) {
            // Our own submit just happened; don't drop the update, replay it after.
            pendingRefresh = true;
            scheduleRetry(wait + 100);
            return;
        }
        pendingRefresh = false;

        const viewValues = snapshotViewControls();
        fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
            .then((html) => {
                const fresh = new DOMParser().parseFromString(html, 'text/html');

                // A form the user is part-way through filling in is carried
                // over as-is (same node, so values, listeners and custom
                // selects survive) while the rest of the page refreshes. If
                // such a form can't be matched in the new page, hold the
                // whole refresh back and retry instead of wiping their input.
                const keep = [];
                for (const form of dirtyForms()) {
                    const freshForm = form.id ? fresh.getElementById(form.id) : null;
                    if (!freshForm) {
                        console.debug('[realtime] refresh deferred: unsaved form cannot be preserved');
                        pendingRefresh = true;
                        scheduleRetry(3000);
                        return;
                    }
                    keep.push([form, freshForm]);
                }
                keep.forEach(([form, freshForm]) => freshForm.replaceWith(form));

                swapContent(fresh);
                restoreViewControls(viewValues);
            })
            .catch(() => {
                // Transient failure (network blip, restart): try again shortly.
                pendingRefresh = true;
                scheduleRetry(5000);
            });
    }

    // Safety net: if the socket was down (sleep, Wi-Fi switch, container
    // restart) any pushes were lost, so catch up when it reconnects or the
    // tab becomes visible again.
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') softRefresh();
    });

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
            if (pendingRefresh && !activeFieldInsideContent() && !hasUnsavedInput()) {
                softRefresh();
            }
        }, 0);
    }, true);

    const socket = io();
    // Pushes sent while disconnected are gone for good; refetch on reconnect.
    let hasConnected = false;
    socket.on('connect', () => {
        console.debug('[realtime] connected', socket.id);
        if (hasConnected) softRefresh();
        hasConnected = true;
    });
    socket.on('disconnect', (reason) => console.debug('[realtime] disconnected:', reason));
    socket.on('connect_error', (err) => console.debug('[realtime] connect_error:', err && err.message));
    socket.on('data_changed', (payload) => {
        const scopes = (payload && payload.scopes) || [];
        if (scopes.includes('sales')) refreshTodaySales();
        const mine = currentScopes();
        console.debug('[realtime] data_changed', scopes, 'page listens for', mine);
        if (scopes.some((s) => mine.includes(s))) {
            // A page can register its own in-place refresh (the Reports pages
            // do, so a live update keeps the chosen Daily/Weekly/... view and
            // never wipes the report builder form) instead of a full swap.
            const live = window.__liveRefresh;
            if (live && live.path === window.location.pathname && typeof live.fn === 'function') {
                live.fn();
            } else {
                queueSoftRefresh();
            }
        } else if (scopes.includes('requests') || scopes.includes('partner_inquiries') || scopes.includes('ai_drafts')) {
            refreshBadgesOnly();
        }
    });
    socket.on('bell_notification', (payload) => {
        if (notifBell) notifBell.add(payload);
        if (payload) showToast(payload.message, payload.level);
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

    // One span per character; each second only the characters that
    // actually changed get re-rendered with a short slide-in, so the
    // ticking seconds read as motion without touching the rest.
    let prev = '';
    let cells = [];

    function tick() {
        const next = formatter.format(new Date());
        if (next.length !== prev.length) {
            el.textContent = '';
            cells = Array.from(next).map((ch) => {
                const span = document.createElement('span');
                span.className = 'clock-char';
                span.textContent = ch;
                el.appendChild(span);
                return span;
            });
        } else {
            const animate = !PREFERS_REDUCED_MOTION.matches && document.visibilityState === 'visible';
            Array.from(next).forEach((ch, i) => {
                if (ch === prev[i]) return;
                const old = cells[i];
                const span = document.createElement('span');
                span.className = 'clock-char';
                span.textContent = ch;
                if (animate && /\d/.test(ch)) span.classList.add('clock-tick');
                old.replaceWith(span);
                cells[i] = span;
            });
        }
        prev = next;
    }

    tick();
    setInterval(tick, 1000);
}
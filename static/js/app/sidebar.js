// Sidebar: mobile drawer, tooltips, pin, hover, indicator, search, keyboard nav, accordion
// (Part of the app's main script; loaded in order by base.html.)

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

    // Keyboard focus opens the rail too, but a mouse click on a link must
    // not leave it stuck open, so only :focus-visible counts.
    sidebar.addEventListener('focusin', (e) => {
        if (e.target.matches(':focus-visible')) sidebar.classList.add('is-kbd');
    });
    sidebar.addEventListener('focusout', (e) => {
        if (!sidebar.contains(e.relatedTarget)) sidebar.classList.remove('is-kbd');
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
        if (window.innerWidth > 760 && typeof window.openTopbarSearch === 'function') {
            window.openTopbarSearch();
            return;
        }
        if (window.innerWidth <= 760 && !sidebar.classList.contains('open')) {
            const menuBtn = document.getElementById('menuToggle');
            if (menuBtn) menuBtn.click();
        }
        input.focus();
        input.select();
    });
}

// Top-bar search for laptops and desktops: a full field in the top bar. Focus
// or typing drops a panel of pages built from the sidebar's own links (so the
// two never drift apart). Choosing a result clicks the real sidebar link,
// which keeps soft navigation and the active-page highlight as they were.
function initTopbarSearch() {
    const sidebar = document.getElementById('sidebar');
    const root = document.getElementById('topbarSearch');
    const panel = document.getElementById('topbarSearchPanel');
    const input = document.getElementById('topbarSearchInput');
    const list = document.getElementById('topbarSearchList');
    const empty = document.getElementById('topbarSearchEmpty');
    const kbd = document.getElementById('topbarSearchKbd');
    if (!sidebar || !root || !panel || !input || !list) return;
    if (kbd) kbd.textContent = IS_MAC ? '\u2318K' : 'Ctrl K';

    const seen = new Set();
    const entries = [];
    sidebar.querySelectorAll('.nav-group .nav-link[href]').forEach((link) => {
        const href = link.getAttribute('href');
        if (!href || href === '#' || link.classList.contains('has-subs') || seen.has(href)) return;
        seen.add(href);
        const textEl = link.querySelector('.nav-text');
        const label = (textEl ? textEl.textContent : link.textContent).trim();
        const groupEl = link.closest('.nav-group');
        const gl = groupEl && groupEl.querySelector('.nav-label');
        const group = gl ? ((gl.childNodes[0] && gl.childNodes[0].textContent) || gl.textContent).trim() : '';
        entries.push({ link, label, group, haystack: (label + ' ' + group).toLowerCase() });
    });

    let shown = [];
    let active = -1;

    function render() {
        const raw = input.value.trim();
        const tokens = raw.toLowerCase().split(/\s+/).filter(Boolean);
        shown = entries.filter((e) => tokens.every((t) => e.haystack.includes(t)));
        list.replaceChildren();
        shown.forEach((e, i) => {
            const li = document.createElement('li');
            li.setAttribute('role', 'option');
            li.id = 'topbarSearchOpt' + i;
            const a = document.createElement('a');
            a.href = e.link.getAttribute('href');
            a.tabIndex = -1;
            const name = document.createElement('div');
            const at = tokens.length ? e.label.toLowerCase().indexOf(tokens[0]) : -1;
            if (at >= 0) {
                const mark = document.createElement('mark');
                mark.textContent = e.label.slice(at, at + tokens[0].length);
                name.append(e.label.slice(0, at), mark, e.label.slice(at + tokens[0].length));
            } else {
                name.textContent = e.label;
            }
            const grp = document.createElement('span');
            grp.textContent = e.group;
            a.append(name, grp);
            // pointerdown keeps focus in the field until the click lands
            a.addEventListener('pointerdown', (ev) => ev.preventDefault());
            a.addEventListener('click', (ev) => { ev.preventDefault(); go(e); });
            li.appendChild(a);
            list.appendChild(li);
        });
        if (empty) {
            empty.hidden = shown.length > 0;
            const q = empty.querySelector('span');
            if (q) q.textContent = raw;
        }
        setActive(shown.length ? 0 : -1);
    }

    function setActive(i) {
        active = i;
        list.querySelectorAll('[role="option"]').forEach((li, n) => li.setAttribute('aria-selected', String(n === i)));
        const cur = list.children[i];
        if (cur) {
            input.setAttribute('aria-activedescendant', cur.id);
            cur.scrollIntoView({ block: 'nearest' });
        } else {
            input.removeAttribute('aria-activedescendant');
        }
    }

    function open() {
        if (panel.hidden) {
            panel.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            render();
        }
    }

    function close() {
        if (panel.hidden) return;
        panel.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
    }

    function go(entry) {
        input.value = '';
        close();
        input.blur();
        entry.link.click();
    }

    window.openTopbarSearch = () => { input.focus(); input.select(); open(); };

    input.addEventListener('focus', open);
    input.addEventListener('input', () => { open(); render(); });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            open();
            if (shown.length) setActive((active + 1) % shown.length);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (shown.length) setActive((active - 1 + shown.length) % shown.length);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (shown[active]) go(shown[active]);
        } else if (e.key === 'Escape') {
            e.stopPropagation();
            if (input.value) { input.value = ''; render(); } else { close(); input.blur(); }
        }
    });
    document.addEventListener('pointerdown', (e) => {
        if (!panel.hidden && !e.target.closest('#topbarSearch')) close();
    });
    input.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== input) close(); }, 120));
}

// Installable app (PWA): the service worker is what lets the browser offer
// its own Install option (address-bar icon, menu). Static files only are
// cached, never pages or data (static/sw.js).
function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((err) => {
            console.warn('[pwa] Service worker registration failed:', err);
        });
    }
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


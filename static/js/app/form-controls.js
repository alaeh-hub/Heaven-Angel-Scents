// Custom selects, datalist inputs and date pickers
// (Part of the app's main script; loaded in order by base.html.)

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
        const nextText = text || ' ';
        const changed = labelEl.textContent !== nextText;
        if (changed) labelEl.textContent = nextText;
        // A dependent dropdown filled in by script used to snap from the dim
        // placeholder to the value in one frame, which read as a twitch.
        // Fade the new text in instead (not on first fill, not on a no-op).
        if (changed && labelEl.dataset.csReady === '1') {
            labelEl.classList.remove('cs-label-in');
            void labelEl.offsetWidth;
            labelEl.classList.add('cs-label-in');
        }
        labelEl.dataset.csReady = '1';
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


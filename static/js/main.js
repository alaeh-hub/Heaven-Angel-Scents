/**
 * Keeps the sidebar's scroll position stable across full-page
 * navigations. Every sidebar link is a normal <a href> (a real page
 * load, not an SPA route), so the browser would otherwise repaint the
 * sidebar at scrollTop 0 on every click — invisible on the short
 * Branch sidebar, but a jarring reset on the longer Admin one.
 *
 * Runs immediately (not inside DOMContentLoaded) because this <script>
 * tag sits at the end of <body>, after the sidebar markup — the
 * element already exists, and restoring the scroll position before
 * first paint avoids a visible flash of "scrolled to top, then jumps
 * down". sessionStorage (not localStorage) so it's per-tab and clears
 * itself when the tab closes, and scoped per-origin like everything
 * else here.
 */
(function initSidebarScrollMemory() {
  const sidebar = document.querySelector('.sidebar');
  if (!sidebar) return;

  const STORAGE_KEY = 'sidebarScrollTop';

  const saved = sessionStorage.getItem(STORAGE_KEY);
  if (saved !== null) {
    sidebar.scrollTop = parseInt(saved, 10) || 0;
  }

  // Debounced save on scroll covers dragging the scrollbar, not just
  // clicking a link.
  let saveTimer = null;
  sidebar.addEventListener('scroll', () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      sessionStorage.setItem(STORAGE_KEY, String(sidebar.scrollTop));
    }, 80);
  });

  // Belt-and-suspenders: capture the scroll position the instant a
  // sidebar link is clicked, in case navigation fires before the
  // debounce above has a chance to run.
  sidebar.addEventListener('click', (e) => {
    if (e.target.closest && e.target.closest('a')) {
      sessionStorage.setItem(STORAGE_KEY, String(sidebar.scrollTop));
    }
  });
})();

/**
 * Mobile hamburger drawer for the sidebar. Only relevant below the
 * 760px breakpoint (see style.css) — the toggle button and backdrop
 * are hidden entirely above that, so this just no-ops on desktop.
 */
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

  // Tapping a nav link is about to navigate to a new page anyway, but
  // closing immediately avoids a flash of the open drawer over the
  // outgoing page while that navigation is in flight.
  sidebar.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', closeMenu);
  });

  // If the window is resized past the mobile breakpoint while the
  // drawer happens to be open, drop the "open" state so it doesn't
  // reappear stuck-open if the viewport later narrows again without a
  // fresh click.
  window.addEventListener('resize', () => {
    if (window.innerWidth > 760) closeMenu();
  });
}

/**
 * Light/dark theme toggle. The initial theme is already set as early as
 * possible by a small inline script in <head> (see base.html / login.html)
 * so there's no flash of the wrong theme on load — this only handles the
 * click itself: flip the attribute, persist it, and briefly enable the
 * `.theme-transitioning` CSS rule (style.css) so every color on the page
 * crossfades instead of snapping.
 */
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
      // Private browsing / storage disabled — theme still applies for
      // this page view, it just won't persist to the next one.
    }

    window.setTimeout(() => root.classList.remove('theme-transitioning'), 400);

    if (window.Motion && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      window.Motion.animate(
        btn,
        { transform: ['scale(1)', 'scale(1.15)', 'scale(1)'] },
        { duration: 0.4, easing: [0.34, 1.56, 0.64, 1] }
      );
    }
  });
}

/**
 * A quiet entrance for whatever's in .content — cards, stat tiles, the
 * report builder — so a page load (and a realtime softRefresh(), which
 * calls this again after swapping .content's markup) feels considered
 * rather than an instant hard cut. Skipped entirely if Motion failed to
 * load or the person has asked for reduced motion; either way the
 * content is already visible in normal CSS, so nothing is ever gated
 * behind this running successfully.
 */
function revealContent() {
  if (!window.Motion || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const items = document.querySelectorAll('.content > *');
  if (!items.length) return;

  window.Motion.animate(
    items,
    { opacity: [0, 1], transform: ['translateY(8px)', 'translateY(0px)'] },
    { duration: 0.4, delay: window.Motion.stagger(0.05), easing: [0.16, 1, 0.3, 1] }
  );
}

document.addEventListener('DOMContentLoaded', () => {
  initMobileSidebar();
  initThemeToggle();
  revealContent();
  // Set stock "fill" bar widths from their data-pct attribute. Done here
  // (rather than an inline style="width: {{ pct }}%") so template output
  // never contains raw Jinja inside a style="" attribute.
  document.querySelectorAll('.fill-bar[data-pct]').forEach((el) => {
    const pct = parseFloat(el.dataset.pct) || 0;
    el.style.width = pct + '%';
  });

  // Auto-dismiss toast notifications after a few seconds.
  document.querySelectorAll('.flash').forEach((el) => {
    setTimeout(() => {
      el.classList.add('flash-exit');
      el.addEventListener('animationend', () => el.remove(), { once: true });
    }, 5000);
  });

  // Confirm before status-changing actions that affect other users' access.
  initToggleConfirmations();

  initSmartTables();
  initDispatchQtyWarnings();
  const notifBell = initNotificationBell();
  initRealtime(notifBell);
});

/**
 * Wires the "are you sure?" confirm dialog onto every toggle-style form
 * currently in the DOM (deactivate account, discontinue product, etc).
 * Pulled out into its own function — rather than left inline in the
 * DOMContentLoaded handler — because these forms live inside
 * <div class="content">, and initRealtime()'s softRefresh() replaces
 * that whole div with fresh markup whenever a relevant "data_changed"
 * event arrives. Without re-running this after every soft refresh, a
 * background update would silently strip the confirmation off buttons
 * that deactivate a coworker's login or discontinue a live SKU — the
 * next click would submit immediately, with no dialog and no visible
 * sign anything changed.
 */
function initToggleConfirmations() {
  document.querySelectorAll('form[action*="/toggle"]').forEach((form) => {
    form.addEventListener('submit', (e) => {
      const btn = form.querySelector('button[type="submit"]');
      const label = btn ? btn.textContent.trim() : 'do this';
      if (!confirm(`${label}?`)) e.preventDefault();
    });
  });
}

/**
 * Enforces the Dispatch quantity field on the Stock Requests page:
 * HQ can dispatch less than a branch requested (partial fulfillment,
 * short on stock, etc.) but never more — the request amount is a
 * ceiling, not a suggestion. Going over is blocked outright (native
 * max= validation plus a submit-time guard); going under still just
 * asks for one confirmation naming both numbers, since that's a
 * deliberate, allowed choice rather than a typo.
 *
 * Wire markup like:
 *   <input class="dispatch-qty-input" data-requested-qty="10" max="10" ...>
 * inside the <form> that submits the dispatch.
 */
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

/**
 * Client-side search + pagination for tables that already render every
 * row server-side. Wire up markup like:
 *
 *   <div data-smart-table data-page-size="10" data-count-target="fooCount" data-count-label="row">
 *     <div class="table-toolbar">
 *       <div class="table-search">
 *         <svg>…</svg>
 *         <input type="text" class="smart-search-input" placeholder="Search…">
 *       </div>
 *     </div>
 *     <div class="table-wrap">
 *       <table class="smart-table">
 *         <tbody>
 *           <tr data-search="lowercase searchable text">…</tr>
 *         </tbody>
 *       </table>
 *       <div class="empty-state smart-no-match" style="display:none;">…</div>
 *     </div>
 *     <div class="smart-pagination" style="display:none;">
 *       <button type="button" data-page-prev>← Previous</button>
 *       <span class="smart-page-info"></span>
 *       <button type="button" data-page-next>Next →</button>
 *     </div>
 *   </div>
 *
 * No backend involvement — everything filters/paginates rows already
 * present in the DOM, so it's safe to drop into any existing table.
 *
 * Optional dropdown filter (used alongside search, not instead of it):
 *
 *   <div class="table-filter">
 *     <select class="smart-filter-select" data-filter-key="variant">
 *       <option value="">All variants</option>
 *       <option value="Male">Male</option>
 *     </select>
 *   </div>
 *
 * ...and give each <tr> a matching data-variant="{{ row.variant }}"
 * attribute. data-filter-key names which data-* attribute on the row
 * the select's value is compared against; it defaults to "filter"
 * (i.e. data-filter="...") if omitted.
 */
function initSmartTables() {
  document.querySelectorAll('[data-smart-table]').forEach((container) => {
    const input = container.querySelector('.smart-search-input');
    // Legacy support: a handful of older pages may still carry a
    // data-filter-key dropdown. New markup shouldn't add one — the
    // search box below now matches every visible column on its own, so
    // a separate filter select is redundant and this is only here so
    // an old page that hasn't been touched yet doesn't break.
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

    // Precompute each row's full visible text once up front (every <td>,
    // not just whatever a template author remembered to put in
    // data-search) rather than re-reading the DOM on every keystroke.
    // Falls back to data-search too, in case a row has extra searchable
    // context (e.g. a SKU) that isn't otherwise visible as its own cell.
    const rowText = new Map();
    rows.forEach((r) => {
      const visible = r.textContent.replace(/\s+/g, ' ').trim().toLowerCase();
      const extra = (r.dataset.search || '').toLowerCase();
      rowText.set(r, extra && extra !== visible ? visible + ' ' + extra : visible);
    });

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

/**
 * Notification bell beside the theme toggle in the topbar. Lives in
 * the persistent page shell (base.html), not inside .content, so it
 * survives every soft refresh initRealtime() performs elsewhere —
 * nothing here needs to be re-initialized after a background update.
 *
 * Backed by localStorage, namespaced per signed-in username, so it
 * survives closed tabs, new tabs, and browser restarts — the bell
 * stays populated until the person hits "Clear all" (or the browser's
 * storage is wiped), not just for the current tab session. Namespacing
 * by username keeps two different accounts signed in on the same
 * shared browser from seeing each other's notifications.
 *
 * This is purely a client-side inbox for the one-line alerts the
 * backend pushes over the "bell_notification" socket event (see
 * sockets.py's notify_bell()) — unlike "data_changed", that event is
 * allowed to carry an actual human-readable message, since this is
 * the one place meant to be read directly rather than triggering a
 * background refetch.
 *
 * Returns { add(payload) } so initRealtime() can hand it incoming
 * events, or null if the bell markup isn't present on this page.
 */
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
      // Private browsing / storage disabled — notifications still show
      // for this page view, they just won't persist across a reload.
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

/**
 * Realtime updates: connects to the same Socket.IO server the backend
 * initializes in app.py (see sockets.py for the room-join logic and
 * the notify_*() calls each write route makes). No page ever reloads
 * — instead, on a relevant "data_changed" event this quietly re-fetches
 * its own current URL in the background, then swaps in just the
 * <div class="content"> and the sidebar's "needs action" badges,
 * leaving everything else (scroll position, sidebar state, the
 * Socket.IO connection itself) untouched.
 *
 * "Relevant" is decided entirely on the client: ROUTE_SCOPES maps a
 * URL path to the scope name(s) that page's content depends on (see
 * the scope list documented at the top of sockets.py). If the event's
 * scopes don't overlap with the current page's scopes, it's ignored.
 *
 * Two small safety nets so a background refresh never fights the
 * person actively using the page:
 *   - If focus is currently inside a text/number/select field within
 *     .content (mid-edit), the refresh is deferred until that field
 *     loses focus rather than silently discarding what they typed.
 *   - Right after this tab submits its own form, incoming events are
 *     ignored for a couple seconds — that form's own POST-redirect-GET
 *     is already about to bring a fresh page, so a background refetch
 *     racing it could (rarely) consume that request's own flash
 *     message before the real navigation shows it.
 */
function initRealtime(notifBell) {
  if (typeof io === 'undefined') return;

  const ROUTE_SCOPES = [
    { match: /^\/admin\/production\/?$/, scopes: ['production', 'inventory', 'movement_logs'] },
    { match: /^\/admin\/requests\/?$/, scopes: ['requests'] },
    { match: /^\/admin\/branch-stock\/?$/, scopes: ['inventory'] },
    { match: /^\/admin\/movement-logs\/?$/, scopes: ['movement_logs'] },
    { match: /^\/admin\/products\/?$/, scopes: ['products'] },
    { match: /^\/admin\/branches\/?$/, scopes: ['branches'] },
    { match: /^\/admin\/users\/?$/, scopes: ['users'] },
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

  document.addEventListener('submit', (e) => {
    if (e.target && e.target.closest && e.target.closest('.content')) {
      suppressUntil = Date.now() + 2500;
    }
  }, true);

  function patchSidebarBadges(freshDoc) {
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
    });
  }

  function softRefresh() {
    if (Date.now() < suppressUntil) return;
    if (activeFieldInsideContent()) {
      pendingRefresh = true;
      return;
    }

    fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
      .then((html) => {
        const fresh = new DOMParser().parseFromString(html, 'text/html');

        const freshContent = fresh.querySelector('.content');
        const liveContent = document.querySelector('.content');
        if (freshContent && liveContent) liveContent.replaceWith(freshContent);

        patchSidebarBadges(fresh);

        // Re-run the same setup the new markup would have gotten on a
        // normal page load.
        document.querySelectorAll('.fill-bar[data-pct]').forEach((el) => {
          const pct = parseFloat(el.dataset.pct) || 0;
          el.style.width = pct + '%';
        });
        initSmartTables();
        initDispatchQtyWarnings();
        initToggleConfirmations();
        revealContent();
      })
      .catch(() => {
        // A failed background refresh just stays stale until the next
        // event arrives — surfacing an error for a silent background
        // sync would be more disruptive than the staleness itself.
      });
  }

  function refreshBadgesOnly() {
    // The sidebar's "needs action" badges (Pending Requests for Admin,
    // In Transit for Branch) are always driven by the "requests" scope,
    // regardless of which page is currently open — unlike .content,
    // they shouldn't wait for the current page to also care. This skips
    // the content swap and widget re-init entirely; it only patches the
    // badge counts, so it's safe to fire even while a form field has
    // focus.
    fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
      .then((html) => {
        const fresh = new DOMParser().parseFromString(html, 'text/html');
        patchSidebarBadges(fresh);
      })
      .catch(() => {});
  }

  document.addEventListener('focusout', () => {
    if (pendingRefresh && !activeFieldInsideContent()) {
      pendingRefresh = false;
      softRefresh();
    }
  }, true);

  const socket = io();
  socket.on('data_changed', (payload) => {
    const scopes = (payload && payload.scopes) || [];
    const mine = currentScopes();
    if (scopes.some((s) => mine.includes(s))) {
      softRefresh();
    } else if (scopes.includes('requests')) {
      refreshBadgesOnly();
    }
  });
  socket.on('bell_notification', (payload) => {
    if (notifBell) notifBell.add(payload);
  });
}
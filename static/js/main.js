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
        toggleAnim.then(null, function () {});
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
    revealAnim.then(null, function () {});
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initMobileSidebar();
  initThemeToggle();
  initPhClock();
  revealContent();

  document.querySelectorAll('.fill-bar[data-pct]').forEach((el) => {
    const pct = parseFloat(el.dataset.pct) || 0;
    el.style.width = pct + '%';
  });

  document.querySelectorAll('.flash').forEach((el) => {
    setTimeout(() => {
      el.classList.add('flash-exit');
      el.addEventListener('animationend', () => el.remove(), { once: true });
    }, 5000);
  });

  initToggleConfirmations();

  initSmartTables();
  initDispatchQtyWarnings();
  const notifBell = initNotificationBell();
  initRealtime(notifBell);
});

function initToggleConfirmations() {
  document.querySelectorAll('form[action*="/toggle"]').forEach((form) => {
    form.addEventListener('submit', (e) => {
      const btn = form.querySelector('button[type="submit"]');
      const label = btn ? btn.textContent.trim() : 'do this';
      if (!confirm(`${label}?`)) e.preventDefault();
    });
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

      });
  }

  function refreshBadgesOnly() {

    fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
      .then((html) => {
        const fresh = new DOMParser().parseFromString(html, 'text/html');
        patchSidebarBadges(fresh);
      })
      .catch(() => { });
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
    } else if (scopes.includes('requests') || scopes.includes('partner_inquiries')) {
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
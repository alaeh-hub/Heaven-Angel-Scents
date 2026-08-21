document.addEventListener('DOMContentLoaded', () => {
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
  document.querySelectorAll('form[action*="/toggle"]').forEach((form) => {
    form.addEventListener('submit', (e) => {
      const btn = form.querySelector('button[type="submit"]');
      const label = btn ? btn.textContent.trim() : 'do this';
      if (!confirm(`${label}?`)) e.preventDefault();
    });
  });

  initSmartTables();
  initDispatchQtyWarnings();
});

/**
 * Soft warning for the Dispatch quantity field on the Stock Requests
 * page. Doesn't block anything — HQ can still send more or less than
 * a branch asked for (rounding to a case size, bundling a future
 * request, partial fulfillment, etc.) — it just makes sure that's a
 * deliberate choice rather than a typo: the input gets a warning
 * outline while the value differs from what was requested, and
 * submitting with a mismatch asks for one confirmation naming both
 * numbers before the form actually posts.
 *
 * Wire markup like:
 *   <input class="dispatch-qty-input" data-requested-qty="10" ...>
 * inside the <form> that submits the dispatch.
 */
function initDispatchQtyWarnings() {
  document.querySelectorAll('.dispatch-qty-input').forEach((input) => {
    const requested = parseInt(input.dataset.requestedQty, 10);
    const form = input.closest('form');
    if (Number.isNaN(requested)) return;

    function sync() {
      const val = parseInt(input.value, 10);
      const differs = !Number.isNaN(val) && val !== requested;
      input.classList.toggle('dispatch-qty-diff', differs);
    }

    input.addEventListener('input', sync);
    sync();

    if (form) {
      form.addEventListener('submit', (e) => {
        const val = parseInt(input.value, 10);
        if (Number.isNaN(val) || val === requested) return;
        const comparison = val > requested ? 'more' : 'less';
        const ok = confirm(
          `This branch requested ${requested}. You're about to dispatch ${val} — ${comparison} than what was asked for. Continue?`
        );
        if (!ok) e.preventDefault();
      });
    }
  });

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

    let filtered = rows.slice();
    let page = 1;

    function render() {
      const q = input ? input.value.trim().toLowerCase() : '';
      const fval = filterSelect ? filterSelect.value : '';
      filtered = rows.filter((r) => {
        const matchesSearch = !q || (r.dataset.search || '').indexOf(q) !== -1;
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
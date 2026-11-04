// Dispatch warnings, smart tables and lists
// (Part of the app's main script; loaded in order by base.html.)

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


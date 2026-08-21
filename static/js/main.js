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
});
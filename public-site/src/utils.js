const pesoFormat = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Same output as app.py's `peso` Jinja filter: ₱1,234.50 */
export const peso = (value) => `₱${pesoFormat.format(Number(value) || 0)}`;

/** 12.5 -> "12.5", 10 -> "10" */
export const percent = (value) => String(Number(Number(value || 0).toFixed(2)));

export const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;

export const packagesPath = (slug, scope) =>
  `/partner-portal/${slug}/packages${scope && scope !== 'all' ? `?scope=${encodeURIComponent(scope)}` : ''}`;

export const productsPath = (slug) => `/partner-portal/${slug}/products`;

export const packagePath = (slug, packageId) => `/partner-portal/${slug}/packages/${packageId}`;


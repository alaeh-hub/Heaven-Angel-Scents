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


/**
 * Glide to the landing section `id`, framing its content rather than its
 * outer edge (sections carry 80-150px of top padding, which left a wide
 * empty band under the nav). Content that fits below the nav is centred
 * in that space; taller content starts just below the nav. Returns false
 * when there's no such section.
 */
export function scrollToSection(id) {
  const section = document.getElementById(id);
  if (!section) return false;
  const content = section.querySelector(':scope > .container') || section;
  const box = content.getBoundingClientRect();
  const navH = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--nav-h')) || 64;
  const room = window.innerHeight - navH;
  const offset = box.height <= room - 48 ? navH + (room - box.height) / 2 : navH + 24;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  window.scrollTo({ top: Math.max(0, box.top + window.scrollY - offset), behavior: reduce ? 'auto' : 'smooth' });
  history.replaceState(null, '', `#${id}`);
  return true;
}

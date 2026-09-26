import { forwardRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { motion } from 'motion/react';
import { ArrowRightIcon, PackageIcon, SparkleIcon } from '@phosphor-icons/react';
import { ARRIVE, EASE_OUT, SPRING } from '../motion.js';
import { packagePath, peso, percent, plural } from '../utils.js';
import AnimatedNumber from './AnimatedNumber.jsx';
import ProductVisual from './ProductVisual.jsx';
import ScopeIcon, { scopeLabel } from './ScopeIcon.jsx';

/**
 * One package in the grid. The whole card is the link (the CTA stretches
 * over it). A normal click carries the list along as `background`, which
 * App.jsx uses to open the package in a sheet over the list, plus the
 * card's on-screen box as `origin`, so the sheet can grow out of the
 * card (see DetailDialog). The href is still the package's own URL, so
 * new-tab and sharing work too.
 *
 * When the card first scrolls in, the price tells the deal in order: a
 * line strikes through the list price, then the partner price counts
 * down to its value from that list price.
 *
 * `layout` lets cards glide to their new spots when the filter changes;
 * forwardRef is required for AnimatePresence's popLayout mode.
 */
const PackageCard = forwardRef(function PackageCard({ slug, pkg, index, featured }, ref) {
  const location = useLocation();
  const navigate = useNavigate();
  const profit = pkg.reference_total - pkg.discounted_total;
  const discounted = profit > 0.005;

  // A plain click opens the sheet with the card's box as its origin;
  // modified clicks (new tab, new window) fall through to the href.
  const open = (e) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const card = e.currentTarget.closest('.pkg-card');
    const box = card?.getBoundingClientRect();
    const origin = box && { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    navigate(packagePath(slug, pkg.package_id), { state: { background: location, origin } });
  };

  return (
    <motion.article
      ref={ref}
      layout
      className={`pkg-card glow-card${featured ? ' pkg-card-featured' : ''}`}
      initial={{ opacity: 0, y: 32 }}
      whileInView={{ opacity: 1, y: 0, transition: { ...ARRIVE, delay: index * 0.06 } }}
      viewport={{ once: true, amount: 0.2 }}
      exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.2 } }}
      whileHover={{ y: -6, transition: SPRING }}
      transition={SPRING}
    >
      <div className="pkg-card-top">
        {pkg.previews?.length ? (
          <span className="pkg-bottles" aria-hidden="true">
            {pkg.previews.map((item, i) => (
              <span className="pkg-bottle" key={`${item.item_name}-${i}`} style={{ zIndex: pkg.previews.length - i }}>
                <ProductVisual item={item} alt="" />
              </span>
            ))}
          </span>
        ) : (
          <span className="pkg-icon"><ScopeIcon scope={pkg.partner_scope} /></span>
        )}
        {featured ? (
          <span className="chip chip-gold"><SparkleIcon size={14} weight="fill" /> Best value</span>
        ) : (
          <span className="chip">{scopeLabel(pkg.partner_scope)}</span>
        )}
      </div>

      <h3>{pkg.package_name}</h3>
      <p className="pkg-desc">{pkg.description || 'A curated set of our fragrances.'}</p>
      <div className="pkg-meta">
        <PackageIcon size={16} />
        {plural(pkg.item_count, 'product')}
        {pkg.unit_count > 0 && <>, {plural(pkg.unit_count, 'piece')}</>}
      </div>

      <div className="pkg-price">
        <AnimatedNumber
          className="pkg-price-now"
          value={pkg.discounted_total}
          from={discounted ? pkg.reference_total : undefined}
          whenInView
          duration={1.2}
        />
        <span className="pkg-price-meta">
        {discounted && (
          <span className="pkg-price-was">
            {peso(pkg.reference_total)}
            <motion.span
              className="pkg-price-strike"
              aria-hidden="true"
              initial={{ scaleX: 0 }}
              whileInView={{ scaleX: 1 }}
              viewport={{ once: true, amount: 0.6 }}
              transition={{ duration: 0.45, ease: EASE_OUT, delay: 0.15 }}
            />
          </span>
        )}
        {pkg.discount_percent > 0 && <span className="chip chip-success">Save {percent(pkg.discount_percent)}%</span>}
        </span>
      </div>
      {(pkg.unit_count > 0 || discounted) && (
        <p className="pkg-earn">
          {pkg.unit_count > 0 && <span>{peso(pkg.discounted_total / pkg.unit_count)} per piece</span>}
          {discounted && <span>About <strong>{peso(profit)}</strong> profit at our list price</span>}
        </p>
      )}

      <Link
        to={packagePath(slug, pkg.package_id)}
        state={{ background: location }}
        onClick={open}
        className="btn btn-secondary pkg-cta"
      >
        View package <ArrowRightIcon size={16} weight="bold" />
      </Link>
    </motion.article>
  );
});

export default PackageCard;

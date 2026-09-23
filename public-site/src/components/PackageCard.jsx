import { forwardRef } from 'react';
import { Link, useLocation } from 'react-router';
import { motion } from 'motion/react';
import { ArrowRightIcon, PackageIcon, SparkleIcon } from '@phosphor-icons/react';
import { ARRIVE, SPRING } from '../motion.js';
import { packagePath, peso, percent, plural } from '../utils.js';
import ScopeIcon, { scopeLabel } from './ScopeIcon.jsx';

/**
 * One package in the grid. The whole card is the link (the CTA stretches
 * over it). A normal click carries the list along as `background`, which
 * App.jsx uses to open the package in a sheet over the list; the href is
 * still the package's own URL, so new-tab and sharing work too.
 *
 * `layout` lets cards glide to their new spots when the filter changes;
 * forwardRef is required for AnimatePresence's popLayout mode.
 */
const PackageCard = forwardRef(function PackageCard({ slug, pkg, index, featured }, ref) {
  const location = useLocation();

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
        <span className="pkg-icon"><ScopeIcon scope={pkg.partner_scope} /></span>
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
        {plural(pkg.item_count, 'product')} in this bundle
      </div>

      <div className="pkg-price">
        <span className="pkg-price-now">{peso(pkg.discounted_total)}</span>
        <span className="pkg-price-was">{peso(pkg.reference_total)}</span>
        {pkg.discount_percent > 0 && <span className="chip chip-success">Save {percent(pkg.discount_percent)}%</span>}
      </div>

      <Link
        to={packagePath(slug, pkg.package_id)}
        state={{ background: location }}
        className="btn btn-secondary pkg-cta"
      >
        View package <ArrowRightIcon size={16} weight="bold" />
      </Link>
    </motion.article>
  );
});

export default PackageCard;

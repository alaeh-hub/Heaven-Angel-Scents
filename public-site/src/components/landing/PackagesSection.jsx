import { AnimatePresence, motion } from 'motion/react';
import { PackageIcon } from '@phosphor-icons/react';
import { SPRING } from '../../motion.js';
import PackageCard from '../PackageCard.jsx';
import Reveal from '../Reveal.jsx';
import Segmented from '../Segmented.jsx';
import StatsBand from './StatsBand.jsx';
import Chapter from '../Chapter.jsx';

export default function PackagesSection({ slug, data, loading, scope, onScopeChange }) {
  const packages = data?.packages || [];
  const partnerTypes = data?.partner_types || ['Distributor', 'Reseller'];
  const options = [['all', 'All packages'], ...partnerTypes.map((t) => [t, `${t}s`])];
  const topDiscount = packages.length ? Math.max(...packages.map((p) => p.discount_percent)) : 0;
  const best = packages.length > 1 ? Math.max(...packages.map((p) => p.discount_percent)) : null;
  // The big featured tile only exists with three or more packages: with
  // one or two, every card is the same size, so switching tabs never
  // resizes a card (the old per-tab size flip is what made them twitch).
  const showLarge = packages.length > 2;

  return (
    <section className="section section-alt" id="packages">
      <div className="container">
        <Chapter
          n={1}
          label="Packages"
          title="Packages built for your business."
          lede="Our most-loved scents, bundled for partners who stock in volume."
        />

        <Segmented id="packages" label="Filter packages" options={options} value={scope} onChange={onScopeChange} />

        {!data && loading && (
          <div className="pkg-grid" aria-busy="true" aria-label="Loading packages">
            {[0, 1, 2].map((i) => <div key={i} className="skeleton pkg-skeleton" />)}
          </div>
        )}

        {data && packages.length > 0 && (
          <AnimatePresence mode="wait" initial={false}>
            {/* One grid per result set (keyed by the scope the data is for, so
                it swaps when the new data lands, not on the click). The old set fades out, then the new one
                mounts and its cards rise in one after another. No card
                animates its own size or position, so nothing stretches
                or twitches while the set changes. */}
            <motion.div
              key={data.scope || 'all'}
              className="pkg-grid"
              data-count={packages.length}
              aria-busy={loading}
              exit={{ opacity: 0, transition: { duration: 0.16, ease: 'easeIn' } }}
            >
              {packages.map((pkg, index) => {
                const isBest = best > 0 && pkg.discount_percent === best;
                return (
                  <PackageCard
                    key={pkg.package_id}
                    slug={slug}
                    pkg={pkg}
                    index={index}
                    featured={isBest}
                    large={isBest && showLarge}
                  />
                );
              })}
            </motion.div>
          </AnimatePresence>
        )}

        {data && packages.length === 0 && (
          <motion.div className="empty" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>
            <PackageIcon size={36} weight="duotone" />
            <h3 className="title-lg" style={{ fontSize: 24 }}>Nothing in this view yet.</h3>
            <p>New packages land here as soon as our team publishes them. Try another filter, or check back soon.</p>
          </motion.div>
        )}

        <StatsBand topDiscount={topDiscount} />
      </div>
    </section>
  );
}

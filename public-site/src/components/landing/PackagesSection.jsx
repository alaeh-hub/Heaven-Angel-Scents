import { AnimatePresence, LayoutGroup, motion } from 'motion/react';
import { PackageIcon } from '@phosphor-icons/react';
import { SPRING } from '../../motion.js';
import PackageCard from '../PackageCard.jsx';
import Reveal from '../Reveal.jsx';
import Segmented from '../Segmented.jsx';

export default function PackagesSection({ slug, data, loading, scope, onScopeChange }) {
  const packages = data?.packages || [];
  const partnerTypes = data?.partner_types || ['Distributor', 'Reseller'];
  const options = [['all', 'All packages'], ...partnerTypes.map((t) => [t, `${t}s`])];
  const best = packages.length > 1 ? Math.max(...packages.map((p) => p.discount_percent)) : null;

  return (
    <section className="section section-alt" id="packages">
      <div className="container">
        <Reveal className="section-head center">
          <h2 className="title-xl">Packages built for your business.</h2>
          <p className="lede">Curated bundles of our best-selling scents, priced for partners who buy in volume.</p>
        </Reveal>

        <Segmented id="packages" label="Filter packages" options={options} value={scope} onChange={onScopeChange} />

        {!data && loading && (
          <div className="pkg-grid" aria-busy="true" aria-label="Loading packages">
            {[0, 1, 2].map((i) => <div key={i} className="skeleton pkg-skeleton" />)}
          </div>
        )}

        {data && packages.length > 0 && (
          <LayoutGroup>
            <motion.div className="pkg-grid" layout transition={SPRING} aria-busy={loading}>
              {/* No initial={false}: the first set of cards has to run its
                  own scroll-in reveal (see PackageCard) when data lands. */}
              <AnimatePresence mode="popLayout">
                {packages.map((pkg, index) => (
                  <PackageCard
                    key={pkg.package_id}
                    slug={slug}
                    pkg={pkg}
                    index={index}
                    featured={best > 0 && pkg.discount_percent === best}
                  />
                ))}
              </AnimatePresence>
            </motion.div>
          </LayoutGroup>
        )}

        {data && packages.length === 0 && (
          <motion.div className="empty" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>
            <PackageIcon size={36} weight="duotone" />
            <h3 className="title-lg" style={{ fontSize: 24 }}>Nothing in this view yet.</h3>
            <p>New packages land here as soon as our team publishes them. Try another filter, or check back soon.</p>
          </motion.div>
        )}
      </div>
    </section>
  );
}

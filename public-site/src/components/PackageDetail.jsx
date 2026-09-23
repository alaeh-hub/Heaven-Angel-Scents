import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { LayoutGroup, motion } from 'motion/react';
import { ArrowRightIcon, CheckIcon, PackageIcon, SealPercentIcon } from '@phosphor-icons/react';
import { fetchPackage } from '../api.js';
import { ARRIVE, SPRING } from '../motion.js';
import { packagePath, peso, percent, plural } from '../utils.js';
import Gallery from './detail/Gallery.jsx';
import InquirySheet from './detail/InquirySheet.jsx';
import ProductVisual from './ProductVisual.jsx';
import ScopeIcon, { scopeLabel } from './ScopeIcon.jsx';
import { useToast } from './Toasts.jsx';

const arrive = (delay = 0, y = 20) => ({
  initial: { opacity: 0, y },
  animate: { opacity: 1, y: 0 },
  transition: { ...ARRIVE, delay },
});

function DetailSkeleton() {
  return (
    <div className="detail" aria-busy="true" aria-label="Loading package">
      <div className="container detail-skeleton">
        <div className="skeleton" style={{ height: 28, width: 260, borderRadius: 999 }} />
        <div className="skeleton" style={{ height: 56, width: 'min(560px, 90%)' }} />
        <div className="skeleton" style={{ height: 22, width: 'min(420px, 70%)', marginBottom: 20 }} />
        <div className="row">
          <div className="skeleton" style={{ aspectRatio: '4 / 3', borderRadius: 24 }} />
          <div className="skeleton" style={{ height: 380, borderRadius: 24 }} />
        </div>
      </div>
    </div>
  );
}

function Summary({ pkg, onInquire }) {
  return (
    <div className="summary glow-card">
      <div>
        <div className="summary-label">Your price</div>
        <div className="summary-price">{peso(pkg.discounted_total)}</div>
      </div>
      <div className="summary-was">
        Reference value <s>{peso(pkg.reference_total)}</s>
      </div>
      {pkg.discount_percent > 0 && (
        <span className="chip chip-success" style={{ justifySelf: 'start' }}>
          <SealPercentIcon size={16} weight="fill" /> {percent(pkg.discount_percent)}% off list price
        </span>
      )}
      <motion.button type="button" className="btn btn-primary" onClick={onInquire} whileTap={{ scale: 0.97 }}>
        Inquire about this package
      </motion.button>
      <ul className="summary-points">
        {['No account needed to inquire', 'Our team follows up by phone or email', "The price shown is exactly what you'd pay"].map((point) => (
          <li key={point}><CheckIcon size={16} weight="bold" />{point}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * One package's full detail and inquiry form. Used standalone at the
 * package's own URL (PackageDetailPage) and inside the package sheet
 * over the list (DetailDialog), where `inDialog` keeps "More packages"
 * links inside the sheet. `onMissing` runs when the package is gone
 * (deactivated or deleted), after the visitor has been told so.
 */
export default function PackageDetail({ slug, packageId, inDialog = false, linkState, onMissing }) {
  const showToast = useToast();
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [inquireOpen, setInquireOpen] = useState(false);
  const [[active, dir], setSlide] = useState([0, 0]);
  const csrfToken = useRef('');

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setFailed(false);
    setInquireOpen(false);
    setSlide([0, 0]);
    fetchPackage(slug, packageId, controller.signal)
      .then((result) => {
        csrfToken.current = result.csrf_token;
        setData(result);
      })
      .catch((err) => {
        if (err.name === 'AbortError') return;
        showToast(err.message, 'error');
        if (err.status === 404) onMissing?.();
        else setFailed(true);
      });
    return () => controller.abort();
    // onMissing is a fresh closure per render; only a new package refetches.
  }, [slug, packageId, attempt, showToast]);

  useEffect(() => {
    if (!inDialog && data) document.title = `${data.package.package_name} · Heaven & Angel Scents`;
  }, [inDialog, data]);

  const closeInquiry = useCallback(() => setInquireOpen(false), []);
  const select = useCallback((index, direction) => setSlide([index, direction]), []);

  if (failed) {
    return (
      <div className="detail">
        <div className="container">
          <div className="empty">
            <PackageIcon size={36} weight="duotone" />
            <h3 className="title-lg" style={{ fontSize: 24 }}>We couldn&apos;t load this package.</h3>
            <p>Check your connection, then try again.</p>
            <button type="button" className="btn btn-primary" onClick={() => setAttempt((n) => n + 1)}>Try again</button>
          </div>
        </div>
      </div>
    );
  }

  if (!data) return <DetailSkeleton />;

  const { package: pkg, items, other_packages: others, partner_types: partnerTypes } = data;

  return (
    <div className="detail" key={pkg.package_id}>
      <div className="container">
        <header className="detail-head">
          <motion.div className="detail-chips" {...arrive(0, 12)}>
            <span className="chip chip-gold"><ScopeIcon scope={pkg.partner_scope} size={16} /> {scopeLabel(pkg.partner_scope)}</span>
            <span className="chip"><PackageIcon size={16} /> {plural(items.length, 'product')}</span>
          </motion.div>
          <motion.h1 className="title-xl" {...arrive(0.05)}>{pkg.package_name}</motion.h1>
          <motion.p className="lede" {...arrive(0.1)}>{pkg.description || 'A curated set of our fragrances.'}</motion.p>
        </header>

        <div className="detail-grid">
          <div className="detail-main">
            <motion.div {...arrive(0.12, 28)}>
              {items.length > 0 ? (
                <Gallery items={items} active={active} dir={dir} onSelect={select} />
              ) : (
                <div className="empty">
                  <PackageIcon size={36} weight="duotone" />
                  <p>This package doesn&apos;t list any products yet.</p>
                </div>
              )}
            </motion.div>

            {items.length > 0 && (
              <motion.section {...arrive(0.18, 28)}>
                <h2 className="block-title">What&apos;s inside</h2>
                <LayoutGroup id={`contents-${pkg.package_id}`}>
                  <ul className="contents">
                    {items.map((item, i) => (
                      <li key={i}>
                        <button
                          type="button"
                          className="contents-row"
                          aria-pressed={i === active}
                          onClick={() => select(i, i > active ? 1 : -1)}
                        >
                          {i === active && <motion.span layoutId="contents-active" className="contents-row-bg" transition={SPRING} />}
                          <span className="contents-thumb"><ProductVisual item={item} /></span>
                          <span className="contents-name">
                            <strong>{item.item_name}</strong>
                            <span>{item.unit}</span>
                          </span>
                          <span className="chip">{item.variant}</span>
                          <span className="contents-qty">x{item.qty}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </LayoutGroup>
              </motion.section>
            )}

            {others.length > 0 && (
              <motion.section initial={{ opacity: 0, y: 24 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, amount: 0.3 }} transition={ARRIVE}>
                <h2 className="block-title">More packages</h2>
                <div className="others">
                  {others.map((op) => (
                    <motion.div key={op.package_id} className="other-card glow-card" whileHover={{ y: -4 }} transition={SPRING}>
                      <span className="chip" style={{ justifySelf: 'start' }}>{scopeLabel(op.partner_scope)}</span>
                      <strong>{op.package_name}</strong>
                      <span className="price">{peso(op.discounted_total)}</span>
                      <span className="meta">{plural(op.item_count, 'product')}, {peso(op.reference_total)} reference value</span>
                      <Link
                        to={packagePath(slug, op.package_id)}
                        state={linkState}
                        replace={inDialog}
                        className="link-arrow"
                      >
                        View package <ArrowRightIcon size={15} weight="bold" />
                      </Link>
                    </motion.div>
                  ))}
                </div>
              </motion.section>
            )}
          </div>

          <motion.aside
            className="detail-aside"
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ ...ARRIVE, delay: 0.15 }}
          >
            <Summary pkg={pkg} onInquire={() => setInquireOpen(true)} />
          </motion.aside>
        </div>
      </div>

      <InquirySheet
        slug={slug}
        pkg={pkg}
        partnerTypes={partnerTypes}
        csrfToken={csrfToken}
        open={inquireOpen}
        onClose={closeInquiry}
      />
    </div>
  );
}

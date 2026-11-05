import { useEffect, useMemo, useRef } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ArrowLeftIcon } from '@phosphor-icons/react';
import { useSite } from '../components/InquiryProvider.jsx';
import { SCENTS } from '../content/scents.js';
import { ScentTile, SHELF_VARS } from '../components/landing/Collection.jsx';
import Pagination from '../components/catalog/Pagination.jsx';
import Chapter from '../components/Chapter.jsx';
import Footer from '../components/Footer.jsx';
import Segmented from '../components/Segmented.jsx';
import { ARRIVE, EASE_OUT } from '../motion.js';
import { packagesPath } from '../utils.js';

const GENDERS = [
  ['all', 'All'],
  ['Male', 'Men'],
  ['Female', 'Women'],
];

// Each page of results arrives as a set: the grid fades in and the cards
// follow one after another.
const grid = {
  enter: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.25, staggerChildren: 0.045 } },
  leave: { opacity: 0, transition: { duration: 0.18, ease: EASE_OUT } },
};
const cell = {
  enter: { opacity: 0, y: 24 },
  show: { opacity: 1, y: 0, transition: ARRIVE },
};

/**
 * The full catalog ("View all products" under the collection strip):
 * every scent, filterable by gender, 12 per page. Filter and page live
 * in the URL, so back/forward and shared links land on the same view.
 */
export default function ProductsPage() {
  const { slug } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const gender = searchParams.get('gender') || 'all';
  const page = Math.max(1, Number(searchParams.get('page')) || 1);
  const reduce = useReducedMotion();
  const gridTopRef = useRef(null);
  const { openInquiry } = useSite();

  // The catalog is the same list the home page's collection shows (see
  // content/scents.js), not the inventory table: those renders are the
  // real product. Women's and men's alternate so the labels trade places.
  const list = useMemo(() => {
    const her = SCENTS.filter((x) => x.variant === 'Female');
    const him = SCENTS.filter((x) => x.variant === 'Male');
    if (gender === 'Female') return her;
    if (gender === 'Male') return him;
    const out = [];
    for (let i = 0; i < Math.max(her.length, him.length); i += 1) {
      if (her[i]) out.push(her[i]);
      if (him[i]) out.push(him[i]);
    }
    return out;
  }, [gender]);
  const counts = useMemo(() => ({
    all: SCENTS.length,
    Male: SCENTS.filter((x) => x.variant === 'Male').length,
    Female: SCENTS.filter((x) => x.variant === 'Female').length,
  }), []);

  useEffect(() => {
    document.title = 'The collection · Heaven & Angel Scents';
  }, []);

  const go = (next) => {
    const params = {};
    if (next.gender && next.gender !== 'all') params.gender = next.gender;
    if (next.page > 1) params.page = String(next.page);
    setSearchParams(params, { preventScrollReset: true });
    // Paging from the bottom of the grid: bring its top back into view.
    const top = gridTopRef.current?.getBoundingClientRect().top ?? 0;
    if (top < 0) gridTopRef.current.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  };

  const perPage = 12;
  const pages = Math.max(1, Math.ceil(list.length / perPage));
  const current = Math.min(page, pages);
  const products = list.slice((current - 1) * perPage, current * perPage);
  const options = GENDERS.map(([key, label]) => [key, label, counts[key]]);
  const first = list.length ? (current - 1) * perPage + 1 : 0;
  const last = first + products.length - 1;

  return (
    <div className="page">
      <header className="detail-topbar">
        <div className="container">
          <Link to={packagesPath(slug)} aria-label="Heaven & Angel Scents, back to the partner portal">
            <img src="/static/img/new-wordmark-sm.webp" alt="" />
          </Link>
          <Link to={packagesPath(slug)} className="detail-back" style={{ margin: 0 }}>
            <ArrowLeftIcon size={16} weight="bold" /> Back to portal
          </Link>
        </div>
      </header>

      <Footer>
        <main className="catalog">
          <div className="container">
            <Chapter label="The full range" title="The collection." lede="Every scent we make, in one place. Ask about any of them, or pick a ready-made package." />

            <div ref={gridTopRef} className="catalog-anchor" />
            <Segmented
              id="catalog"
              label="Filter by gender"
              options={options}
              value={gender}
              onChange={(g) => go({ gender: g, page: 1 })}
            />

            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={`${gender}-${current}`}
                className="catalog-grid"
                variants={grid}
                initial="enter"
                animate="show"
                exit="leave"
              >
                {products.map((scent, i) => (
                  <motion.div key={scent.code} className="catalog-item" variants={cell}>
                    <div className="catalog-tile" style={SHELF_VARS}>
                      <ScentTile scent={scent} eager={i < 4} nearby={i < 8} />
                    </div>
                    <button
                      type="button"
                      className="product-card-ask"
                      onClick={() => openInquiry({ message: `I'm interested in ${scent.name} (${scent.variant}).` })}
                    >
                      Ask about this scent
                    </button>
                  </motion.div>
                ))}
              </motion.div>
            </AnimatePresence>

            <div className="catalog-foot">
              <p className="muted">
                Showing {first}-{last} of {list.length} scents
              </p>
              <Pagination page={current} pages={pages} onChange={(p) => go({ gender, page: p })} />
            </div>
          </div>
        </main>
      </Footer>
    </div>
  );
}

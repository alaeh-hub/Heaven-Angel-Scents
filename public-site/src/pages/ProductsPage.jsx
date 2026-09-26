import { useEffect, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ArrowLeftIcon, DropIcon } from '@phosphor-icons/react';
import { useSite } from '../components/InquiryProvider.jsx';
import { fetchProducts } from '../api.js';
import Pagination from '../components/catalog/Pagination.jsx';
import ProductCard from '../components/catalog/ProductCard.jsx';
import Footer from '../components/Footer.jsx';
import Reveal from '../components/Reveal.jsx';
import Segmented from '../components/Segmented.jsx';
import { useToast } from '../components/Toasts.jsx';
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
  const showToast = useToast();
  const reduce = useReducedMotion();
  const gridTopRef = useRef(null);
  const { openInquiry } = useSite();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    document.title = 'The collection · Heaven & Angel Scents';
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetchProducts(slug, { gender, page }, controller.signal)
      .then((result) => {
        setData(result);
        setLoading(false);
      })
      .catch((err) => {
        if (err.name === 'AbortError') return;
        showToast(err.message, 'error');
        setLoading(false);
      });
    return () => controller.abort();
  }, [slug, gender, page, showToast]);

  const go = (next) => {
    const params = {};
    if (next.gender && next.gender !== 'all') params.gender = next.gender;
    if (next.page > 1) params.page = String(next.page);
    setSearchParams(params, { preventScrollReset: true });
    // Paging from the bottom of the grid: bring its top back into view.
    const top = gridTopRef.current?.getBoundingClientRect().top ?? 0;
    if (top < 0) gridTopRef.current.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  };

  const counts = data?.counts;
  const options = GENDERS.map(([key, label]) => [key, label, counts ? counts[key] : undefined]);
  const products = data?.products || [];
  const first = data && data.total ? (data.page - 1) * data.per_page + 1 : 0;
  const last = data ? first + products.length - 1 : 0;

  return (
    <div className="page">
      <header className="detail-topbar">
        <div className="container">
          <Link to={packagesPath(slug)} aria-label="Heaven & Angel Scents, back to the partner portal">
            <img src="/static/img/logo-wordmark.png" alt="" />
          </Link>
          <Link to={packagesPath(slug)} className="detail-back" style={{ margin: 0 }}>
            <ArrowLeftIcon size={16} weight="bold" /> Back to portal
          </Link>
        </div>
      </header>

      <Footer>
        <main className="catalog">
          <div className="container">
            <Reveal className="section-head center catalog-head">
              <h1 className="title-xl">The collection.</h1>
              <p className="lede">Every scent we make, in one place. Ask about any of them, or pick a ready-made package.</p>
            </Reveal>

            <div ref={gridTopRef} className="catalog-anchor" />
            <Segmented
              id="catalog"
              label="Filter by gender"
              options={options}
              value={gender}
              onChange={(g) => go({ gender: g, page: 1 })}
            />

            {!data && loading && (
              <div className="catalog-grid" aria-busy="true" aria-label="Loading products">
                {Array.from({ length: 8 }, (_, i) => <div key={i} className="skeleton catalog-skeleton" />)}
              </div>
            )}

            {data && products.length === 0 && (
              <motion.div className="empty" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
                <DropIcon size={36} weight="duotone" />
                <h3 className="title-lg" style={{ fontSize: 24 }}>No scents here yet.</h3>
                <p>Nothing in the catalog matches this filter right now. Try another one.</p>
              </motion.div>
            )}

            {data && products.length > 0 && (
              <>
                <AnimatePresence mode="wait" initial={false}>
                  <motion.div
                    key={`${data.gender}-${data.page}`}
                    className="catalog-grid"
                    variants={grid}
                    initial="enter"
                    animate="show"
                    exit="leave"
                    aria-busy={loading}
                  >
                    {products.map((product) => (
                      <motion.div key={`${product.item_name}-${product.variant}`} variants={cell}>
                        <ProductCard
                          product={product}
                          onAsk={() => openInquiry({ message: `I'm interested in ${product.item_name} (${product.variant}).` })}
                        />
                      </motion.div>
                    ))}
                  </motion.div>
                </AnimatePresence>

                <div className="catalog-foot">
                  <p className="muted">
                    Showing {first}-{last} of {data.total} {data.total === 1 ? 'scent' : 'scents'}
                  </p>
                  <Pagination page={data.page} pages={data.pages} onChange={(p) => go({ gender, page: p })} />
                </div>
              </>
            )}
          </div>
        </main>
      </Footer>
    </div>
  );
}

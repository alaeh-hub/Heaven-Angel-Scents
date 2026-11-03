import { lazy, Suspense, useEffect } from 'react';
import { Route, Routes, useLocation } from 'react-router';
import { AnimatePresence, motion } from 'motion/react';
import BackToTop from './components/BackToTop.jsx';
import ScrollProgress from './components/ScrollProgress.jsx';
import { InquiryProvider } from './components/InquiryProvider.jsx';
import { ToastProvider } from './components/Toasts.jsx';
import { useGlowPointer } from './hooks/useGlowPointer.js';
import { EASE_OUT } from './motion.js';
import PackagesPage from './pages/PackagesPage.jsx';

// The landing page ships with the app; the pages a visitor may never open
// (the dialog, a package's own page, the full catalog) are separate
// downloads, fetched in the background once the browser is idle so that
// opening one is still instant.
const loadDialog = () => import('./pages/DetailDialog.jsx');
const loadPackagePage = () => import('./pages/PackageDetailPage.jsx');
const loadProducts = () => import('./pages/ProductsPage.jsx');
const DetailDialog = lazy(loadDialog);
const PackageDetailPage = lazy(loadPackagePage);
const ProductsPage = lazy(loadProducts);

function usePrefetchPages() {
  useEffect(() => {
    const run = () => { loadDialog(); loadPackagePage(); loadProducts(); };
    if ('requestIdleCallback' in window) {
      const id = window.requestIdleCallback(run, { timeout: 4000 });
      return () => window.cancelIdleCallback(id);
    }
    const id = setTimeout(run, 2500);
    return () => clearTimeout(id);
  }, []);
}

const LIST = '/partner-portal/:slug/packages';
const DETAIL = '/partner-portal/:slug/packages/:packageId';
const PRODUCTS = '/partner-portal/:slug/products';

/**
 * Flask only serves this app at the two page URLs above, after checking
 * the slug (routes/portal.py) — a wrong slug never reaches React.
 *
 * "Modal route" pattern: a package opened from the list carries the list's
 * location as `state.background`. The page routes then keep rendering
 * that background (the list, untouched) and the package renders on top in
 * a dialog. Opened any other way — a shared link, a new tab — the same
 * URL renders the standalone detail page instead.
 *
 * Moving between pages (list, catalog, standalone detail) cross-fades:
 * the old page fades out, the window resets to the top, the new one
 * fades in. Opacity only: a transform here would re-anchor the fixed
 * backdrop videos and nav sheet to this wrapper. Keyed by pathname, so
 * filters/pagination (search params) and the modal route don't trigger it.
 */
export default function App() {
  const location = useLocation();
  const background = location.state?.background;
  const pageLocation = background || location;
  useGlowPointer();
  usePrefetchPages();

  return (
    <ToastProvider>
      <InquiryProvider>
        <AnimatePresence mode="wait" initial={false} onExitComplete={() => window.scrollTo(0, 0)}>
          <motion.div
            key={pageLocation.pathname}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1, transition: { duration: 0.35, ease: EASE_OUT } }}
            exit={{ opacity: 0, transition: { duration: 0.2, ease: 'easeIn' } }}
          >
            <Suspense fallback={null}>
            <Routes location={pageLocation}>
              <Route path={LIST} element={<PackagesPage />} />
              <Route path={DETAIL} element={<PackageDetailPage />} />
              <Route path={PRODUCTS} element={<ProductsPage />} />
              <Route path="*" element={<p style={{ padding: 40 }}>That page doesn&apos;t exist.</p>} />
            </Routes>
            </Suspense>
          </motion.div>
        </AnimatePresence>
        <ScrollProgress />
        <BackToTop />
        {background && (
          <Suspense fallback={null}>
            <Routes>
              <Route path={DETAIL} element={<DetailDialog />} />
            </Routes>
          </Suspense>
        )}
      </InquiryProvider>
    </ToastProvider>
  );
}

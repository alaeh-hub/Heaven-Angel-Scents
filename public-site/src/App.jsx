import { Route, Routes, useLocation } from 'react-router';
import { AnimatePresence, motion } from 'motion/react';
import BackToTop from './components/BackToTop.jsx';
import { InquiryProvider } from './components/InquiryProvider.jsx';
import { ToastProvider } from './components/Toasts.jsx';
import { useGlowPointer } from './hooks/useGlowPointer.js';
import { EASE_OUT } from './motion.js';
import DetailDialog from './pages/DetailDialog.jsx';
import PackageDetailPage from './pages/PackageDetailPage.jsx';
import PackagesPage from './pages/PackagesPage.jsx';
import ProductsPage from './pages/ProductsPage.jsx';

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
            <Routes location={pageLocation}>
              <Route path={LIST} element={<PackagesPage />} />
              <Route path={DETAIL} element={<PackageDetailPage />} />
              <Route path={PRODUCTS} element={<ProductsPage />} />
              <Route path="*" element={<p style={{ padding: 40 }}>That page doesn&apos;t exist.</p>} />
            </Routes>
          </motion.div>
        </AnimatePresence>
        <BackToTop />
        {background && (
          <Routes>
            <Route path={DETAIL} element={<DetailDialog />} />
          </Routes>
        )}
      </InquiryProvider>
    </ToastProvider>
  );
}

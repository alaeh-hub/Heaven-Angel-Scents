import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate, useParams } from 'react-router';
import { AnimatePresence, motion, useDragControls } from 'motion/react';
import { XIcon } from '@phosphor-icons/react';
import PackageDetail from '../components/PackageDetail.jsx';
import { useBodyScrollLock } from '../hooks/useBodyScrollLock.js';
import { SPRING_SHEET } from '../motion.js';

const DISMISS_PX = 140;
const DISMISS_VELOCITY = 700;

/**
 * The package detail as a sheet that rises over the package list,
 * rendered by App.jsx when the URL is a package's but the navigation
 * carried a `background` location (see PackageCard). The URL is still
 * the package's own, so it can be shared or reloaded as-is.
 *
 * Close with the X, Escape, a tap on the dimmed page, or by dragging the
 * top bar down. The route only changes once the exit animation has
 * finished, so the sheet always gets to slide away.
 */
export default function DetailDialog() {
  const { slug, packageId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(true);
  const dragControls = useDragControls();
  const scrollRef = useRef(null);
  useBodyScrollLock(true);

  // Another package picked from inside the sheet: start it at the top.
  useEffect(() => {
    scrollRef.current?.scrollTo(0, 0);
  }, [packageId]);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && close();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [close]);

  const leave = () => {
    // Opening pushed one history entry and switching packages inside
    // the sheet replaces it, so going back one lands on the list. With
    // nothing to go back to, replace this entry with the list.
    const { background } = location.state;
    if (window.history.state?.idx > 0) navigate(-1);
    else navigate(background.pathname + background.search, { replace: true });
  };

  return createPortal(
    <AnimatePresence onExitComplete={leave}>
      {open && (
        <motion.div
          key="scrim"
          className="sheet-scrim"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
          onClick={close}
        />
      )}
      {open && (
        <motion.div
          key="sheet"
          className="sheet"
          role="dialog"
          aria-modal="true"
          aria-label="Package details"
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          exit={{ y: '100%' }}
          transition={SPRING_SHEET}
          drag="y"
          dragControls={dragControls}
          dragListener={false}
          dragConstraints={{ top: 0, bottom: 0 }}
          dragElastic={{ top: 0, bottom: 0.7 }}
          onDragEnd={(_, info) => {
            if (info.offset.y > DISMISS_PX || info.velocity.y > DISMISS_VELOCITY) close();
          }}
        >
          <div className="sheet-bar" onPointerDown={(e) => dragControls.start(e)}>
            <span className="sheet-grabber" aria-hidden="true" />
            <button
              type="button"
              className="icon-btn"
              aria-label="Close"
              onClick={close}
              onPointerDown={(e) => e.stopPropagation()}
            >
              <XIcon size={16} weight="bold" />
            </button>
          </div>
          <div className="sheet-scroll" ref={scrollRef}>
            <PackageDetail
              slug={slug}
              packageId={packageId}
              inDialog
              linkState={location.state}
              onMissing={close}
            />
          </div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

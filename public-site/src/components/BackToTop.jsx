import { useEffect, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ArrowUpIcon } from '@phosphor-icons/react';
import { SPRING } from '../motion.js';

/** How far down (px) the visitor must be before the button appears. */
const SHOW_AFTER = 600;

/**
 * A floating pill, centered at the bottom of the screen, that glides the
 * page back to the top. Hidden until the visitor has scrolled a screen or
 * so down; sits under dialogs and the mobile detail bar lifts it up.
 */
export default function BackToTop() {
  const reduce = useReducedMotion();
  const [show, setShow] = useState(false);

  useEffect(() => {
    const check = () => setShow(window.scrollY > SHOW_AFTER);
    check();
    window.addEventListener('scroll', check, { passive: true });
    return () => window.removeEventListener('scroll', check);
  }, []);

  const toTop = () => window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });

  return (
    <AnimatePresence>
      {show && (
        <motion.button
          key="to-top"
          type="button"
          className="to-top"
          onClick={toTop}
          aria-label="Back to top"
          initial={{ opacity: 0, y: reduce ? 0 : 16, x: '-50%' }}
          animate={{ opacity: 1, y: 0, x: '-50%' }}
          exit={{ opacity: 0, y: reduce ? 0 : 16, x: '-50%' }}
          transition={reduce ? { duration: 0.15 } : SPRING}
          whileHover={reduce ? undefined : { y: -2 }}
          whileTap={reduce ? undefined : { scale: 0.96 }}
        >
          <ArrowUpIcon size={16} weight="bold" />
          <span>Back to top</span>
        </motion.button>
      )}
    </AnimatePresence>
  );
}

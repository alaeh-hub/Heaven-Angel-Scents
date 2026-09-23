import { useEffect, useState } from 'react';
import { AnimatePresence, motion, useMotionValueEvent, useScroll } from 'motion/react';
import { ListIcon, XIcon } from '@phosphor-icons/react';
import { SPRING } from '../../motion.js';

export const NAV_LINKS = [
  { id: 'about', label: 'About' },
  { id: 'packages', label: 'Packages' },
  { id: 'how-it-works', label: 'How it works' },
  { id: 'faq', label: 'FAQ' },
];

/** Which of `ids` sits across the middle band of the viewport right now. */
function useActiveSection(ids) {
  const [active, setActive] = useState(null);
  useEffect(() => {
    // Tracked as a set so leaving a section (into one that isn't in the
    // nav, like the collection) clears the highlight instead of leaving
    // the last one lit.
    const inBand = new Set();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) inBand.add(entry.target.id);
        else inBand.delete(entry.target.id);
      });
      setActive(ids.find((id) => inBand.has(id)) ?? null);
    }, { rootMargin: '-45% 0px -50% 0px' });
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [ids]);
  return active;
}

const SECTION_IDS = NAV_LINKS.map((l) => l.id);

/**
 * Sticky top bar. Transparent over the hero, frosted once the page moves;
 * slides away while reading downward and returns on any scroll up, so
 * it's there when wanted and out of the way otherwise.
 */
export default function Nav() {
  const { scrollY } = useScroll();
  const [scrolled, setScrolled] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const active = useActiveSection(SECTION_IDS);

  useMotionValueEvent(scrollY, 'change', (y) => {
    const previous = scrollY.getPrevious() ?? 0;
    setScrolled(y > 8);
    setHidden(y > 320 && y > previous + 2 && !menuOpen);
    if (y < previous - 2) setHidden(false);
  });

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (e) => e.key === 'Escape' && setMenuOpen(false);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [menuOpen]);

  return (
    <>
      <motion.header
        className="nav"
        data-scrolled={scrolled || menuOpen}
        animate={{ y: hidden ? '-100%' : '0%' }}
        transition={SPRING}
      >
        <div className="container nav-inner">
          <a className="nav-brand" href="#top" aria-label="Heaven & Angel Scents, back to top">
            <img src="/static/img/logo-wordmark.png" alt="" />
          </a>

          <nav className="nav-links" aria-label="Sections">
            {NAV_LINKS.map(({ id, label }) => (
              <a key={id} href={`#${id}`} className="nav-link" aria-current={active === id}>
                {active === id && <motion.span layoutId="nav-active" className="nav-link-pill" transition={SPRING} />}
                {label}
              </a>
            ))}
          </nav>

          <div className="nav-actions">
            <a href="#packages" className="btn btn-primary btn-sm">View packages</a>
            <button
              type="button"
              className="icon-btn nav-menu-btn"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              {/* The icon turns as it swaps, so open/close reads as one motion. */}
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.span
                  key={menuOpen ? 'close' : 'open'}
                  style={{ display: 'inline-flex' }}
                  initial={{ opacity: 0, rotate: -90, scale: 0.6 }}
                  animate={{ opacity: 1, rotate: 0, scale: 1 }}
                  exit={{ opacity: 0, rotate: 90, scale: 0.6 }}
                  transition={SPRING}
                >
                  {menuOpen ? <XIcon size={18} weight="bold" /> : <ListIcon size={18} weight="bold" />}
                </motion.span>
              </AnimatePresence>
            </button>
          </div>
        </div>
      </motion.header>

      <AnimatePresence>
        {menuOpen && (
          <motion.nav
            className="nav-sheet"
            aria-label="Sections"
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={SPRING}
          >
            {NAV_LINKS.map(({ id, label }, i) => (
              <motion.a
                key={id}
                href={`#${id}`}
                onClick={() => setMenuOpen(false)}
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...SPRING, delay: 0.04 * i }}
              >
                {label}
              </motion.a>
            ))}
          </motion.nav>
        )}
      </AnimatePresence>
    </>
  );
}

import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useMotionValueEvent, useScroll } from 'motion/react';
import { ListIcon, XIcon } from '@phosphor-icons/react';
import { SPRING } from '../../motion.js';
import { scrollToSection } from '../../utils.js';
import { useSite } from '../InquiryProvider.jsx';

export const NAV_LINKS = [
  { id: 'packages', label: 'Packages' },
  { id: 'earnings', label: 'Earnings' },
  { id: 'how-it-works', label: 'How it works' },
  { id: 'about', label: 'About' },
  { id: 'faq', label: 'FAQ' },
];

/**
 * Which of `ids` sits across the middle band of the viewport right now.
 * `lockRef.current` pauses tracking while a nav click scrolls the page, so
 * the pill heads straight for the chosen link instead of stopping at every
 * section it passes.
 */
function useActiveSection(ids, lockRef) {
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
      if (!lockRef.current) setActive(ids.find((id) => inBand.has(id)) ?? null);
    }, { rootMargin: '-45% 0px -50% 0px' });
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [ids, lockRef]);
  return [active, setActive];
}


/** Pixels of upward scrolling it takes to bring the hidden bar back. */
const UP_TO_SHOW = 24;
/**
 * Without `scrollend`: a nav glide counts as finished once the page has
 * been still this long (ms).
 */
const SETTLE_MS = 180;
/** Unlock regardless after this long (ms), in case no end is ever reported. */
const GLIDE_MAX_MS = 4000;

/**
 * Sticky top bar. Transparent over the hero, frosted once the page moves;
 * slides away while reading downward and returns only on a real scroll
 * up, so it's there when wanted and out of the way otherwise.
 */
export default function Nav({ hiddenIds = [] }) {
  const { scrollY } = useScroll();
  const [scrolled, setScrolled] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [hovered, setHovered] = useState(null);
  const navigatingRef = useRef(false);
  const { openInquiry } = useSite();
  // Sections that aren't on the page right now (Earnings, with no
  // discounted package to estimate) get no link, rather than a dead one.
  const hiddenKey = hiddenIds.join(' ');
  const links = useMemo(() => NAV_LINKS.filter((l) => !hiddenKey.split(' ').includes(l.id)), [hiddenKey]);
  // A new list whenever a section appears or goes, so the tracker below
  // re-observes and picks up sections that render after the first load.
  const sectionIds = useMemo(() => links.map((l) => l.id), [links]);
  const [active, setActive] = useActiveSection(sectionIds, navigatingRef);

  // How far the page has moved up since it last moved down.
  const upTravel = useRef(0);

  useMotionValueEvent(scrollY, 'change', (y) => {
    const dy = y - (scrollY.getPrevious() ?? 0);
    setScrolled(y > 8);
    if (y <= 320 || menuOpen) {
      upTravel.current = 0;
      setHidden(false);
      return;
    }
    if (dy > 0) {
      upTravel.current = 0;
      // Stay put while a nav click is carrying the page downward.
      if (!navigatingRef.current) setHidden(true);
    } else if (dy < 0) {
      // Only a deliberate scroll up brings it back: the tiny steps at the
      // tail of a smooth or momentum scroll (and layout nudges) no longer
      // count as "not scrolling down", which kept popping it back in.
      upTravel.current -= dy;
      if (upTravel.current > UP_TO_SHOW) setHidden(false);
    }
  });

  // Ends a nav glide: stops watching the scroll and lets the bar react again.
  const stopWatch = useRef(() => {});
  useEffect(() => () => stopWatch.current(), []);

  /** Light the chosen link at once, then glide to its section. */
  const goTo = (e, id) => {
    // Locked first, so the bar stays put for the whole glide down. It
    // unlocks once the page has settled rather than after a fixed delay,
    // since a long glide (hero to packages) outlasts any fixed guess, and
    // a bar sliding away at the end left a gap above the section.
    stopWatch.current();
    navigatingRef.current = true;
    if (!scrollToSection(id)) {
      navigatingRef.current = false;
      return;
    }
    e.preventDefault();
    setActive(id);
    // `scrollend` fires when the smooth scroll is really done, however
    // slowly the frames arrive; older browsers fall back to "still for
    // SETTLE_MS".
    const hasEnd = 'onscrollend' in window;
    let timer;
    const done = () => stopWatch.current();
    const settle = () => {
      clearTimeout(timer);
      timer = setTimeout(done, SETTLE_MS);
    };
    const cap = setTimeout(done, GLIDE_MAX_MS);
    stopWatch.current = () => {
      clearTimeout(timer);
      clearTimeout(cap);
      window.removeEventListener('scroll', settle);
      window.removeEventListener('scrollend', done);
      navigatingRef.current = false;
      stopWatch.current = () => {};
    };
    if (hasEnd) {
      window.addEventListener('scrollend', done, { once: true });
    } else {
      window.addEventListener('scroll', settle, { passive: true });
      settle();
    }
  };

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

          <nav className="nav-links" aria-label="Sections" onMouseLeave={() => setHovered(null)}>
            {links.map(({ id, label }) => (
              <motion.a
                key={id}
                href={`#${id}`}
                className="nav-link"
                aria-current={active === id}
                onClick={(e) => goTo(e, id)}
                onMouseEnter={() => setHovered(id)}
                onFocus={() => setHovered(id)}
                onBlur={() => setHovered(null)}
                whileTap={{ scale: 0.94 }}
                transition={SPRING}
              >
                {/* A faint hover pill glides between links; the active pill
                    sits above it and springs over when a link is chosen. */}
                <AnimatePresence>
                  {hovered === id && active !== id && (
                    <motion.span
                      layoutId="nav-hover"
                      className="nav-link-hover"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={SPRING}
                    />
                  )}
                </AnimatePresence>
                {active === id && <motion.span layoutId="nav-active" className="nav-link-pill" transition={SPRING} />}
                {label}
              </motion.a>
            ))}
          </nav>

          <div className="nav-actions">
            {/* Packages has its own link beside this, so the one button
                is the other thing a visitor comes to do: get in touch. */}
            <button type="button" className="btn btn-primary btn-sm" onClick={() => openInquiry()}>Send an inquiry</button>
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
            {links.map(({ id, label }, i) => (
              <motion.a
                key={id}
                href={`#${id}`}
                aria-current={active === id}
                onClick={(e) => { setMenuOpen(false); goTo(e, id); }}
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                whileTap={{ x: 6, opacity: 0.7 }}
                transition={{ ...SPRING, delay: 0.04 * i }}
              >
                {label}
              </motion.a>
            ))}
            <motion.button
              type="button"
              className="btn btn-primary nav-sheet-cta"
              onClick={() => { setMenuOpen(false); openInquiry(); }}
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...SPRING, delay: 0.04 * links.length }}
            >
              Send an inquiry
            </motion.button>
          </motion.nav>
        )}
      </AnimatePresence>
    </>
  );
}

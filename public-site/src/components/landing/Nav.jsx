import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useMotionValueEvent, useScroll } from 'motion/react';
import { ArrowUpRightIcon, EnvelopeSimpleIcon, PhoneIcon } from '@phosphor-icons/react';
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock.js';
import { SPRING } from '../../motion.js';
import { scrollToSection } from '../../utils.js';
import { useSite } from '../InquiryProvider.jsx';

export const NAV_LINKS = [
  { id: 'packages', label: 'Packages' },
  { id: 'earnings', label: 'Earnings' },
  { id: 'shop-online', label: 'Shop now' },
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
  const { site, openInquiry } = useSite();
  const contact = site?.contact || {};
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

  useBodyScrollLock(menuOpen);
  useEffect(() => {
    if (!menuOpen) return undefined;
    const mq = window.matchMedia('(min-width: 961px)');
    const close = () => mq.matches && setMenuOpen(false);
    mq.addEventListener('change', close);
    return () => mq.removeEventListener('change', close);
  }, [menuOpen]);

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
            <img src="/static/img/new-wordmark-sm.webp" alt="" />
          </a>

          <nav className="nav-links" aria-label="Sections" onMouseLeave={() => setHovered(null)} onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); e.currentTarget.style.setProperty('--spot-x', `${e.clientX - r.left}px`); }}>
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
              className="nav-menu-btn"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={menuOpen}
              aria-controls="mobile-menu"
              onClick={() => setMenuOpen((open) => !open)}
            >
              {/* Two lines that cross into an X, so open and close read as one motion. */}
              <span className="burger" aria-hidden="true"><i /><i /></span>
            </button>
          </div>
        </div>
      </motion.header>

      <AnimatePresence>
        {menuOpen && (
          <motion.div
            id="mobile-menu"
            className="mobile-menu"
            role="dialog"
            aria-modal="true"
            aria-label="Menu"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
          >
            <nav className="mobile-menu-links" aria-label="Sections">
              {links.map(({ id, label }, i) => (
                <motion.a
                  key={id}
                  href={`#${id}`}
                  aria-current={active === id}
                  onClick={(e) => { setMenuOpen(false); goTo(e, id); }}
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ ...SPRING, delay: 0.05 + 0.045 * i }}
                >
                  <span className="mm-n" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
                  <span className="mm-label">{label}</span>
                  <ArrowUpRightIcon className="mm-arrow" size={18} weight="bold" aria-hidden="true" />
                </motion.a>
              ))}
            </nav>

            <motion.div
              className="mobile-menu-foot"
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...SPRING, delay: 0.1 + 0.045 * links.length }}
            >
              <button type="button" className="btn btn-primary" onClick={() => { setMenuOpen(false); openInquiry(); }}>
                Send an inquiry
              </button>
              {(contact.phone || contact.email) && (
                <div className="mobile-menu-contact">
                  {contact.phone && (
                    <a href={`tel:${contact.phone.replace(/[^\d+]/g, '')}`}><PhoneIcon size={16} weight="duotone" aria-hidden="true" />{contact.phone}</a>
                  )}
                  {contact.email && (
                    <a href={`mailto:${contact.email}`}><EnvelopeSimpleIcon size={16} weight="duotone" aria-hidden="true" />{contact.email}</a>
                  )}
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

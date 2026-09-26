import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { motion, useReducedMotion, useScroll, useTransform } from 'motion/react';
import {
  EnvelopeSimpleIcon, FacebookLogoIcon, InstagramLogoIcon, MapPinIcon,
  MessengerLogoIcon, PhoneIcon, TiktokLogoIcon, XLogoIcon, YoutubeLogoIcon,
} from '@phosphor-icons/react';
import { FOOTER } from '../content/footer.js';
import { packagesPath, productsPath, scrollToSection } from '../utils.js';
import { useSite } from './InquiryProvider.jsx';

const SOCIAL_ICONS = {
  facebook: FacebookLogoIcon,
  instagram: InstagramLogoIcon,
  tiktok: TiktokLogoIcon,
  youtube: YoutubeLogoIcon,
  messenger: MessengerLogoIcon,
  x: XLogoIcon,
};

const EXPLORE = [
  { id: 'packages', label: 'Packages' },
  { id: 'how-it-works', label: 'How it works' },
  { id: 'about', label: 'About the brand' },
  { id: 'faq', label: 'FAQ' },
];

/** A footer taller than this share of the screen doesn't tuck under
    the page: it would have to reveal bottom-first, hiding its top. */
const MAX_REVEAL_SHARE = 0.9;

const clamp01 = (v) => Math.min(1, Math.max(0, v));
/** 0..1 across [from, to] of `v`. */
const span = (v, from, to) => clamp01((v - from) / (to - from));

/**
 * The site footer, as a sticky reveal: it sits still under the page
 * (z-index -1, sticky to the bottom edge) and the content above it
 * slides up to uncover it, while the footer itself fades, rises, scales
 * up and sharpens in step with how much of it is uncovered (useScroll
 * over its own box, 0 as its top reaches the bottom of the screen, 1 at
 * the very end of the page).
 *
 * `children` is what covers it (the closing band on the landing page,
 * the whole page elsewhere). They share one wrapper, which is the
 * sticky footer's containing block, so the footer only waits beneath
 * them and never shows through the see-through sections higher up the
 * page (the ones over the backdrop video). Whatever is passed must be
 * opaque, see .footer-zone in landing.css.
 *
 * Under reduced motion, or when the footer is too tall for the screen
 * (phones), it's an ordinary footer that simply follows the page.
 */
export default function Footer({ children }) {
  const { slug } = useParams();
  const { pathname } = useLocation();
  const { site } = useSite();
  const reduce = useReducedMotion();
  const footerRef = useRef(null);
  const coverRef = useRef(null);
  // Read inside the scroll mapping below, so kept in a ref (no re-render).
  const sizesRef = useRef({ footer: 1, viewport: 1 });
  const [fits, setFits] = useState(true);

  useEffect(() => {
    const footer = footerRef.current;
    if (!footer) return undefined;
    const check = () => {
      sizesRef.current = { footer: footer.offsetHeight || 1, viewport: window.innerHeight || 1 };
      setFits(footer.offsetHeight <= window.innerHeight * MAX_REVEAL_SHARE);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(footer);
    window.addEventListener('resize', check);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', check);
    };
  }, []);

  const reveal = fits && !reduce;

  // How much of the footer is uncovered, 0..1. Measured off the cover,
  // not the footer: a sticky element's measured position moves with it
  // while it's stuck, which threw the progress off. The cover scrolls
  // normally, and the footer is uncovered exactly as far as the cover's
  // bottom edge has risen past the bottom of the screen. That rise, over
  // one screen, is `v` below, and the footer's own height is all of it.
  const { scrollYProgress } = useScroll({ target: coverRef, offset: ['end end', 'end start'] });
  const uncovered = useTransform(scrollYProgress, (v) => {
    const { footer, viewport } = sizesRef.current;
    return clamp01((v * viewport) / footer);
  });
  // Mapped through functions on purpose: given plain ranges, Motion can
  // hand opacity/filter to the browser's native scroll timeline, which
  // measures differently from the transforms (same issue as the hero's
  // useSpan). This way every value follows the one progress above.
  const opacity = useTransform(uncovered, (v) => span(v, 0, 0.7));
  const scale = useTransform(uncovered, (v) => 0.94 + 0.06 * v);
  const y = useTransform(uncovered, (v) => 48 * (1 - v));
  const filter = useTransform(uncovered, (v) => `blur(${(8 * (1 - span(v, 0, 0.8))).toFixed(2)}px)`);

  const onLanding = pathname === packagesPath(slug);
  const phone = site?.contact?.phone || FOOTER.phone;
  const email = site?.contact?.email || FOOTER.email;

  /** On the landing page, glide to the section; elsewhere, go there. */
  const sectionLink = ({ id, label }) => (onLanding ? (
    <a href={`#${id}`} onClick={(e) => { if (scrollToSection(id)) e.preventDefault(); }}>{label}</a>
  ) : (
    <Link to={`${packagesPath(slug)}#${id}`}>{label}</Link>
  ));

  return (
    <div className="footer-zone">
      <div className="footer-cover" ref={coverRef}>{children}</div>
      <footer ref={footerRef} className={`footer${reveal ? ' footer-reveal' : ''}`}>
        {/* Keyed by mode: switching to a plain footer (a phone, or a
            window resized short) remounts it, so none of the reveal's
            last values linger on it. */}
        <motion.div
          key={reveal ? 'reveal' : 'static'}
          className="footer-body"
          style={reveal ? { opacity, scale, y, filter } : undefined}
        >
          <div className="container">
            <div className="footer-grid">
              <div className="footer-brand">
                <img src="/static/img/logo-wordmark.png" alt="Heaven & Angel Scents" />
                <p>{FOOTER.blurb}</p>
                {FOOTER.socials.length > 0 && (
                  <ul className="footer-socials" aria-label="Follow us">
                    {FOOTER.socials.map(({ network, label, href }) => {
                      const Icon = SOCIAL_ICONS[network];
                      if (!Icon) return null;
                      return (
                        <li key={network}>
                          <motion.a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={label}
                            title={label}
                            whileHover={{ y: -3 }}
                            whileTap={{ scale: 0.92 }}
                          >
                            <Icon size={20} weight="fill" />
                          </motion.a>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>

              <nav className="footer-col" aria-label="Explore">
                <h2>Explore</h2>
                <ul>
                  {EXPLORE.map((item) => <li key={item.id}>{sectionLink(item)}</li>)}
                  <li><Link to={productsPath(slug)}>All products</Link></li>
                </ul>
              </nav>

              <div className="footer-col">
                <h2>Contact</h2>
                <ul className="footer-contact">
                  <li>
                    <PhoneIcon size={18} weight="duotone" />
                    <a href={`tel:${phone.replace(/[^\d+]/g, '')}`}>{phone}</a>
                  </li>
                  <li>
                    <EnvelopeSimpleIcon size={18} weight="duotone" />
                    <a href={`mailto:${email}`}>{email}</a>
                  </li>
                  <li>
                    <MapPinIcon size={18} weight="duotone" />
                    <span>
                      {FOOTER.address}
                      {FOOTER.hours && <span className="footer-hours">{FOOTER.hours}</span>}
                    </span>
                  </li>
                </ul>
              </div>
            </div>

            <div className="footer-legal">
              <span>© {new Date().getFullYear()} Heaven &amp; Angel Scents</span>
              <span>This link is shared privately with our partners. Please don&apos;t forward it publicly.</span>
            </div>
          </div>
        </motion.div>
      </footer>
    </div>
  );
}

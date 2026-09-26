import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { animate, motion, useMotionValue, useReducedMotion, useScroll, useTransform } from 'motion/react';
import { ArrowRightIcon } from '@phosphor-icons/react';
import { ARRIVE, SPRING_SOFT } from '../../motion.js';
import { useNarrow } from '../../hooks/useNarrow.js';
import { scrollToSection } from '../../utils.js';
// three.js is large, so the scene loads as its own chunk after the page.
const PerfumeScene = lazy(() => import('../PerfumeScene.jsx'));

const HEADLINE = 'Premium scents, priced for partners.';
/** In-page link handler: frame the section the same way the nav does. */
const jump = (id) => (e) => { if (scrollToSection(id)) e.preventDefault(); };

const LEDE = 'Curated bundles for distributors and resellers, below our regular list prices.';

/** Phones: seconds the whole hero sequence takes when it plays by itself. */
const AUTOPLAY_S = 10;
/** Phones: start anyway if the scene hasn't drawn by then (slow load, no WebGL). */
const AUTOPLAY_FALLBACK_MS = 2500;

/**
 * 0..1 across [from, to] of the scroll. Mapped in a function on purpose:
 * handing plain ranges straight to style lets Motion offload them to the
 * browser's native scroll timeline, which misplaces short sub-ranges
 * like these (words drifted back to blurred further down the scroll).
 */
function useSpan(progress, from, to) {
  return useTransform(progress, (v) => Math.min(1, Math.max(0, (v - from) / (to - from))));
}

/**
 * Wipe reveal at 0..1: a mask that uncovers the element left to right
 * behind a soft 20% edge. Off entirely once fully shown, since a mask
 * also clips anything outside the element's box (the buttons' glow).
 */
function wipeMask(v) {
  if (v >= 1) return 'none';
  const edge = v * 120;
  return `linear-gradient(90deg, #000 ${(edge - 20).toFixed(2)}%, transparent ${edge.toFixed(2)}%)`;
}

/** Style for an element wiped in along `t` (0..1). */
function useWipe(t) {
  const mask = useTransform(t, wipeMask);
  return { maskImage: mask, WebkitMaskImage: mask };
}

/** One headline word, wiped in over [from, to] of the scroll. */
function ScrollWord({ progress, from, to, children }) {
  const wipe = useWipe(useSpan(progress, from, to));
  return <motion.span className="wipe-word" style={wipe}>{children}</motion.span>;
}

/**
 * The opening scene. The hero pins for under one extra screen of
 * scrolling (180vh, see .hero-pin), and the offer and its CTA are fully
 * in place by about 60% of it, i.e. roughly half a screen of scrolling
 * in; the rest is a short hold so the copy can be read before the
 * packages scroll up. It plays like a film strip scrubbed by the scroll:
 *   on load    golden angel wings unfurl under a halo (PerfumeScene)
 *   0.00-0.40  the scene plays: the wings beat and dissolve into streams
 *              of light that build Uriel H1 and Raphael A3, while the
 *              halo splits into the rings of their two lit platforms
 *   0.06-0.16  the wordmark wipes in beneath it
 *   0.30-0.44  the scene moves aside, the wordmark recedes
 *   0.38-0.50  the headline wipes in word by word, left to right
 *   0.48-0.60  the lede, then the buttons, wipe in the same way
 *   0.58-0.75  a gold glow rises from below
 * On phones the same timeline plays by itself over AUTOPLAY_S seconds as
 * soon as the scene has loaded, and the hero doesn't pin (see .hero-pin):
 * the scene, logo and copy sit in one normal, non-overlaid column (see
 * .hero-scene-wrap etc. at max-width: 900px) — the scene stays full size
 * throughout instead of shrinking aside, and the copy simply follows
 * underneath once it's done, rather than overlaid at a fixed spot that a
 * tall headline could spill past. Under reduced motion none of this
 * runs: see HeroStatic.
 */
function HeroPinned() {
  const ref = useRef(null);
  const narrow = useNarrow();
  const narrowRef = useRef(narrow);
  narrowRef.current = narrow;
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end end'] });
  const clock = useMotionValue(0);
  // Both are read every time so both stay tracked if the layout flips.
  const p = useTransform(() => {
    const byTime = clock.get();
    const byScroll = scrollYProgress.get();
    return narrowRef.current ? byTime : byScroll;
  });

  const scene = useSpan(p, 0, 0.4);
  const [sceneReady, setSceneReady] = useState(false);

  // Phones: play the timeline once the scene is on screen (or after the
  // fallback delay, so the copy always arrives).
  const [autoStart, setAutoStart] = useState(false);
  useEffect(() => {
    if (!narrow) return undefined;
    const id = setTimeout(() => setAutoStart(true), AUTOPLAY_FALLBACK_MS);
    return () => clearTimeout(id);
  }, [narrow]);
  useEffect(() => {
    if (!narrow || !(sceneReady || autoStart)) return undefined;
    const run = animate(clock, 1, { duration: AUTOPLAY_S * (1 - clock.get()), ease: 'linear' });
    return () => run.stop();
  }, [narrow, sceneReady, autoStart, clock]);

  const capsIn = useSpan(p, 0.03, 0.08);
  const caps = useTransform(capsIn, (v) => 1 - v);
  const logoIn = useSpan(p, 0.06, 0.16);
  const logoOut = useSpan(p, 0.26, 0.34);
  const logoOpacity = useTransform(logoOut, (v) => 1 - v);
  const logoScale = useTransform(() => 0.96 + 0.04 * logoIn.get() - 0.2 * logoOut.get());
  const logoWipe = useWipe(logoIn);

  // Desktop only: the scene shrinks and slides aside to make room for the
  // copy beside it. On phones the copy sits below the scene instead (see
  // the CSS), so the scene just stays put at full size.
  const stageX = useTransform(p, [0.3, 0.44], ['0vw', '-24vw']);
  const stageScale = useTransform(p, [0.3, 0.44], [1, 0.82]);

  const ledeWipe = useWipe(useSpan(p, 0.48, 0.55));
  const ctaWipe = useWipe(useSpan(p, 0.53, 0.6));
  const ctaEvents = useTransform(p, (v) => (v > 0.55 ? 'auto' : 'none'));
  const glow = useSpan(p, 0.58, 0.75);

  const words = HEADLINE.split(' ');

  return (
    <section className="hero-pin" id="top" ref={ref}>
      <div className="hero-stage">
        {/* Desktop: the glow fills the stage itself, outside the scene's
            box, which shrinks and slides aside (inside it, the glow's
            edges showed as a rectangle once it moved). */}
        {!narrow && <motion.div className="hero-glow-rise" style={{ opacity: glow }} aria-hidden="true" />}
        {/* Scoped to the scene's own box (not the whole stage), so on
            phones — where the stage grows taller than one screen to fit
            the logo and copy below — the glow and eyebrow stay pinned to
            the scene instead of stretching down the whole section. */}
        <motion.div
          className="hero-scene-wrap"
          style={narrow ? undefined : { x: stageX, scale: stageScale }}
        >
          {narrow && <motion.div className="hero-glow-rise" style={{ opacity: glow }} aria-hidden="true" />}

          {/* Outer fades in on load; inner fades out with the scroll. */}
          <motion.div
            className="hero-caps"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ ...ARRIVE, delay: 0.3 }}
          >
            <motion.p style={{ opacity: caps }}>Heaven &amp; Angel Scents</motion.p>
          </motion.div>

          <motion.div
            className="hero-scene"
            aria-hidden="true"
            initial={{ opacity: 0, scale: 0.96 }}
            animate={sceneReady ? { opacity: 1, scale: 1 } : { opacity: 0, scale: 0.96 }}
            transition={{ ...SPRING_SOFT, opacity: { duration: 0.8 } }}
          >
            <Suspense fallback={null}>
              <PerfumeScene progress={scene} onReady={() => setSceneReady(true)} />
            </Suspense>
          </motion.div>
        </motion.div>

        <div className="hero-logo-wrap" aria-hidden="true">
          <motion.img
            src="/static/img/logo-wordmark.png"
            alt=""
            className="hero-logo"
            style={{ opacity: logoOpacity, scale: logoScale, ...logoWipe }}
          />
        </div>

        <div className="hero-copy-wrap">
          <div className="container">
            <div className="hero-copy">
              <h1 className="hero-title" aria-label={HEADLINE}>
                {words.map((word, i) => (
                  <span key={i} aria-hidden="true">
                    <ScrollWord progress={p} from={0.38 + i * 0.016} to={0.43 + i * 0.016}>{word}</ScrollWord>
                    {i < words.length - 1 && ' '}
                  </span>
                ))}
              </h1>
              <motion.p className="lede" style={ledeWipe}>{LEDE}</motion.p>
              <motion.div className="hero-ctas" style={{ ...ctaWipe, pointerEvents: ctaEvents }}>
                <motion.a href="#packages" onClick={jump('packages')} className="btn btn-primary" whileTap={{ scale: 0.97 }}>
                  View packages
                </motion.a>
                <a href="#how-it-works" onClick={jump('how-it-works')} className="link-arrow">
                  How it works <ArrowRightIcon size={16} weight="bold" />
                </a>
              </motion.div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

/** Reduced motion: the offer and the bottles, all visible, nothing pinned. */
function HeroStatic() {
  return (
    <section className="hero-static" id="top">
      <div className="container hero-static-grid">
        <div className="hero-copy hero-copy-static">
          <h1 className="hero-title">{HEADLINE}</h1>
          <p className="lede">{LEDE}</p>
          <div className="hero-ctas">
            <a href="#packages" onClick={jump('packages')} className="btn btn-primary">View packages</a>
            <a href="#how-it-works" onClick={jump('how-it-works')} className="link-arrow">
              How it works <ArrowRightIcon size={16} weight="bold" />
            </a>
          </div>
        </div>
        <div className="hero-scene hero-scene-static" aria-hidden="true">
          <Suspense fallback={null}>
            {/* No progress: it holds the finished bottles. */}
            <PerfumeScene />
          </Suspense>
        </div>
      </div>
    </section>
  );
}

export default function Hero() {
  const reduce = useReducedMotion();
  return reduce ? <HeroStatic /> : <HeroPinned />;
}

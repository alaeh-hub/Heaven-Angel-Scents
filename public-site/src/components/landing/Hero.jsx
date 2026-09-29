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

/** Phones: seconds the scene takes when it plays by itself. */
const AUTOPLAY_S = 7;
/** Phones: start anyway if the scene hasn't drawn by then (slow load, no WebGL). */
const AUTOPLAY_FALLBACK_MS = 2500;
/** Seconds the offer takes to wipe in on load, and how long it waits first. */
const INTRO_S = 1.4;
const INTRO_DELAY_S = 0.25;

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

/** One headline word, wiped in over [from, to] of the intro. */
function WipeWord({ progress, from, to, children }) {
  const wipe = useWipe(useSpan(progress, from, to));
  return <motion.span className="wipe-word" style={wipe}>{children}</motion.span>;
}

/**
 * The opening scene. The offer (headline, lede, buttons) is there from
 * the first second: it wipes in on load, on a short clock of its own,
 * beside the scene rather than after it, so a partner opening a shared
 * link knows what the page is before they scroll at all.
 *
 * The scene is what the scroll plays. On desktop the hero pins for a
 * little over half an extra screen (160vh, see .hero-pin) and scrubs it
 * like a film strip:
 *   on load    golden angel wings unfurl under a halo (PerfumeScene)
 *   0.00-0.85  the wings beat and dissolve into streams of light that
 *              build Uriel H1 and Raphael A3, while the halo splits into
 *              the rings of their two lit platforms
 *   0.03-0.08  the brand line over the scene fades away
 *   0.60-0.95  a gold glow rises from below
 * On phones the hero doesn't pin (see .hero-pin): the scene sits above
 * the copy in one normal column and plays by itself over AUTOPLAY_S
 * seconds once it has loaded. Under reduced motion none of this runs:
 * see HeroStatic.
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

  const scene = useSpan(p, 0, 0.85);
  const [sceneReady, setSceneReady] = useState(false);

  // Phones: play the scene once it is on screen (or after the fallback
  // delay, so a slow or missing scene never holds anything up).
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

  // The offer's own clock: runs once on load, whatever the scroll does.
  const intro = useMotionValue(0);
  useEffect(() => {
    const run = animate(intro, 1, { duration: INTRO_S, delay: INTRO_DELAY_S, ease: 'linear' });
    return () => run.stop();
  }, [intro]);

  const capsOut = useSpan(p, 0.03, 0.08);
  const caps = useTransform(capsOut, (v) => 1 - v);

  const ledeWipe = useWipe(useSpan(intro, 0.45, 0.75));
  const ctaWipe = useWipe(useSpan(intro, 0.65, 1));
  const glow = useSpan(p, 0.6, 0.95);

  const words = HEADLINE.split(' ');
  const wordStep = 0.45 / words.length;

  return (
    <section className="hero-pin" id="top" ref={ref}>
      <div className="hero-stage">
        {/* Desktop: the glow fills the stage itself, outside the scene's
            box, which sits off to one side (inside it, the glow's edges
            showed as a rectangle). */}
        {!narrow && <motion.div className="hero-glow-rise" style={{ opacity: glow }} aria-hidden="true" />}
        {/* Scoped to the scene's own box (not the whole stage), so on
            phones, where the stage grows taller than one screen to fit
            the copy below, the glow and brand line stay pinned to the
            scene instead of stretching down the whole section. */}
        <div className="hero-scene-wrap">
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
        </div>

        <div className="hero-copy-wrap">
          <div className="container">
            <div className="hero-copy">
              <h1 className="hero-title" aria-label={HEADLINE}>
                {words.map((word, i) => (
                  <span key={i} aria-hidden="true">
                    <WipeWord progress={intro} from={i * wordStep} to={i * wordStep + 0.2}>{word}</WipeWord>
                    {i < words.length - 1 && ' '}
                  </span>
                ))}
              </h1>
              <motion.p className="lede" style={ledeWipe}>{LEDE}</motion.p>
              <motion.div className="hero-ctas" style={ctaWipe}>
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

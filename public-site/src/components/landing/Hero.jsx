import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { animate, motion, useMotionValue, useReducedMotion, useScroll, useTransform } from 'motion/react';
import { ArrowRightIcon } from '@phosphor-icons/react';
import { ARRIVE, SPRING_SOFT } from '../../motion.js';
import { useNarrow } from '../../hooks/useNarrow.js';
// three.js is large, so the scene loads as its own chunk after the page.
const PerfumeScene = lazy(() => import('../PerfumeScene.jsx'));

const HEADLINE = 'Partner with Heaven & Angel Scents.';
const LEDE = 'Premium fragrances, priced below wholesale. Curated bundles for distributors and resellers.';

/** Phones: seconds the whole hero sequence takes when it plays by itself. */
const AUTOPLAY_S = 9;
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

/** One headline word, sharpening out of a blur over [from, to] of the scroll. */
function ScrollWord({ progress, from, to, children }) {
  const t = useSpan(progress, from, to);
  const y = useTransform(t, (v) => (1 - v) * 14);
  const filter = useTransform(t, (v) => (v >= 1 ? 'none' : `blur(${((1 - v) * 10).toFixed(2)}px)`));
  return <motion.span className="blur-word" style={{ opacity: t, y, filter }}>{children}</motion.span>;
}

/**
 * The opening scene. The hero pins for a few screens of scrolling and
 * plays like a film strip scrubbed by the scroll:
 *   on load    two perfume droplets glow in out of the dark (PerfumeScene)
 *   0.00-0.56  the scene plays: a stream pours into each droplet, they
 *              land and rise into Uriel H1 and Raphael A3, and two lit
 *              platforms lift the finished bottles
 *   0.10-0.24  the wordmark sharpens in beneath it
 *   0.48-0.62  the scene moves aside, the wordmark recedes
 *   0.58-0.72  the headline arrives word by word, each out of a blur
 *   0.70-0.84  the lede, then the buttons
 *   0.82-0.95  a gold glow rises from below
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

  const scene = useSpan(p, 0, 0.56);
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

  const capsIn = useSpan(p, 0.04, 0.1);
  const caps = useTransform(capsIn, (v) => 1 - v);
  const logoIn = useSpan(p, 0.1, 0.24);
  const logoOut = useSpan(p, 0.38, 0.48);
  const logoOpacity = useTransform(() => logoIn.get() * (1 - logoOut.get()));
  const logoScale = useTransform(() => 0.96 + 0.04 * logoIn.get() - 0.2 * logoOut.get());
  const logoFilter = useTransform(logoIn, (v) => (v >= 1 ? 'none' : `blur(${((1 - v) * 6).toFixed(2)}px)`));

  // Desktop only: the scene shrinks and slides aside to make room for the
  // copy beside it. On phones the copy sits below the scene instead (see
  // the CSS), so the scene just stays put at full size.
  const stageX = useTransform(p, [0.48, 0.62], ['0vw', '-24vw']);
  const stageScale = useTransform(p, [0.48, 0.62], [1, 0.82]);

  const ledeOpacity = useSpan(p, 0.7, 0.78);
  const ledeY = useTransform(ledeOpacity, (v) => (1 - v) * 16);
  const ctaOpacity = useSpan(p, 0.76, 0.84);
  const ctaY = useTransform(ctaOpacity, (v) => (1 - v) * 16);
  const ctaEvents = useTransform(p, (v) => (v > 0.78 ? 'auto' : 'none'));
  const glow = useSpan(p, 0.82, 0.95);

  const words = HEADLINE.split(' ');

  return (
    <section className="hero-pin" id="top" ref={ref}>
      <div className="hero-stage">
        {/* Scoped to the scene's own box (not the whole stage), so on
            phones — where the stage grows taller than one screen to fit
            the logo and copy below — the glow and eyebrow stay pinned to
            the scene instead of stretching down the whole section. */}
        <motion.div
          className="hero-scene-wrap"
          style={narrow ? undefined : { x: stageX, scale: stageScale }}
        >
          <motion.div className="hero-glow-rise" style={{ opacity: glow }} aria-hidden="true" />

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
            style={{ opacity: logoOpacity, scale: logoScale, filter: logoFilter }}
          />
        </div>

        <div className="hero-copy-wrap">
          <div className="container">
            <div className="hero-copy">
              <h1 className="hero-title" aria-label={HEADLINE}>
                {words.map((word, i) => (
                  <span key={i} aria-hidden="true">
                    <ScrollWord progress={p} from={0.58 + i * 0.022} to={0.64 + i * 0.022}>{word}</ScrollWord>
                    {i < words.length - 1 && ' '}
                  </span>
                ))}
              </h1>
              <motion.p className="lede" style={{ opacity: ledeOpacity, y: ledeY }}>{LEDE}</motion.p>
              <motion.div className="hero-ctas" style={{ opacity: ctaOpacity, y: ctaY, pointerEvents: ctaEvents }}>
                <motion.a href="#packages" className="btn btn-primary" whileTap={{ scale: 0.97 }}>
                  View packages
                </motion.a>
                <a href="#how-it-works" className="link-arrow">
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
            <a href="#packages" className="btn btn-primary">View packages</a>
            <a href="#how-it-works" className="link-arrow">
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

import { lazy, Suspense, useRef, useState } from 'react';
import {
  AnimatePresence,
  motion,
  useMotionValueEvent,
  useReducedMotion,
  useScroll,
  useTransform,
} from 'motion/react';
import { SPRING } from '../../motion.js';
import BlurText from '../BlurText.jsx';
// Same chunk as the hero's scene (three.js), loaded after the page.
const PerfumeScene = lazy(() => import('../PerfumeScene.jsx'));

// General perfumery, not a claim about any one scent: the notes listed
// are common examples of each layer.
const STAGES = [
  {
    name: 'Top notes',
    when: 'First 15 minutes',
    title: 'The first impression.',
    body: 'Bright, quick notes greet you the moment it touches skin, then lift away to make room for what comes next.',
    notes: ['Bergamot', 'Lemon', 'Pink pepper'],
  },
  {
    name: 'Heart notes',
    when: '2 to 4 hours',
    title: 'The character.',
    body: 'As the opening fades, the heart rises. This is the personality of the scent, the part people remember.',
    notes: ['Rose', 'Jasmine', 'Lavender'],
  },
  {
    name: 'Base notes',
    when: '6 hours and beyond',
    title: 'What stays.',
    body: 'Deep, slow notes settle in last and linger on skin and fabric long after the rest has gone.',
    notes: ['Sandalwood', 'Amber', 'Musk'],
  },
];

// The glow behind the bottles warms as the scent deepens: champagne, rose
// gold, then amber. All three stay in the brand's gold family.
const TONES = ['#ecd68c', '#e2aa96', '#be803a'];

/**
 * "How a fragrance unfolds": a pinned stage that walks through the three
 * layers of a perfume as the visitor scrolls. Uriel H1 and Raphael A3
 * turn together on their lit platforms (PerfumeScene's turntable, one
 * full turn every 10 s), the glow behind them warms through the layers,
 * and each layer's example notes drift in around them. The layer names
 * double as buttons that jump to that point. Under reduced motion
 * nothing pins; all three layers are simply listed.
 */
export default function ScentJourney() {
  const ref = useRef(null);
  const reduce = useReducedMotion();
  const [stage, setStage] = useState(0);

  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end end'] });
  const tone = useTransform(scrollYProgress, [0.1, 0.9], [0, 1]);
  const [sceneReady, setSceneReady] = useState(false);

  useMotionValueEvent(scrollYProgress, 'change', (v) => {
    setStage(Math.min(STAGES.length - 1, Math.floor(v * STAGES.length)));
  });

  /** Scroll to the middle of stage `i`'s share of the pinned distance. */
  const jumpTo = (i) => {
    const el = ref.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY;
    const travel = el.offsetHeight - window.innerHeight;
    window.scrollTo({ top: top + travel * ((i + 0.5) / STAGES.length), behavior: 'smooth' });
  };

  if (reduce) {
    return (
      <section className="section journey-static" id="notes">
        <div className="container">
          <div className="section-head">
            <BlurText className="title-xl" text="How a fragrance unfolds." />
            <p className="lede">A good perfume changes as it wears. Here is what your customers notice, hour by hour.</p>
          </div>
          <ol className="journey-static-list">
            {STAGES.map((s) => (
              <li key={s.name}>
                <span className="journey-when">{s.name}, {s.when.toLowerCase()}</span>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
                <p className="journey-notes-inline">{s.notes.join(', ')}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>
    );
  }

  const current = STAGES[stage];

  return (
    <section className="journey" id="notes" ref={ref}>
      <div className="journey-stage">
        <div className="container journey-grid">
          <div className="journey-copy">
            <BlurText className="title-xl" text="How a fragrance unfolds." />
            <p className="lede journey-lede">
              A good perfume changes as it wears. Here is what your customers notice, hour by hour.
            </p>

            <div className="journey-tabs" aria-label="Layers of a fragrance">
              {STAGES.map((s, i) => (
                <button
                  key={s.name}
                  type="button"
                  className="journey-tab"
                  aria-current={stage === i}
                  onClick={() => jumpTo(i)}
                >
                  {stage === i && <motion.span layoutId="journey-tab" className="journey-tab-pill" transition={SPRING} />}
                  {s.name}
                </button>
              ))}
            </div>

            {/* All three blocks share one grid cell, so swapping them never
                shifts the layout; only the active one is visible. */}
            <div className="journey-text" aria-live="polite">
              {STAGES.map((s, i) => (
                <motion.div
                  key={s.name}
                  className="journey-block"
                  aria-hidden={stage !== i}
                  initial={false}
                  animate={stage === i ? { opacity: 1, y: 0 } : { opacity: 0, y: i < stage ? -18 : 18 }}
                  transition={SPRING}
                >
                  <span className="journey-when">{s.when}</span>
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </motion.div>
              ))}
            </div>
          </div>

          <div className="journey-visual" aria-hidden="true">
            <motion.div
              className="journey-scene"
              initial={{ opacity: 0 }}
              animate={{ opacity: sceneReady ? 1 : 0 }}
              transition={{ duration: 0.8 }}
            >
              <Suspense fallback={null}>
                <PerfumeScene variant="turntable" tone={tone} tones={TONES} onReady={() => setSceneReady(true)} />
              </Suspense>
            </motion.div>
            <AnimatePresence mode="popLayout">
              {current.notes.map((note, i) => (
                <motion.span
                  key={`${stage}-${note}`}
                  className={`journey-note journey-note-${i}`}
                  initial={{ opacity: 0, y: 14, scale: 0.92 }}
                  animate={{ opacity: 1, y: 0, scale: 1, transition: { ...SPRING, delay: 0.08 * i } }}
                  exit={{ opacity: 0, y: -14, scale: 0.96, transition: { duration: 0.2 } }}
                >
                  {note}
                </motion.span>
              ))}
            </AnimatePresence>
          </div>
        </div>
      </div>
    </section>
  );
}

import { lazy, Suspense, useEffect, useId, useState } from 'react';
import { animate, AnimatePresence, motion, useMotionValue, useReducedMotion } from 'motion/react';
import { LAYERS, SCENTS } from '../../content/notes.js';
import { SPRING } from '../../motion.js';
// Same chunk as the hero's scene (three.js), loaded after the page.
const PerfumeScene = lazy(() => import('../PerfumeScene.jsx'));

const TITLE = 'How our scents unfold.';
const LEDE = 'Uriel H1 and Raphael A3 change as they wear. Here is what your customers notice, hour by hour.';

// The glow behind the bottles warms as the scent deepens: champagne, rose
// gold, then amber. All three stay in the brand's gold family.
const TONES = ['#ecd68c', '#e2aa96', '#be803a'];

/** Each scent's notes for one layer, as a small definition list. */
function LayerNotes({ layer }) {
  return (
    <dl className="journey-notes">
      {SCENTS.map(({ key, name }) => (
        <div key={key}>
          <dt>{name}</dt>
          <dd>{layer.notes[key].join(', ')}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * "How our scents unfold": the three layers of Uriel H1 and Raphael A3,
 * one at a time, picked with the tabs (no scroll pinning: the hero is
 * the page's one pinned moment). The pair turns on their lit platforms
 * (PerfumeScene's turntable), the glow behind them eases to the chosen
 * layer's tone, and each bottle's lead note for that layer floats over
 * it. Copy lives in content/notes.js. Under reduced motion all three
 * layers are simply listed.
 */
export default function ScentJourney() {
  const reduce = useReducedMotion();
  const [stage, setStage] = useState(0);
  const [sceneReady, setSceneReady] = useState(false);
  const tone = useMotionValue(0);
  const panelId = useId();

  useEffect(() => {
    const run = animate(tone, stage / (LAYERS.length - 1), { duration: 0.9, ease: 'easeInOut' });
    return () => run.stop();
  }, [stage, tone]);

  if (reduce) {
    return (
      <section className="section journey-static" id="notes">
        <div className="container">
          <div className="section-head">
            <h2 className="title-xl">{TITLE}</h2>
            <p className="lede">{LEDE}</p>
          </div>
          <ol className="journey-static-list">
            {LAYERS.map((layer) => (
              <li key={layer.name}>
                <span className="journey-when">{layer.name}, {layer.when.toLowerCase()}</span>
                <h3>{layer.title}</h3>
                <p>{layer.body}</p>
                <LayerNotes layer={layer} />
              </li>
            ))}
          </ol>
        </div>
      </section>
    );
  }

  const current = LAYERS[stage];

  return (
    <section className="section journey" id="notes">
      <div className="container journey-grid">
        <div className="journey-copy">
          <h2 className="title-xl">{TITLE}</h2>
          <p className="lede journey-lede">{LEDE}</p>

          <div className="journey-tabs" role="tablist" aria-label="Layers of a fragrance">
            {LAYERS.map((layer, i) => (
              <button
                key={layer.name}
                type="button"
                role="tab"
                className="journey-tab"
                aria-selected={stage === i}
                aria-controls={panelId}
                onClick={() => setStage(i)}
              >
                {stage === i && <motion.span layoutId="journey-tab" className="journey-tab-pill" transition={SPRING} />}
                {layer.name}
              </button>
            ))}
          </div>

          {/* All three blocks share one grid cell, so swapping them never
              shifts the layout; only the active one is visible. */}
          <div className="journey-text" id={panelId} role="tabpanel" aria-live="polite">
            {LAYERS.map((layer, i) => (
              <motion.div
                key={layer.name}
                className="journey-block"
                aria-hidden={stage !== i}
                initial={false}
                animate={stage === i ? { opacity: 1, y: 0 } : { opacity: 0, y: i < stage ? -18 : 18 }}
                transition={SPRING}
              >
                <span className="journey-when">{layer.when}</span>
                <h3>{layer.title}</h3>
                <p>{layer.body}</p>
                <LayerNotes layer={layer} />
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
          {/* One pill over each bottle: its lead note for this layer. */}
          <AnimatePresence mode="popLayout">
            {SCENTS.map(({ key }, i) => (
              <motion.span
                key={`${stage}-${key}`}
                className={`journey-note journey-note-${i}`}
                initial={{ opacity: 0, y: 14, scale: 0.92 }}
                animate={{ opacity: 1, y: 0, scale: 1, transition: { ...SPRING, delay: 0.08 * i } }}
                exit={{ opacity: 0, y: -14, scale: 0.96, transition: { duration: 0.2 } }}
              >
                {current.notes[key][0]}
              </motion.span>
            ))}
          </AnimatePresence>
        </div>
      </div>
    </section>
  );
}

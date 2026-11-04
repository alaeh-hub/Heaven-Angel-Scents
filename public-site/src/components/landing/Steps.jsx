import { useRef } from 'react';
import { motion, useScroll, useSpring } from 'motion/react';
import { useNarrow } from '../../hooks/useNarrow.js';
import Chapter from '../Chapter.jsx';
import Reveal from '../Reveal.jsx';

const STEPS = [
  {
    title: 'Browse the packages',
    body: 'Every bundle, priced in plain sight and veiled by nothing.',
  },
  {
    title: 'Send an inquiry',
    body: "One message from you, and we welcome you as a partner.",
  },
  {
    title: 'Reorder anytime',
    body: 'Restock in minutes, whenever your shelves ask for more.',
  },
];

/**
 * The heading on top, then the three steps as a timeline: side by side
 * on desktop, stacked on phones. A gold rail through the step markers
 * fills as the visitor reads through them (left to right, or top to
 * bottom on phones), so progress through the process is visible at a
 * glance. Sits right after the packages and the earnings estimate,
 * since it answers "what happens after I click Inquire?".
 */
export default function Steps() {
  const listRef = useRef(null);
  const narrow = useNarrow();
  const { scrollYProgress } = useScroll({
    target: listRef,
    offset: narrow ? ['start 0.75', 'end 0.55'] : ['start 0.85', 'end 0.6'],
  });
  const fill = useSpring(scrollYProgress, { stiffness: 200, damping: 40, restDelta: 0.001 });

  return (
    <section className="section" id="how-it-works">
      <div className="container">
        <Chapter n={3} label="Process" title="How it works." lede="No account and no paperwork. Three steps from browsing to a stocked shelf." />

        <div className="steps-list" ref={listRef}>
          <span className="steps-rail" aria-hidden="true" />
          <motion.span className="steps-rail-fill" aria-hidden="true" style={narrow ? { scaleY: fill } : { scaleX: fill }} />
          <ol>
            {STEPS.map(({ title, body }, i) => (
              <Reveal as="li" className="step" key={title} delay={i * 0.08} amount={0.6}>
                <svg className="step-n" viewBox="0 0 210 150" aria-hidden="true" focusable="false">
                  <text x="4" y="138">{String(i + 1).padStart(2, '0')}</text>
                </svg>
                <h3>{title}</h3>
                <p>{body}</p>
              </Reveal>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

import { useRef } from 'react';
import { motion, useScroll, useSpring } from 'motion/react';
import { ArrowsClockwiseIcon, ChatCircleTextIcon, MagnifyingGlassIcon } from '@phosphor-icons/react';
import { useNarrow } from '../../hooks/useNarrow.js';
import Reveal from '../Reveal.jsx';

const STEPS = [
  {
    Icon: MagnifyingGlassIcon,
    title: 'Browse the packages',
    body: 'Pricing and contents for every bundle above are laid out plainly, with no fine print.',
  },
  {
    Icon: ChatCircleTextIcon,
    title: 'Send an inquiry',
    body: "Open a package and send your contact details. We'll confirm the order and set you up as a partner.",
  },
  {
    Icon: ArrowsClockwiseIcon,
    title: 'Reorder anytime',
    body: 'Come back whenever the shelves run low. Reordering takes minutes, not another round of paperwork.',
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
        <Reveal className="section-head">
          <h2 className="title-xl">How it works.</h2>
          <p className="lede">No account and no paperwork. Three steps from browsing to a stocked shelf.</p>
        </Reveal>

        <div className="steps-list" ref={listRef}>
          <span className="steps-rail" aria-hidden="true" />
          <motion.span className="steps-rail-fill" aria-hidden="true" style={narrow ? { scaleY: fill } : { scaleX: fill }} />
          <ol>
            {STEPS.map(({ Icon, title, body }, i) => (
              <Reveal as="li" className="step" key={title} delay={i * 0.08} amount={0.6}>
                <span className="step-icon"><Icon size={16} weight="bold" /></span>
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

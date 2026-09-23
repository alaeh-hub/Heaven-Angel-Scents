import { useRef } from 'react';
import { motion, useScroll, useSpring } from 'motion/react';
import { ArrowsClockwiseIcon, ChatCircleTextIcon, MagnifyingGlassIcon } from '@phosphor-icons/react';
import Reveal from '../Reveal.jsx';

const STEPS = [
  {
    Icon: MagnifyingGlassIcon,
    title: 'Fall in love, fast',
    body: 'Browse the bundles above. Pricing and contents are laid out plainly, with no login and no fine print.',
  },
  {
    Icon: ChatCircleTextIcon,
    title: 'Say the word',
    body: "Send one quick inquiry and we're already on it. We'll confirm details and get you set up as a partner.",
  },
  {
    Icon: ArrowsClockwiseIcon,
    title: 'Restock on repeat',
    body: 'Come back whenever the shelves run low. Reordering takes minutes, not another round of paperwork.',
  },
];

/**
 * Sticky intro on the left, steps on the right. A gold rail fills as the
 * visitor reads down the steps, so progress through the process is
 * visible at a glance.
 */
export default function Steps() {
  const listRef = useRef(null);
  const { scrollYProgress } = useScroll({ target: listRef, offset: ['start 0.75', 'end 0.55'] });
  const fill = useSpring(scrollYProgress, { stiffness: 200, damping: 40, restDelta: 0.001 });

  return (
    <section className="section" id="how-it-works">
      <div className="container steps-grid">
        <Reveal className="steps-intro">
          <h2 className="title-xl">How it works</h2>
          <p className="lede">No account and no paperwork. Three steps from browsing to a stocked shelf.</p>
        </Reveal>

        <div className="steps-list" ref={listRef}>
          <span className="steps-rail" aria-hidden="true" />
          <motion.span className="steps-rail-fill" aria-hidden="true" style={{ scaleY: fill }} />
          <ol>
          {STEPS.map(({ Icon, title, body }, i) => (
            <Reveal as="li" className="step" key={title} delay={i * 0.05} amount={0.6}>
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

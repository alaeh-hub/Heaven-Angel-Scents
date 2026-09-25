import { useRef } from 'react';
import { motion, useScroll, useSpring } from 'motion/react';
import { ArrowsClockwiseIcon, ChatCircleTextIcon, MagnifyingGlassIcon } from '@phosphor-icons/react';
import Reveal from '../Reveal.jsx';
import BlurText from '../BlurText.jsx';

const STEPS = [
  {
    Icon: MagnifyingGlassIcon,
    title: 'Browse the packages',
    body: 'Browse the bundles above. Pricing and contents are laid out plainly, with no login and no fine print.',
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
 * Sticky intro on the left, steps on the right. A gold rail fills as the
 * visitor reads down the steps, so progress through the process is
 * visible at a glance. No 3D scene here: the bottles already carry the
 * hero and the fragrance-notes section right above this one.
 */
export default function Steps() {
  const listRef = useRef(null);
  const { scrollYProgress } = useScroll({ target: listRef, offset: ['start 0.75', 'end 0.55'] });
  const fill = useSpring(scrollYProgress, { stiffness: 200, damping: 40, restDelta: 0.001 });

  return (
    <section className="section" id="how-it-works">
      <div className="container steps-grid">
        <Reveal className="steps-intro">
          <BlurText className="title-xl" text="How it works" />
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

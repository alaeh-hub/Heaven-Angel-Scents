import { useEffect, useRef } from 'react';
import { animate, motion, useInView, useMotionValue, useReducedMotion, useTransform } from 'motion/react';
import { EASE_OUT } from '../motion.js';
import { peso } from '../utils.js';

/**
 * A number that rolls to its value instead of just appearing, so the
 * figure that matters (a partner price, a profit) is the one the eye
 * lands on. With `from`, it starts there (a card's price counts down
 * from the list price); `whenInView` waits until it's on screen, once.
 * After that, any change to `value` rolls from the current figure.
 *
 * Driven by a motion value, so the rolling never re-renders React.
 * Screen readers get only the final figure; under reduced motion it's
 * simply the final figure.
 */
export default function AnimatedNumber({ value, from, format = peso, whenInView = false, duration = 1.1, className }) {
  const ref = useRef(null);
  const reduce = useReducedMotion();
  const inView = useInView(ref, { once: true, amount: 0.6 });
  const mv = useMotionValue(reduce ? value : (from ?? value));
  const text = useTransform(mv, (v) => format(v));

  useEffect(() => {
    if (reduce) {
      mv.set(value);
      return undefined;
    }
    if (whenInView && !inView) return undefined;
    const controls = animate(mv, value, { duration, ease: EASE_OUT });
    return () => controls.stop();
  }, [value, inView, whenInView, reduce, duration, mv]);

  return (
    <span ref={ref} className={className}>
      <span className="sr-only">{format(value)}</span>
      <motion.span aria-hidden="true">{text}</motion.span>
    </span>
  );
}

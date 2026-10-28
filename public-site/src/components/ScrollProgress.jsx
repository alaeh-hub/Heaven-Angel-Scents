import { motion, useReducedMotion, useScroll, useSpring } from 'motion/react';

/**
 * A thin gold line along the top edge that fills as the page is read, so
 * the length of the long landing page is visible at a glance. Driven by
 * motion values (no React state), smoothed with a spring, and left out
 * under reduced motion.
 */
export default function ScrollProgress() {
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll();
  const scaleX = useSpring(scrollYProgress, { stiffness: 200, damping: 40, restDelta: 0.001 });
  if (reduce) return null;
  return <motion.div className="scroll-progress" style={{ scaleX }} aria-hidden="true" />;
}

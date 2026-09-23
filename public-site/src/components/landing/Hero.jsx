import { useRef } from 'react';
import { motion, useReducedMotion, useScroll, useTransform } from 'motion/react';
import { ArrowRightIcon } from '@phosphor-icons/react';
import { ARRIVE, SPRING_SOFT } from '../../motion.js';

const LINES = ['Partner with', 'Heaven & Angel Scents.'];

/**
 * Split hero: the offer on the left, the two signature bottles on the
 * right. On load the headline rises line by line out of its own mask and
 * the bottles settle in; while scrolling away the bottles drift apart at
 * different speeds (depth) and the copy recedes.
 */
export default function Hero() {
  const ref = useRef(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end start'] });
  const frontY = useTransform(scrollYProgress, [0, 1], [0, -110]);
  const backY = useTransform(scrollYProgress, [0, 1], [0, 40]);
  const copyY = useTransform(scrollYProgress, [0, 1], [0, 70]);
  const copyOpacity = useTransform(scrollYProgress, [0, 0.65], [1, 0]);

  return (
    <section className="hero" id="top" ref={ref}>
      <div className="container hero-grid">
        <motion.div className="hero-copy" style={reduce ? undefined : { y: copyY, opacity: copyOpacity }}>
          <h1 className="display hero-title">
            {LINES.map((line, i) => (
              <span className="line" key={line}>
                <motion.span
                  initial={{ y: '110%' }}
                  animate={{ y: '0%' }}
                  transition={{ ...ARRIVE, duration: 1.05, delay: 0.1 + i * 0.1 }}
                >
                  {line}
                </motion.span>
              </span>
            ))}
          </h1>
          <motion.p
            className="lede"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...ARRIVE, delay: 0.4 }}
          >
            Premium fragrances, priced below wholesale. Curated bundles for distributors and resellers.
          </motion.p>
          <motion.div
            className="hero-ctas"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...ARRIVE, delay: 0.5 }}
          >
            <motion.a href="#packages" className="btn btn-primary" whileTap={{ scale: 0.97 }}>
              View packages
            </motion.a>
            <a href="#how-it-works" className="link-arrow">
              How it works <ArrowRightIcon size={16} weight="bold" />
            </a>
          </motion.div>
        </motion.div>

        <div className="hero-visual glow-area" aria-hidden="true">
          <motion.div
            className="hero-glow"
            initial={{ opacity: 0, scale: 0.85 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 1.6, ease: ARRIVE.ease }}
          />
          <motion.img
            src="/static/img/hero-perfume-female.png"
            alt=""
            className="hero-bottle hero-bottle-b"
            style={reduce ? undefined : { y: backY }}
            initial={{ opacity: 0, scale: 0.94 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ ...SPRING_SOFT, delay: 0.3 }}
          />
          <motion.img
            src="/static/img/hero-perfume-male.png"
            alt=""
            className="hero-bottle hero-bottle-a"
            style={reduce ? undefined : { y: frontY }}
            initial={{ opacity: 0, scale: 0.94 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ ...SPRING_SOFT, delay: 0.18 }}
            fetchPriority="high"
          />
        </div>
      </div>
    </section>
  );
}

import { useEffect, useRef } from 'react';
import { animate, motion, useInView, useMotionValue, useReducedMotion, useTransform } from 'motion/react';
import { EASE_OUT } from '../../motion.js';

/** A figure that counts up the first time it's seen, and glides to its
    new value whenever it changes (e.g. after switching filters). The
    count runs on a motion value, so it never re-renders React per frame. */
function Stat({ value, decimals = 0, unit, label, delay = 0 }) {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, amount: 0.6 });
  const reduce = useReducedMotion();
  const count = useMotionValue(0);
  const text = useTransform(() => count.get().toFixed(decimals));

  useEffect(() => {
    if (!inView) return undefined;
    if (reduce) {
      count.set(value);
      return undefined;
    }
    const controls = animate(count, value, { duration: 1.4, ease: EASE_OUT, delay });
    return () => controls.stop();
  }, [inView, value, reduce, delay, count]);

  return (
    <div className="stat" ref={ref}>
      <div className="stat-value">
        <motion.span>{text}</motion.span>
        {unit && <span className="unit">{unit}</span>}
      </div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export default function Stats({ data }) {
  const packages = data?.packages || [];
  const bestDiscount = packages.length ? Math.max(...packages.map((p) => p.discount_percent)) : 0;
  const partnerTypes = data?.partner_types?.length || 2;

  return (
    <section className="section" aria-label="At a glance" style={{ paddingBlock: 'clamp(64px, 8vw, 110px)' }}>
      <div className="container stats-row">
        <Stat value={packages.length} label={packages.length === 1 ? 'Package available now' : 'Packages available now'} />
        <Stat
          value={bestDiscount}
          decimals={Number.isInteger(bestDiscount) ? 0 : 1}
          unit="%"
          label="Best discount on offer"
          delay={0.1}
        />
        <Stat value={partnerTypes} label="Partner types welcome" delay={0.2} />
      </div>
    </section>
  );
}

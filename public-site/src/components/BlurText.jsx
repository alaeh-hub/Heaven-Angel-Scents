import { motion, useReducedMotion } from 'motion/react';

// Each word rises a little and sharpens out of a blur.
const WORD = {
  hidden: { opacity: 0, y: 14, filter: 'blur(10px)' },
  show: { opacity: 1, y: 0, filter: 'blur(0px)', transition: { duration: 0.7, ease: [0.215, 0.61, 0.355, 1] } },
};

/**
 * Text whose words sharpen in one after another the first time it
 * scrolls into view. Screen readers get the plain sentence (aria-label);
 * under reduced motion it's just the text.
 */
export default function BlurText({ as = 'h2', text, className, delay = 0, stagger = 0.035, amount = 0.6 }) {
  const reduce = useReducedMotion();
  const Tag = motion[as];
  if (reduce) return <Tag className={className}>{text}</Tag>;

  const words = text.split(' ');
  return (
    <Tag
      className={className}
      aria-label={text}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, amount }}
      variants={{ show: { transition: { staggerChildren: stagger, delayChildren: delay } } }}
    >
      {words.map((word, i) => (
        <span key={i}>
          <motion.span className="blur-word" aria-hidden="true" variants={WORD}>{word}</motion.span>
          {i < words.length - 1 && ' '}
        </span>
      ))}
    </Tag>
  );
}

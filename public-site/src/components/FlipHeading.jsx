import { useRef } from 'react';
import { useInView } from 'motion/react';
import { FlipText } from '@/components/ui/flip-text';

/**
 * A heading whose letters flip over once as it scrolls into view
 * (Vengeance UI flip-text, held paused until then, see skin.css). The
 * real text sits in `aria-label`; the per-letter spans are hidden from
 * screen readers. Under reduced motion the letters simply stay put.
 */
export default function FlipHeading({ as: Tag = 'h2', text, className = '', duration = 1.5 }) {
  const ref = useRef(null);
  const seen = useInView(ref, { once: true, amount: 0.6 });
  return (
    <Tag ref={ref} className={className} aria-label={text}>
      <span aria-hidden="true" className={seen ? 'flip-in-view' : ''}>
        <FlipText duration={duration} loop={false}>{text}</FlipText>
      </span>
    </Tag>
  );
}

import { motion } from 'motion/react';
import { SPRING } from '../motion.js';

/**
 * Segmented control; the pill slides to whichever option is picked.
 * `options` is a list of [value, label, count?]. `id` keeps two controls
 * on different pages from sharing one sliding pill.
 */
export default function Segmented({ id, label, options, value, onChange }) {
  return (
    <div className="segmented-wrap">
      <div className="segmented" role="tablist" aria-label={label}>
        {options.map(([key, text, count]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={value === key}
            className="segmented-item"
            onClick={() => onChange(key)}
          >
            {value === key && <motion.span layoutId={`${id}-pill`} className="segmented-pill" transition={SPRING} />}
            <span className="segmented-label">
              {text}
              {count != null && <span className="segmented-count">{count}</span>}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

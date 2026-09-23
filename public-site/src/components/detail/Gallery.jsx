import { AnimatePresence, motion } from 'motion/react';
import { CaretLeftIcon, CaretRightIcon } from '@phosphor-icons/react';
import { SPRING } from '../../motion.js';
import ProductVisual from '../ProductVisual.jsx';

// Slides in from the side it's coming from, so paging reads as spatial.
const slide = {
  enter: (dir) => ({ opacity: 0, x: dir * 70, scale: 0.98 }),
  center: { opacity: 1, x: 0, scale: 1 },
  exit: (dir) => ({ opacity: 0, x: dir * -70, scale: 0.98 }),
};

const SWIPE_PX = 60;

/**
 * Big product stage + thumbnail strip. `active`/`dir` live in the parent
 * so the contents list below can drive the same selection. On touch the
 * stage can be swiped left/right.
 */
export default function Gallery({ items, active, dir, onSelect }) {
  const item = items[active];
  const many = items.length > 1;
  const step = (delta) => onSelect((active + delta + items.length) % items.length, delta);

  return (
    <div className="gallery">
      <div className="gallery-stage glow-area">
        <AnimatePresence initial={false} custom={dir}>
          <motion.div
            key={active}
            className="gallery-slide"
            custom={dir}
            variants={slide}
            initial="enter"
            animate="center"
            exit="exit"
            transition={{ x: SPRING, scale: SPRING, opacity: { duration: 0.25 } }}
            drag={many ? 'x' : false}
            dragConstraints={{ left: 0, right: 0 }}
            dragElastic={0.25}
            onDragEnd={(_, info) => {
              if (info.offset.x < -SWIPE_PX) step(1);
              else if (info.offset.x > SWIPE_PX) step(-1);
            }}
          >
            <ProductVisual item={item} />
          </motion.div>
        </AnimatePresence>

        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={active}
            className="gallery-caption"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.25 }}
          >
            <strong>{item.item_name}</strong>
            <span style={{ display: 'flex', gap: 6 }}>
              <span className="chip">{item.variant} · {item.unit}</span>
              <span className="chip chip-gold">x{item.qty} per set</span>
            </span>
          </motion.div>
        </AnimatePresence>

        {many && (
          <>
            <button type="button" className="gallery-nav prev" aria-label="Previous product" onClick={() => step(-1)}>
              <CaretLeftIcon size={18} weight="bold" />
            </button>
            <button type="button" className="gallery-nav next" aria-label="Next product" onClick={() => step(1)}>
              <CaretRightIcon size={18} weight="bold" />
            </button>
          </>
        )}
      </div>

      {many && (
        <div className="gallery-thumbs" role="tablist" aria-label="Products in this package">
          {items.map((it, i) => (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={i === active}
              aria-label={it.item_name}
              className="gallery-thumb"
              onClick={() => onSelect(i, i > active ? 1 : -1)}
            >
              {i === active && <motion.span layoutId="gallery-ring" className="gallery-thumb-ring" transition={SPRING} />}
              <ProductVisual item={it} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

import { motion } from 'motion/react';
import { CaretLeftIcon, CaretRightIcon } from '@phosphor-icons/react';
import { SPRING } from '../../motion.js';

/** 1 … 4 5 6 … 12, collapsing the middle once there are more than 7 pages. */
function pageList(page, pages) {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const list = [1];
  const from = Math.max(2, page - 1);
  const to = Math.min(pages - 1, page + 1);
  if (from > 2) list.push('gap-start');
  for (let p = from; p <= to; p += 1) list.push(p);
  if (to < pages - 1) list.push('gap-end');
  list.push(pages);
  return list;
}

export default function Pagination({ page, pages, onChange }) {
  if (pages <= 1) return null;
  return (
    <nav className="pagination" aria-label="Product pages">
      <button
        type="button"
        className="page-btn page-step"
        aria-label="Previous page"
        disabled={page === 1}
        onClick={() => onChange(page - 1)}
      >
        <CaretLeftIcon size={16} weight="bold" />
      </button>

      {pageList(page, pages).map((p) =>
        typeof p === 'string' ? (
          <span key={p} className="page-gap" aria-hidden="true">…</span>
        ) : (
          <button
            key={p}
            type="button"
            className="page-btn"
            aria-label={`Page ${p}`}
            aria-current={p === page ? 'page' : undefined}
            onClick={() => onChange(p)}
          >
            {p === page && <motion.span layoutId="page-pill" className="page-pill" transition={SPRING} />}
            <span className="page-num">{p}</span>
          </button>
        ),
      )}

      <button
        type="button"
        className="page-btn page-step"
        aria-label="Next page"
        disabled={page === pages}
        onClick={() => onChange(page + 1)}
      >
        <CaretRightIcon size={16} weight="bold" />
      </button>
    </nav>
  );
}

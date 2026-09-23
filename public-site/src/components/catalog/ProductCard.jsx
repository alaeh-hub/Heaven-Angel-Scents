import { forwardRef } from 'react';
import { motion } from 'motion/react';
import { SPRING, SPRING_SOFT } from '../../motion.js';
import ProductVisual from '../ProductVisual.jsx';

export const GENDER_LABEL = { Male: 'For men', Female: 'For women', Unisex: 'Unisex' };

// Hover choreography, driven from the card so every layer moves together:
// the card lifts, the bottle rises and tilts toward the viewer, a soft
// gold glow blooms behind it, and the sizes slide up into view.
const card = { rest: { y: 0 }, hover: { y: -8 } };
const bottle = { rest: { scale: 1, y: 0, rotate: 0 }, hover: { scale: 1.07, y: -8, rotate: -3 } };
const glow = { rest: { opacity: 0, scale: 0.7 }, hover: { opacity: 1, scale: 1 } };
const sizes = { rest: { opacity: 0.75, y: 2 }, hover: { opacity: 1, y: 0 } };

/** One scent in the catalog grid. forwardRef for AnimatePresence. */
const ProductCard = forwardRef(function ProductCard({ product }, ref) {
  return (
    <motion.article
      ref={ref}
      className="product-card glow-card"
      variants={card}
      initial="rest"
      animate="rest"
      whileHover="hover"
      transition={SPRING}
    >
      <div className="product-card-media">
        <motion.span className="product-card-glow" variants={glow} transition={SPRING_SOFT} aria-hidden="true" />
        <ProductVisual item={product} variants={bottle} transition={SPRING_SOFT} />
      </div>
      <div className="product-card-body">
        <h3>{product.item_name}</h3>
        <span className="chip">{GENDER_LABEL[product.variant] || product.variant}</span>
      </div>
      {product.sizes.length > 0 && (
        <motion.p className="product-card-sizes" variants={sizes} transition={SPRING}>
          {product.sizes.map((s) => (s === 'BULK' ? 'Bulk' : s.replace('ML', ' ml'))).join(', ')}
        </motion.p>
      )}
    </motion.article>
  );
});

export default ProductCard;

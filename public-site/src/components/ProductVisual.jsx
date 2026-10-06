import { forwardRef } from 'react';
import { motion } from 'motion/react';

// TODO(content): placeholder photography. Until real product photos are
// uploaded on the admin Products page, every product without one shows
// the men's or women's bottle render matching its gender.
const MALE = '/static/img/hero-perfume-male.webp';
const FEMALE = '/static/img/hero-perfume-female.webp';

export function productImage(item) {
  if (item.image_url) return item.image_url;
  if (item.variant === 'Male') return MALE;
  return FEMALE;
}

/** A product's own photo, or its gender's placeholder bottle. Accepts
    motion props, so callers can animate it (e.g. on hover). */
const ProductVisual = forwardRef(function ProductVisual({ item, className = '', ...motionProps }, ref) {
  return (
    <motion.img
      ref={ref}
      src={productImage(item)}
      alt={item.item_name}
      className={`product-visual ${className}`}
      loading="lazy"
      draggable={false}
      {...motionProps}
    />
  );
});

export default ProductVisual;

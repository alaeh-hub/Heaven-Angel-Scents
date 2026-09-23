import { forwardRef } from 'react';
import { motion } from 'motion/react';

// TODO(content): placeholder photography. Until real product photos are
// uploaded on the admin Products page, every product without one shows
// the men's or women's bottle render matching its gender; unisex scents
// alternate between the two (stable per name, so a product never flips).
const MALE = '/static/img/hero-perfume-male.png';
const FEMALE = '/static/img/hero-perfume-female.png';

function hash(text) {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export function productImage(item) {
  if (item.image_url) return item.image_url;
  if (item.variant === 'Male') return MALE;
  if (item.variant === 'Female') return FEMALE;
  return hash(item.item_name || '') % 2 ? FEMALE : MALE;
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

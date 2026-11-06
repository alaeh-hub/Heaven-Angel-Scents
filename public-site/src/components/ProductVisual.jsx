import { forwardRef } from 'react';
import { motion } from 'motion/react';

import { SCENTS } from '../content/scents.js';

// A product's own uploaded photo wins. Without one, it shows the real bottle
// render for its scent (matched by name, then by the code at the start of its
// SKU, e.g. "A1-85ML" -> A1). Only when neither matches does it fall back to
// the first real bottle of its gender, so the placeholder is never a stock image.
const byName = new Map(SCENTS.map((s) => [s.name.toLowerCase(), s]));
const byCode = new Map(SCENTS.map((s) => [s.code.toUpperCase(), s]));
const defaultFor = (variant) => SCENTS.find((s) => s.variant === (variant === 'Male' ? 'Male' : 'Female'));

export function productImage(item) {
  if (item.image_url) return item.image_url;
  const code = String(item.sku || '').split('-')[0].toUpperCase();
  const scent = byName.get(String(item.item_name || '').trim().toLowerCase())
    || byCode.get(code)
    || defaultFor(item.variant);
  return scent.bottle;
}

// The scent renders are wide transparent layers with the bottle on the right
// (see SCENT_BOTTLE_CENTRE), so they need centring wherever they are shown;
// `.is-render` in detail.css does that. An uploaded photo needs nothing.
const isRender = (item) => productImage(item).includes('/static/img/scents/');

/** A product's own photo, or its gender's placeholder bottle. Accepts
    motion props, so callers can animate it (e.g. on hover). */
const ProductVisual = forwardRef(function ProductVisual({ item, className = '', ...motionProps }, ref) {
  return (
    <motion.img
      ref={ref}
      src={productImage(item)}
      alt={item.item_name}
      className={`product-visual${isRender(item) ? ' is-render' : ''} ${className}`}
      loading="lazy"
      draggable={false}
      {...motionProps}
    />
  );
});

export default ProductVisual;

import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { motion } from 'motion/react';
import { ArrowRightIcon } from '@phosphor-icons/react';
import { fetchProducts } from '../../api.js';
import { productsPath } from '../../utils.js';
import { productImage } from '../ProductVisual.jsx';
import Reveal from '../Reveal.jsx';
import BlurText from '../BlurText.jsx';
import DotField from './DotField.jsx';

const FALLBACK = [
  ['/static/img/hero-perfume-male.png', "Men's fragrance"],
  ['/static/img/hero-perfume-female.png', "Women's fragrance"],
];
/** Enough tiles that one track is wider than the widest screen. */
const MIN_TILES = 8;

/** Repeat `items` until there are at least MIN_TILES of them. */
function fill(items) {
  return Array.from({ length: Math.max(MIN_TILES, items.length) }, (_, i) => items[i % items.length]);
}

/**
 * The shelf shows the real catalog (first page), each scent with its own
 * photo once one is uploaded (see ProductVisual). Until the request
 * lands, or if it fails, it shows the two signature renders.
 */
function useShelf(slug) {
  const [tiles, setTiles] = useState(() => fill(FALLBACK));
  useEffect(() => {
    const controller = new AbortController();
    fetchProducts(slug, { page: 1 }, controller.signal)
      .then((data) => {
        const items = (data?.products || []).map((p) => [productImage(p), p.item_name]);
        if (items.length) setTiles(fill(items));
      })
      .catch(() => {});
    return () => controller.abort();
  }, [slug]);
  return tiles;
}

/**
 * The one marquee on the page: a slow, continuous shelf of the
 * catalog's scents. Pauses on hover, where the bottle under the pointer
 * lifts and shows its name; under reduced motion it becomes a
 * still, swipeable row instead. Sits over its own interactive dot
 * grid (DotField), lit by the cursor only within this section.
 */
export default function Collection() {
  const { slug } = useParams();
  const tiles = useShelf(slug);
  return (
    <section className="section collection" id="collection">
      <DotField className="collection-dots" />
      <div className="container">
        <Reveal className="section-head">
          <BlurText className="title-xl" text="The collection." />
          <p className="lede">Men's and women's signatures, each bottled at 85 ml.</p>
          <div className="collection-ctas">
            <motion.div whileTap={{ scale: 0.97 }}>
              <Link to={productsPath(slug)} className="btn btn-secondary">
                View all products <ArrowRightIcon size={16} weight="bold" />
              </Link>
            </motion.div>
            {/* Grouped so these two wrap as a pair if space runs low,
                never splitting one onto its own line. */}
            <div className="collection-cta-links">
              <Link to={`${productsPath(slug)}?gender=Male`} className="link-arrow">
                For him <ArrowRightIcon size={16} weight="bold" />
              </Link>
              <Link to={`${productsPath(slug)}?gender=Female`} className="link-arrow">
                For her <ArrowRightIcon size={16} weight="bold" />
              </Link>
            </div>
          </div>
        </Reveal>
      </div>
      <Reveal className="marquee" y={0}>
        {[0, 1].map((copy) => (
          <div key={copy} className="marquee-track" aria-hidden={copy === 1 ? 'true' : undefined}>
            {tiles.map(([src, alt], i) => (
              <div className="bottle-tile glow-card" key={i}>
                <img src={src} alt={copy === 1 ? '' : alt} loading="lazy" />
                {/* Revealed under the lifted bottle on hover (the strip
                    pauses), so the shelf can be browsed by name. */}
                <span className="bottle-tile-name" aria-hidden="true">{alt}</span>
              </div>
            ))}
          </div>
        ))}
      </Reveal>
    </section>
  );
}

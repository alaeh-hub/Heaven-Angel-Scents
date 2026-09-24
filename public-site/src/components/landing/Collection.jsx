import { Link, useParams } from 'react-router';
import { motion } from 'motion/react';
import { ArrowRightIcon } from '@phosphor-icons/react';
import { productsPath } from '../../utils.js';
import Reveal from '../Reveal.jsx';
import BlurText from '../BlurText.jsx';
import DotField from './DotField.jsx';

const BOTTLES = [
  ['/static/img/hero-perfume-male.png', "Men's fragrance"],
  ['/static/img/hero-perfume-female.png', "Women's fragrance"],
];
const TILES = Array.from({ length: 8 }, (_, i) => BOTTLES[i % 2]);

/**
 * The one marquee on the page: a slow, continuous shelf of the two
 * signatures. Pauses on hover; under reduced motion it becomes a
 * still, swipeable row instead. Sits over its own interactive dot
 * grid (DotField), lit by the cursor only within this section.
 */
export default function Collection() {
  const { slug } = useParams();
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
            {TILES.map(([src, alt], i) => (
              <div className="bottle-tile glow-card" key={i}>
                <img src={src} alt={copy === 1 ? '' : alt} loading="lazy" />
              </div>
            ))}
          </div>
        ))}
      </Reveal>
    </section>
  );
}

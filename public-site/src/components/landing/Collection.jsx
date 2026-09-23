import { Link, useParams } from 'react-router';
import { motion } from 'motion/react';
import { ArrowRightIcon } from '@phosphor-icons/react';
import { productsPath } from '../../utils.js';
import Reveal from '../Reveal.jsx';

const BOTTLES = [
  ['/static/img/hero-perfume-male.png', "Men's fragrance"],
  ['/static/img/hero-perfume-female.png', "Women's fragrance"],
];
const TILES = Array.from({ length: 8 }, (_, i) => BOTTLES[i % 2]);

/**
 * The one marquee on the page: a slow, continuous shelf of the two
 * signatures. Pauses on hover; under reduced motion it becomes a
 * still, swipeable row instead. Sits over its own backdrop video
 * (see PackagesPage).
 */
export default function Collection() {
  const { slug } = useParams();
  return (
    <section className="section collection section-video" id="collection">
      <div className="container">
        <Reveal className="section-head">
          <h2 className="title-xl">The collection.</h2>
          <p className="lede">Men&apos;s and women&apos;s signatures, each bottled at 85 ml.</p>
          <motion.div whileTap={{ scale: 0.97 }} style={{ justifySelf: 'start', marginTop: 8 }}>
            <Link to={productsPath(slug)} className="btn btn-secondary">
              View all products <ArrowRightIcon size={16} weight="bold" />
            </Link>
          </motion.div>
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

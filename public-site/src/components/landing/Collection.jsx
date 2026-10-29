import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { motion } from 'motion/react';
import { ArrowRightIcon } from '@phosphor-icons/react';
import { productsPath } from '../../utils.js';
import { SCENTS, SCENT_ASPECT, SCENT_BOTTLE_CENTRE } from '../../content/scents.js';
import Reveal from '../Reveal.jsx';
import DotField from './DotField.jsx';

/** Women's and men's scents alternate along the shelf, so the red and
    blue labels trade places; whichever side has more fills the tail. */
function interleave(scents) {
  const her = scents.filter((s) => s.variant === 'Female');
  const him = scents.filter((s) => s.variant === 'Male');
  const out = [];
  for (let i = 0; i < Math.max(her.length, him.length); i += 1) {
    if (her[i]) out.push(her[i]);
    if (him[i]) out.push(him[i]);
  }
  return out;
}

const SHELF = interleave(SCENTS);

/** The shelf's frame is sized from the render's own proportions, and the
    bottle's centre line, so the CSS can park the bottle in the middle of
    the tile at rest and slide the whole shot back on hover. */
const SHELF_VARS = {
  '--scent-aspect': SCENT_ASPECT,
  '--scent-centre': `${(SCENT_BOTTLE_CENTRE * 100).toFixed(2)}%`,
  '--tiles': SHELF.length,
};

/**
 * One scent: its bottle on its own, with the gift box tucked behind it.
 * Hovering (or focusing, or tapping on a touch screen, which has no hover)
 * slides the box out beside the bottle and shows the scent's name. The
 * two layers are cut from the same studio render on one canvas (see
 * scripts/build_scent_images.py), so together they rebuild the original
 * shot exactly.
 */
function ScentTile({ scent, hidden, active, auto, eager, nearby }) {
  const [open, setOpen] = useState(false);
  const forHim = scent.variant === 'Male';

  const toggleOnTouch = (e) => {
    // Mouse users get the box from hover alone; a click shouldn't
    // fight it. Touch (and pen) has no hover, so a tap flips it.
    if (e.pointerType === 'mouse') return;
    // The swipe row opens whichever tile is centred; a tap just brings
    // the tapped one to the centre.
    if (auto) {
      e.currentTarget.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
      return;
    }
    setOpen((v) => !v);
  };

  return (
    <div
      className="bottle-tile glow-card"
      data-variant={scent.variant}
      data-code={scent.code}
      data-open={open || active || undefined}
      tabIndex={hidden ? undefined : 0}
      role={hidden ? undefined : 'group'}
      aria-label={hidden ? undefined : `${scent.name} ${scent.code}, ${forHim ? 'for him' : 'for her'}`}
      onPointerEnter={(e) => e.pointerType === 'mouse' && setOpen(true)}
      onPointerLeave={(e) => e.pointerType === 'mouse' && setOpen(false)}
      onPointerUp={toggleOnTouch}
      onFocus={(e) => e.currentTarget.matches(':focus-visible') && setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <span className="bottle-tile-aura" aria-hidden="true" />
      <div className="scent-frame">
        <span className="scent-floor" aria-hidden="true" />
        <img
          className="scent-box"
          src={scent.box}
          alt=""
          loading={nearby ? 'eager' : 'lazy'}
          decoding="async"
          draggable={false}
        />
        <img
          className="scent-bottle"
          src={scent.bottle}
          alt={hidden ? '' : `${scent.name} ${scent.code}, 85 ml eau de parfum`}
          loading={eager ? 'eager' : 'lazy'}
          decoding="async"
          draggable={false}
        />
      </div>
      {/* Revealed with the box on hover, so the shelf can be browsed by
          name; always shown where there's no hover. */}
      <span className="bottle-tile-meta" aria-hidden="true">
        <span className="bottle-tile-code">{scent.code}</span>
        <span className="bottle-tile-name">{scent.name}</span>
        <span className="bottle-tile-sub">{forHim ? 'For him' : 'For her'} · 85 ml</span>
      </span>
    </div>
  );
}

/**
 * The one marquee on the page: a slow, continuous shelf of the
 * collection's scents, each shown as its bottle alone. Pauses on hover,
 * where the bottle under the pointer slides over and its box appears
 * beside it. On touch screens (nothing to hover, so no way to pause it
 * or pick a bottle) and under reduced motion it becomes a still row the
 * visitor swipes through instead, tapping a bottle to see its box (see
 * the CSS). Sits over its own interactive dot grid (DotField), lit by
 * the cursor only within this section.
 */
export default function Collection() {
  const { slug } = useParams();
  const sectionRef = useRef(null);
  // Code of the tile centred in the swipe row (touch screens only).
  const [centred, setCentred] = useState(null);

  // The track's own animation is compositor-only (transform) and cheap,
  // but left running it's a layer the compositor keeps ticking forever;
  // paused the moment the shelf scrolls off screen, like the canvas and
  // WebGL loops elsewhere on the page.
  useEffect(() => {
    const marquee = sectionRef.current?.querySelector('.marquee');
    if (!marquee) return undefined;
    const io = new IntersectionObserver(([entry]) => {
      marquee.dataset.paused = entry.isIntersecting ? 'false' : 'true';
    });
    io.observe(marquee);
    return () => io.disconnect();
  }, []);

  // On a touch screen there is no hover, so the shelf is a swipe row and
  // the tile nearest its centre is the one that opens its box.
  useEffect(() => {
    const marquee = sectionRef.current?.querySelector('.marquee');
    const track = marquee?.querySelector('.marquee-track');
    if (!track) return undefined;
    const touch = window.matchMedia('(hover: none)');
    let raf = 0;
    const pick = () => {
      raf = 0;
      if (!touch.matches) { setCentred(null); return; }
      const box = marquee.getBoundingClientRect();
      const mid = box.left + box.width / 2;
      let best = null;
      let bestGap = Infinity;
      for (const tile of track.children) {
        const r = tile.getBoundingClientRect();
        const gap = Math.abs(r.left + r.width / 2 - mid);
        if (gap < bestGap) { bestGap = gap; best = tile.dataset.code; }
      }
      setCentred(best);
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(pick); };
    marquee.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    touch.addEventListener('change', schedule);
    pick();
    return () => {
      cancelAnimationFrame(raf);
      marquee.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      touch.removeEventListener('change', schedule);
    };
  }, []);

  // The boxes of the centred tile and its neighbours are fetched ahead of
  // the swipe, so a box is already there when its tile opens.
  const centredAt = SHELF.findIndex((s) => s.code === centred);

  return (
    <section className="section collection" id="collection" ref={sectionRef}>
      <DotField className="collection-dots" />
      <div className="container">
        <Reveal className="section-head">
          <h2 className="title-xl">The collection.</h2>
          <p className="lede">Men’s and women’s signatures, each bottled at 85 ml.</p>
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
      <Reveal className="marquee" y={0} style={SHELF_VARS}>
        {[0, 1].map((copy) => (
          <div key={copy} className="marquee-track" aria-hidden={copy === 1 ? 'true' : undefined}>
            {SHELF.map((scent, i) => (
              <ScentTile
                key={scent.code}
                scent={scent}
                hidden={copy === 1}
                auto={copy === 0}
                active={copy === 0 && centred === scent.code}
                eager={copy === 0 && i < 4}
                nearby={copy === 0 && centredAt >= 0 && Math.abs(i - centredAt) <= 2}
              />
            ))}
          </div>
        ))}
      </Reveal>
    </section>
  );
}

import { LogoSlider } from '@/components/ui/logo-slider';
import FlipHeading from '../FlipHeading.jsx';
import { SCENTS } from '../../content/scents.js';
import { thumb } from '../../content/scentImages.js';

const WORDS = ['Eau de parfum', 'Partner pricing', 'Small-batch runs', 'Easy reordering', '85 ml bottles', 'Distributors', 'Resellers'];

/**
 * Two slow marquees under the hero, running against each other: the
 * bottles (the breadth of the range), then the house's own words in
 * large serif type. A look at what is on offer before any decision.
 */
export default function ScentRail() {
  const bottles = SCENTS.map((s) => (
    <div className="scent-chip" key={s.code}>
      <img src={thumb(s.bottle, 's')} alt="" width="68" height="100" loading="lazy" decoding="async" />
      <span>
        <span className="scent-chip-code">{s.code}</span>
        <span className="scent-chip-name">{s.name}</span>
      </span>
    </div>
  ));
  const words = WORDS.map((w) => <span className="word-chip" key={w}>{w}</span>);

  return (
    <section className="scent-rail" aria-label="The fragrance range">
      <div className="container scent-rail-head">
        <FlipHeading className="scent-rail-title" text="Every scent in the range" />
        <p className="scent-rail-count">{SCENTS.length} fragrances</p>
      </div>
      <LogoSlider logos={bottles} speed={SCENTS.length * 4} pauseOnHover showBlur={false} />
      <div className="word-rail" aria-hidden="true">
        <LogoSlider logos={words} speed={WORDS.length * 9} direction="right" showBlur={false} />
      </div>
    </section>
  );
}

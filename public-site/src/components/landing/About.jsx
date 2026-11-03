import { ABOUT } from '../../content/about.js';
import Chapter from '../Chapter.jsx';
import { CursorCard } from '@/components/ui/cursor-card';
import { SCENTS, SCENT_BOTTLE_CENTRE } from '../../content/scents.js';
import { thumb } from '../../content/scentImages.js';
import Reveal from '../Reveal.jsx';

/**
 * "About the brand": the heading and story across the top, then the
 * photographs (a large landscape with a smaller portrait overlapping its
 * corner) beside three short facts that arrive one after another. Copy
 * and photos live in content/about.js (placeholders for now). Shares a plain
 * black ground with the craft film (.house).
 */
const PICKS = [SCENTS[0], SCENTS.find((s) => s.variant === 'Male'), SCENTS[4]].filter(Boolean);

export default function About() {
  const { main, detail } = ABOUT.photos;
  return (
    <section className="section section-video" id="about">
      <div className="container">
        <Chapter n={4} label="The house" title={ABOUT.title} lede={ABOUT.lede}>
          <p className="about-start">
            Start with{' '}
            {PICKS.map((s, i) => (
              <span key={s.code}>
                <CursorCard href="#collection" image={thumb(s.bottle, 's')} imageCentre={SCENT_BOTTLE_CENTRE} title={s.name} description={`${s.code}, ${s.variant === 'Male' ? 'for him' : 'for her'}. Eau de parfum, 85 ml.`} hint="See it in the collection">{s.name}</CursorCard>
                {i < PICKS.length - 2 ? ', ' : i === PICKS.length - 2 ? ' and ' : '.'}
              </span>
            ))}
          </p>
        </Chapter>

        <div className="about-body">
          <Reveal className="about-media">
            <img className="about-photo about-photo-main" src={main.src} alt={main.alt} loading="lazy" width="1600" height="1200" />
            <img className="about-photo about-photo-detail" src={detail.src} alt={detail.alt} loading="lazy" width="800" height="1000" />
          </Reveal>

          <ul className="about-points">
            {ABOUT.points.map(({ key, title, body }, i) => (
              <Reveal as="li" className="about-point" key={key} delay={i * 0.08} amount={0.5}>
                <div>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </div>
              </Reveal>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

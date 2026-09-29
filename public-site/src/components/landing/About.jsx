import { FlaskIcon, HourglassMediumIcon, UsersThreeIcon } from '@phosphor-icons/react';
import { ABOUT } from '../../content/about.js';
import Reveal from '../Reveal.jsx';

const ICONS = { people: UsersThreeIcon, years: HourglassMediumIcon, formula: FlaskIcon };

/**
 * "About the brand": the heading and story across the top, then the
 * photographs (a large landscape with a smaller portrait overlapping its
 * corner) beside three short facts that arrive one after another. Copy
 * and photos live in content/about.js (placeholders for now). Sits over
 * the fixed backdrop clip (see PackagesPage).
 */
export default function About() {
  const { main, detail } = ABOUT.photos;
  return (
    <section className="section section-video" id="about">
      <div className="container">
        <Reveal className="section-head about-head">
          <div className="eyebrow">About the brand</div>
          <h2 className="title-xl">{ABOUT.title}</h2>
          <p className="lede">{ABOUT.lede}</p>
        </Reveal>

        <div className="about-body">
          <Reveal className="about-media">
            <img className="about-photo about-photo-main" src={main.src} alt={main.alt} loading="lazy" width="1600" height="1200" />
            <img className="about-photo about-photo-detail" src={detail.src} alt={detail.alt} loading="lazy" width="800" height="1000" />
          </Reveal>

          <ul className="about-points">
            {ABOUT.points.map(({ key, title, body }, i) => {
              const Icon = ICONS[key];
              return (
                <Reveal as="li" className="about-point" key={key} delay={i * 0.08} amount={0.5}>
                  <span className="about-point-icon"><Icon size={22} weight="duotone" /></span>
                  <div>
                    <h3>{title}</h3>
                    <p>{body}</p>
                  </div>
                </Reveal>
              );
            })}
          </ul>
        </div>
      </div>
    </section>
  );
}

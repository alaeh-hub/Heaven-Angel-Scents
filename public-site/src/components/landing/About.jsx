import { FlaskIcon, HourglassMediumIcon, UsersThreeIcon } from '@phosphor-icons/react';
import { ABOUT } from '../../content/about.js';
import Reveal from '../Reveal.jsx';

const ICONS = { people: UsersThreeIcon, years: HourglassMediumIcon, formula: FlaskIcon };

/**
 * "About the brand": the story on the left, three short facts on the
 * right that arrive one after another. Copy lives in content/about.js
 * (placeholder text for now). Sits over the fixed backdrop clip (see
 * PackagesPage).
 */
export default function About() {
  return (
    <section className="section section-video" id="about">
      <div className="container about-grid">
        <Reveal className="about-story">
          <div className="eyebrow">About the brand</div>
          <h2 className="title-xl">{ABOUT.title}</h2>
          <p className="lede">{ABOUT.lede}</p>
        </Reveal>

        <ul className="about-points">
          {ABOUT.points.map(({ key, title, body }, i) => {
            const Icon = ICONS[key];
            return (
              <Reveal as="li" className="about-point glow-card glow-fill" key={key} delay={i * 0.08} amount={0.5}>
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
    </section>
  );
}

import { LAYERS, SCENTS } from '../../content/notes.js';

const TITLE = 'How our scents unfold.';
const LEDE = 'Uriel H1 and Raphael A3 evolve on the skin, hour by hour.';

/**
 * "How our scents unfold": the three layers of Uriel H1 and Raphael A3 as
 * one gold timeline, no cards and no numerals (the process section above
 * already uses them). Static markup and CSS only, so nothing runs on a
 * phone. Copy lives in content/notes.js.
 */
export default function ScentJourney() {
  return (
    <section className="section journey" id="notes">
      <div className="container">
        <div className="section-head">
          <h2 className="title-xl">{TITLE}</h2>
          <p className="lede">{LEDE}</p>
        </div>

        <ol className="journey-steps">
          {LAYERS.map((layer) => (
            <li key={layer.name} className="journey-step">
              <span className="journey-when">{layer.name} · {layer.when}</span>
              <h3>{layer.title}</h3>
              <p className="journey-body">{layer.body}</p>
              <dl className="journey-notes">
                {SCENTS.map(({ key, name }) => (
                  <div key={key}>
                    <dt>{name}</dt>
                    <dd>{layer.notes[key].join(', ')}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

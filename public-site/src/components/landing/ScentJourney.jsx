import { LAYERS, SCENTS } from '../../content/notes.js';

const TITLE = 'How our scents unfold.';
const LEDE = 'Uriel H1 and Raphael A3 change as they wear. Here is what your customers notice, hour by hour.';

/**
 * "How our scents unfold": the three layers of Uriel H1 and Raphael A3 as
 * a plain timeline of three steps. No scene, no scripted motion, nothing
 * to run on a phone: static markup and CSS only. Copy lives in
 * content/notes.js.
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
          {LAYERS.map((layer, i) => (
            <li key={layer.name} className="journey-step">
              <div className="journey-step-top">
                <span className="journey-step-n">0{i + 1}</span>
                <span className="journey-when">{layer.when}</span>
              </div>
              <p className="journey-layer">{layer.name}</p>
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

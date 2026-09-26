import { FACTS, TESTIMONIALS } from '../../content/partner.js';
import { percent } from '../../utils.js';
import AnimatedNumber from '../AnimatedNumber.jsx';
import { useSite } from '../InquiryProvider.jsx';
import Reveal from '../Reveal.jsx';

const count = (v) => String(Math.round(v));

/**
 * The facts a buyer weighs before inquiring, right under the packages:
 * real catalog and package counts (from the server, see api_site), the
 * best discount on offer, the reply-time promise when one is set, plus
 * any facts and partner quotes filled in content/partner.js. The counts
 * roll up from zero as the band comes into view. Nothing here is ever a
 * placeholder: an unknown figure is left out, not guessed.
 */
export default function StatsBand({ topDiscount }) {
  const { site } = useSite();
  const stats = site?.stats;

  const items = [];
  if (stats?.scents) items.push({ key: 'scents', value: <AnimatedNumber value={stats.scents} from={0} format={count} whenInView />, label: 'scents in the catalog' });
  if (stats?.packages) items.push({ key: 'packages', value: <AnimatedNumber value={stats.packages} from={0} format={count} whenInView />, label: stats.packages === 1 ? 'package ready to order' : 'packages ready to order' });
  if (topDiscount > 0) items.push({ key: 'discount', value: <>Up to <AnimatedNumber value={topDiscount} from={0} format={(v) => `${percent(Math.round(v * 10) / 10)}%`} whenInView /></>, label: 'below our list price' });
  items.push(site?.reply_time
    ? { key: 'reply', value: 'We reply', label: site.reply_time }
    : { key: 'account', value: 'No login', label: 'needed to inquire' });
  FACTS.forEach((fact) => items.push({ key: fact.label, value: fact.value, label: fact.label }));

  return (
    <div className="stats">
      <Reveal as="dl" className="stats-band" amount={0.4}>
        {items.map((item) => (
          <div className="stats-item" key={item.key}>
            <dt className="sr-only">{item.label}</dt>
            <dd>
              <span className="stats-value">{item.value}</span>
              <span className="stats-label" aria-hidden="true">{item.label}</span>
            </dd>
          </div>
        ))}
      </Reveal>

      {TESTIMONIALS.length > 0 && (
        <div className="quotes">
          {TESTIMONIALS.map((t, i) => (
            <Reveal as="figure" className="quote" key={t.name} delay={i * 0.08}>
              <blockquote>&ldquo;{t.quote}&rdquo;</blockquote>
              <figcaption>
                <strong>{t.name}</strong>
                <span>{[t.role, t.place].filter(Boolean).join(', ')}</span>
              </figcaption>
            </Reveal>
          ))}
        </div>
      )}
    </div>
  );
}

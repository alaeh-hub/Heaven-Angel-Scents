import { useId, useState } from 'react';
import { motion } from 'motion/react';
import { MinusIcon, PlusIcon } from '@phosphor-icons/react';
import { peso, plural } from '../../utils.js';
import AnimatedNumber from '../AnimatedNumber.jsx';
import BlurText from '../BlurText.jsx';
import Reveal from '../Reveal.jsx';

const MIN_SETS = 1;
const MAX_SETS = 20;

/**
 * "What you'd earn": pick a package and how many sets, and the three
 * figures that decide a reseller's order (what they pay, what it sells
 * for at our list price, the profit in between) roll to their new
 * values. The estimate is honest about its assumption: it's the gap
 * between the partner price and our own list price, nothing else.
 * Only packages that actually carry a discount are offered; with none,
 * the section doesn't render.
 */
/** The packages the estimate can be shown for: those with a discount. */
export const earningOptions = (packages) => packages.filter((p) => p.reference_total - p.discounted_total > 0.005);

export default function Earnings({ packages }) {
  const options = earningOptions(packages);
  const [pickedId, setPickedId] = useState(null);
  const [sets, setSets] = useState(3);
  const selectId = useId();
  const rangeId = useId();

  if (!options.length) return null;
  const pkg = options.find((p) => p.package_id === pickedId) || options[0];
  const pay = pkg.discounted_total * sets;
  const sells = pkg.reference_total * sets;
  const profit = sells - pay;
  const clamp = (n) => Math.min(MAX_SETS, Math.max(MIN_SETS, n));

  return (
    <section className="section" id="earnings">
      <div className="container earn-grid">
        <Reveal className="earn-controls">
          <BlurText className="title-xl" text="See what you'd earn." />
          <p className="lede">Pick a package and how many sets. Profit is estimated at our regular list price.</p>

          <div className="field">
            <label htmlFor={selectId}>Package</label>
            <select
              id={selectId}
              className="input"
              value={pkg.package_id}
              onChange={(e) => setPickedId(Number(e.target.value))}
            >
              {options.map((p) => <option key={p.package_id} value={p.package_id}>{p.package_name}</option>)}
            </select>
          </div>

          <div className="field">
            <label htmlFor={rangeId}>How many sets</label>
            <div className="earn-qty">
              <motion.button type="button" className="icon-btn" aria-label="One set fewer" whileTap={{ scale: 0.9 }} onClick={() => setSets((n) => clamp(n - 1))} disabled={sets <= MIN_SETS}>
                <MinusIcon size={16} weight="bold" />
              </motion.button>
              <input
                id={rangeId}
                className="earn-range"
                type="range"
                min={MIN_SETS}
                max={MAX_SETS}
                value={sets}
                onChange={(e) => setSets(Number(e.target.value))}
                style={{ '--fill': `${((sets - MIN_SETS) / (MAX_SETS - MIN_SETS)) * 100}%` }}
                aria-valuetext={plural(sets, 'set')}
              />
              <motion.button type="button" className="icon-btn" aria-label="One set more" whileTap={{ scale: 0.9 }} onClick={() => setSets((n) => clamp(n + 1))} disabled={sets >= MAX_SETS}>
                <PlusIcon size={16} weight="bold" />
              </motion.button>
              <output className="earn-sets" htmlFor={rangeId}>{plural(sets, 'set')}</output>
            </div>
          </div>
        </Reveal>

        <Reveal className="earn-panel glow-card" delay={0.1}>
          <dl>
            <div className="earn-row">
              <dt>You pay</dt>
              <dd><AnimatedNumber value={pay} duration={0.6} /></dd>
            </div>
            <div className="earn-row">
              <dt>Sells for, at our list price</dt>
              <dd><AnimatedNumber value={sells} duration={0.6} /></dd>
            </div>
            <div className="earn-row earn-profit">
              <dt>Your estimated profit</dt>
              <dd><AnimatedNumber value={profit} from={0} whenInView duration={0.8} /></dd>
            </div>
          </dl>
          <p className="earn-note">
            {pkg.unit_count > 0 && <>{plural(sets * pkg.unit_count, 'piece')} at {peso(pkg.discounted_total / pkg.unit_count)} each. </>}
            Your own selling price may differ.
          </p>
        </Reveal>
      </div>
    </section>
  );
}

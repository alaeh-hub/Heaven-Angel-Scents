import { useId, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { PlusIcon } from '@phosphor-icons/react';
import { EASE_OUT, SPRING } from '../../motion.js';
import Reveal from '../Reveal.jsx';
import BlurText from '../BlurText.jsx';

const FAQS = [
  ["What's the difference between a Distributor and a Reseller?",
    'A Distributor resells further down a chain of their own (think other stores or sub-resellers). A Reseller sells directly to end customers. Both are bulk buyers, and some packages are open to either.'],
  ['How does the discount work?',
    "Each package's discount is applied to the combined reference price of everything inside it. The price shown on each card and package page is exactly what you'd pay if the order goes through."],
  ['Do I need an account to inquire?',
    "No. Open any package and send an inquiry with your contact details. There's no login required; our team reaches out directly once we receive it."],
  ['How long until someone follows up?',
    'Our team is notified as soon as an inquiry comes in and follows up by phone or email using the details you provide.'],
  ['Is there a minimum order for a package?',
    "Each package is already sized as a bundle, so there's no separate minimum on top of it. The quantities and item count shown are exactly what you'd receive."],
  ['Can I ask for a custom mix of products?',
    "Start with an inquiry on the package closest to what you have in mind. Mention what you'd like adjusted, and our team will let you know what's possible."],
];

function FaqItem({ n, question, answer, open, onToggle }) {
  const id = useId();
  return (
    <div className="faq-item glow-card glow-fill">
      <button type="button" className="faq-q" aria-expanded={open} aria-controls={id} onClick={onToggle}>
        <span className="faq-q-label">
          <span className="faq-n" aria-hidden="true">{String(n).padStart(2, '0')}</span>
          {question}
        </span>
        <motion.span animate={{ rotate: open ? 45 : 0 }} transition={SPRING} style={{ display: 'inline-flex' }}>
          <PlusIcon size={20} weight="bold" />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={id}
            className="faq-a"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: EASE_OUT }}
          >
            <p>{answer}</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default function Faq() {
  const [open, setOpen] = useState(0);
  return (
    <section className="section section-alt" id="faq">
      <div className="container faq-inner">
        <Reveal className="section-head">
          <div className="eyebrow">FAQ</div>
          <BlurText className="title-xl" text="Questions, answered." />
          <p className="lede">The questions partners ask most, before that first inquiry.</p>
        </Reveal>
        <Reveal className="faq-list" amount={0.15}>
          {FAQS.map(([question, answer], i) => (
            <FaqItem
              key={question}
              n={i + 1}
              question={question}
              answer={answer}
              open={open === i}
              onToggle={() => setOpen((current) => (current === i ? null : i))}
            />
          ))}
        </Reveal>
      </div>
    </section>
  );
}

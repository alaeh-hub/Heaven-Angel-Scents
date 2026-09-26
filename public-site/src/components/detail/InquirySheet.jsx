import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { CheckIcon, WarningCircleIcon, XIcon } from '@phosphor-icons/react';
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock.js';
import { EASE_OUT, SPRING, SPRING_SHEET } from '../../motion.js';

const EMPTY = { company_name: '', contact_person: '', phone: '', email: '', address: '', message: '' };
const ANY = '';
/** How each contact method reads in "We reach you by ___." */
const VIA = { Call: 'phone call', SMS: 'SMS', Viber: 'Viber', Email: 'email' };

/**
 * The inquiry dialog, for one package (from its detail view) or for no
 * package at all (the general "Send an inquiry" buttons, see
 * InquiryProvider). `fixedType` fixes "You are a" for a package scoped
 * to one partner type; otherwise it's asked. The name field relabels for
 * a Reseller, who may be an individual rather than a business (it's
 * stored as company_name either way, see routes/portal.py).
 *
 * `submit(payload)` does the actual request (and any CSRF retry) and
 * resolves to the server's {message, reference}. Server errors show
 * inline above the buttons; success swaps the form for a confirmation
 * with the inquiry's reference and what happens next.
 *
 * Inside the package sheet its fixed overlay covers the sheet (the
 * sheet's transform makes it the containing block); opened from the
 * page itself, it covers the whole page.
 */
export default function InquirySheet({
  title,
  intro,
  fixedType = '',
  partnerTypes,
  contactMethods = [],
  replyTime,
  initialMessage = '',
  open,
  onClose,
  submit,
}) {
  const [partnerType, setPartnerType] = useState(fixedType);
  const [fields, setFields] = useState(EMPTY);
  const [preferred, setPreferred] = useState(ANY);
  const [status, setStatus] = useState('idle'); // idle | sending | sent
  const [error, setError] = useState('');
  const [sent, setSent] = useState({ message: '', reference: '', via: '' });
  const firstFieldRef = useRef(null);
  useBodyScrollLock(open);

  // A general inquiry can arrive pre-filled ("I'm interested in ..."
  // from a product card); only fill an untouched message box.
  useEffect(() => {
    if (open && initialMessage) setFields((f) => (f.message ? f : { ...f, message: initialMessage }));
  }, [open, initialMessage]);

  useEffect(() => {
    if (!open) return undefined;
    const focusTimer = setTimeout(() => firstFieldRef.current?.focus(), 120);
    const onKey = (e) => {
      if (e.key === 'Escape') {
        // Don't also close the package sheet underneath.
        e.stopImmediatePropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      clearTimeout(focusTimer);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open, onClose]);

  const resetAfterClose = () => {
    if (status !== 'sent') return;
    setStatus('idle');
    setFields(EMPTY);
    setPreferred(ANY);
    setPartnerType(fixedType);
  };

  const isReseller = partnerType === 'Reseller';
  const set = (name) => (e) => setFields((f) => ({ ...f, [name]: e.target.value }));

  const onSubmit = async (e) => {
    e.preventDefault();
    setStatus('sending');
    setError('');
    try {
      const result = await submit({ ...fields, partner_type: partnerType, preferred_contact: preferred });
      setSent({ message: result.message, reference: result.reference, via: preferred });
      setStatus('sent');
    } catch (err) {
      setError(err.message);
      setStatus('idle');
    }
  };

  // The server's message already carries the reply time, so it isn't
  // repeated here.
  const nextSteps = [
    'Our team reviews your inquiry.',
    `We reach you by ${VIA[sent.via] || 'phone or email'}.`,
    'We confirm the details and set you up as a partner.',
  ];

  return (
    <AnimatePresence onExitComplete={resetAfterClose}>
      {open && (
        <motion.div
          className="inquiry-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.25 }}
          onClick={(e) => e.target === e.currentTarget && onClose()}
        >
          <motion.div
            className="inquiry"
            role="dialog"
            aria-modal="true"
            aria-labelledby="inquiry-title"
            initial={{ opacity: 0, y: 40, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.97 }}
            transition={SPRING_SHEET}
          >
            <AnimatePresence mode="wait" initial={false}>
              {status === 'sent' ? (
                <motion.div
                  key="done"
                  className="inquiry-done"
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.3, ease: EASE_OUT }}
                >
                  <motion.span
                    className="inquiry-done-icon"
                    initial={{ scale: 0.4, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ type: 'spring', stiffness: 420, damping: 18, delay: 0.08 }}
                  >
                    <CheckIcon size={34} weight="bold" />
                  </motion.span>
                  <h2 id="inquiry-title">Inquiry sent</h2>
                  <p>{sent.message}</p>
                  {sent.reference && (
                    <p className="inquiry-ref">
                      Your reference <strong>{sent.reference}</strong>
                    </p>
                  )}
                  {/* The next steps tick in one after another, after the check. */}
                  <ol className="inquiry-next">
                    {nextSteps.map((step, i) => (
                      <motion.li
                        key={step}
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ ...SPRING, delay: 0.35 + i * 0.12 }}
                      >
                        <motion.span
                          className="inquiry-next-mark"
                          initial={{ scale: 0 }}
                          animate={{ scale: 1 }}
                          transition={{ type: 'spring', stiffness: 500, damping: 22, delay: 0.42 + i * 0.12 }}
                        >
                          <CheckIcon size={12} weight="bold" />
                        </motion.span>
                        {step}
                      </motion.li>
                    ))}
                  </ol>
                  <button type="button" className="btn btn-secondary" onClick={onClose} style={{ marginTop: 8 }}>
                    Done
                  </button>
                </motion.div>
              ) : (
                <motion.div
                  key="form"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.2 }}
                >
                  <div className="inquiry-head">
                    <div>
                      <h2 id="inquiry-title">{title}</h2>
                      <p>
                        {intro}
                        {replyTime && <> We reply {replyTime}.</>}
                      </p>
                    </div>
                    <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
                      <XIcon size={16} weight="bold" />
                    </button>
                  </div>

                  <form onSubmit={onSubmit}>
                    {/* Fields pair up two per .inquiry-row so the dialog stays
                        short on desktop; the rows stack on phones. */}
                    <div className="inquiry-row">
                      <div className="field">
                        <label htmlFor="partner_type">You are a</label>
                        {fixedType ? (
                          <input id="partner_type" className="input" value={fixedType} disabled />
                        ) : (
                          <select
                            id="partner_type"
                            ref={firstFieldRef}
                            className="input"
                            required
                            value={partnerType}
                            onChange={(e) => setPartnerType(e.target.value)}
                          >
                            <option value="">Select…</option>
                            {partnerTypes.map((t) => <option key={t} value={t}>{t}</option>)}
                          </select>
                        )}
                      </div>

                      <div className="field">
                        <label htmlFor="company_name">{isReseller ? 'Your full name' : 'Business or company name'}</label>
                        <input
                          id="company_name"
                          ref={fixedType ? firstFieldRef : undefined}
                          className="input"
                          required
                          maxLength={150}
                          autoComplete={isReseller ? 'name' : 'organization'}
                          placeholder={isReseller ? 'e.g. Maria Santos' : 'e.g. Golden Scent Trading'}
                          value={fields.company_name}
                          onChange={set('company_name')}
                        />
                      </div>
                    </div>

                    <div className="inquiry-row">
                      <div className="field">
                        <label htmlFor="contact_person">Contact person</label>
                        <input
                          id="contact_person"
                          className="input"
                          required
                          maxLength={100}
                          autoComplete="name"
                          value={fields.contact_person}
                          onChange={set('contact_person')}
                        />
                        {isReseller && <p className="help">Inquiring as an individual? This can match your name.</p>}
                      </div>

                      <div className="field">
                        <label htmlFor="address">Address <span className="optional">(optional)</span></label>
                        <input
                          id="address"
                          className="input"
                          maxLength={255}
                          autoComplete="address-level2"
                          placeholder="City or area you operate from"
                          value={fields.address}
                          onChange={set('address')}
                        />
                      </div>
                    </div>

                    <div className="inquiry-row">
                      <div className="field">
                        <label htmlFor="phone">Phone</label>
                        {/* Every punctuation mark in `pattern` is escaped: browsers
                            compile it with the regex `v` flag, which rejects a bare
                            ( ) or - inside a character class. */}
                        <input
                          id="phone"
                          className="input"
                          type="tel"
                          required
                          maxLength={30}
                          autoComplete="tel"
                          placeholder="09xx xxx xxxx"
                          pattern="[0-9+\-\(\)\.\s]{7,30}"
                          title="Digits, spaces, +, -, and ( ) only."
                          value={fields.phone}
                          onChange={set('phone')}
                        />
                      </div>
                      <div className="field">
                        <label htmlFor="email">Email</label>
                        <input
                          id="email"
                          className="input"
                          type="email"
                          required
                          maxLength={120}
                          autoComplete="email"
                          placeholder="name@example.com"
                          value={fields.email}
                          onChange={set('email')}
                        />
                      </div>
                    </div>

                    {contactMethods.length > 0 && (
                      <fieldset className="field contact-pref">
                        <legend>Best way to reach you <span className="optional">(optional)</span></legend>
                        <div className="contact-pref-options">
                          {[ANY, ...contactMethods].map((method) => (
                            <label key={method || 'any'} className="contact-pref-option">
                              <input
                                type="radio"
                                name="preferred_contact"
                                value={method}
                                checked={preferred === method}
                                onChange={() => setPreferred(method)}
                              />
                              {preferred === method && (
                                <motion.span layoutId="contact-pref-pill" className="contact-pref-pill" transition={SPRING} />
                              )}
                              <span className="contact-pref-label">{method || 'Any'}</span>
                            </label>
                          ))}
                        </div>
                        <p className="help">We&apos;ll only use your details to follow up about this inquiry.</p>
                      </fieldset>
                    )}

                    <div className="field">
                      <label htmlFor="message">Message <span className="optional">(optional)</span></label>
                      <textarea
                        id="message"
                        className="input"
                        maxLength={500}
                        rows={2}
                        placeholder="Anything else we should know?"
                        value={fields.message}
                        onChange={set('message')}
                      />
                    </div>

                    <AnimatePresence initial={false}>
                      {error && (
                        <motion.div
                          className="inquiry-error"
                          role="alert"
                          initial={{ opacity: 0, y: -6 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0 }}
                          transition={SPRING}
                        >
                          <WarningCircleIcon size={18} weight="fill" />
                          {error}
                        </motion.div>
                      )}
                    </AnimatePresence>

                    <div className="inquiry-actions">
                      <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                      <motion.button
                        type="submit"
                        className="btn btn-primary"
                        disabled={status === 'sending'}
                        whileTap={{ scale: 0.97 }}
                      >
                        {status === 'sending' ? <><span className="spinner" aria-hidden="true" /> Sending…</> : 'Send inquiry'}
                      </motion.button>
                    </div>
                  </form>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

import { motion } from 'motion/react';
import { ChatsCircleIcon, EnvelopeSimpleIcon, MessengerLogoIcon, PhoneIcon } from '@phosphor-icons/react';
import { useSite } from '../InquiryProvider.jsx';
import BlurText from '../BlurText.jsx';
import Reveal from '../Reveal.jsx';

/** Viber deep link for a number stored with its country code. */
const viberLink = (number) => `viber://chat?number=${encodeURIComponent(`+${number.replace(/\D/g, '')}`)}`;

/**
 * The last thing on the page before the footer: one clear next step
 * (the general inquiry form) and, beside it, every direct line HQ has
 * configured (config.py's PORTAL_CONTACT_*). Channels that aren't set
 * are left out, so there's never a placeholder number on the page.
 */
export default function ClosingCta() {
  const { site, openInquiry } = useSite();
  const contact = site?.contact || {};
  const channels = [
    contact.phone && { key: 'phone', Icon: PhoneIcon, label: contact.phone, href: `tel:${contact.phone.replace(/[^\d+]/g, '')}` },
    contact.email && { key: 'email', Icon: EnvelopeSimpleIcon, label: contact.email, href: `mailto:${contact.email}` },
    contact.viber && { key: 'viber', Icon: ChatsCircleIcon, label: 'Message us on Viber', href: viberLink(contact.viber) },
    /^https?:\/\//i.test(contact.messenger_url || '') && { key: 'messenger', Icon: MessengerLogoIcon, label: 'Message us on Messenger', href: contact.messenger_url, external: true },
  ].filter(Boolean);

  return (
    <section className="section closing" id="contact">
      <div className="container">
        <Reveal className={`closing-panel${channels.length ? '' : ' closing-panel-solo'}`}>
          <div className="closing-copy">
            <BlurText className="title-xl" text="Ready to stock our scents?" />
            <p className="lede">
              Tell us what you have in mind and our team takes it from there
              {site?.reply_time ? `, ${site.reply_time}.` : '.'}
            </p>
            <motion.button type="button" className="btn btn-primary" onClick={() => openInquiry()} whileTap={{ scale: 0.97 }}>
              Send an inquiry
            </motion.button>
          </div>

          {channels.length > 0 && (
            <ul className="closing-channels" aria-label="Contact us directly">
              {channels.map(({ key, Icon, label, href, external }, i) => (
                <Reveal as="li" key={key} delay={0.1 + i * 0.06}>
                  <a
                    className="closing-channel"
                    href={href}
                    {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
                  >
                    <span className="closing-channel-icon"><Icon size={20} weight="duotone" /></span>
                    {label}
                  </a>
                </Reveal>
              ))}
            </ul>
          )}
        </Reveal>
      </div>
    </section>
  );
}

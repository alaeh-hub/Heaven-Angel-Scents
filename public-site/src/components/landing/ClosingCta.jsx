import { motion } from 'motion/react';
import {
  ArrowRightIcon, ChatsCircleIcon, EnvelopeSimpleIcon, MessengerLogoIcon, PhoneIcon,
} from '@phosphor-icons/react';
import { useSite } from '../InquiryProvider.jsx';
import BlurText from '../BlurText.jsx';
import { AnimatedRays } from '@/components/ui/animated-rays';
import Reveal from '../Reveal.jsx';

/** Viber deep link for a number stored with its country code. */
const viberLink = (number) => `viber://chat?number=${encodeURIComponent(`+${number.replace(/\D/g, '')}`)}`;

/**
 * The last thing on the page before the footer, built to get an inquiry:
 * a short headline, one big button, three reasons to send it (each one a
 * promise the page already makes elsewhere) and, under them, every
 * direct line HQ has configured (config.py's PORTAL_CONTACT_*). Channels
 * that aren't set are left out, so there's never a placeholder number.
 */
export default function ClosingCta() {
  const { site, openInquiry } = useSite();
  const contact = site?.contact || {};
  const channels = [
    contact.phone && { key: 'phone', Icon: PhoneIcon, label: contact.phone, href: `tel:${contact.phone.replace(/[^\d+]/g, '')}` },
    contact.email && { key: 'email', Icon: EnvelopeSimpleIcon, label: contact.email, href: `mailto:${contact.email}` },
    contact.viber && { key: 'viber', Icon: ChatsCircleIcon, label: 'Viber', href: viberLink(contact.viber) },
    /^https?:\/\//i.test(contact.messenger_url || '') && { key: 'messenger', Icon: MessengerLogoIcon, label: 'Messenger', href: contact.messenger_url, external: true },
  ].filter(Boolean);

  const reasons = [
    { key: 'price', title: 'Partner pricing', body: 'Below our regular list, on every bundle.' },
    { key: 'reply', title: 'A personal reply', body: site?.reply_time ? `We answer ${site.reply_time}.` : 'A real person answers every inquiry.' },
    { key: 'restock', title: 'Easy restocking', body: 'Reorder in minutes when shelves run low.' },
  ];

  return (
    <section className="section closing" id="contact">
      <div className="container">
        <Reveal className="closing-stage">
          <AnimatedRays className="rays" />
          <p className="closing-eyebrow">Partner with us</p>
          <BlurText className="title-xl" text="Ready to stock our scents?" />
          <p className="lede closing-lede">
            Your shelves, our signatures. Tell us what you have in mind and we will shape the order around you.
          </p>

          <div className="closing-action">
            <motion.button type="button" className="btn btn-primary closing-btn" onClick={() => openInquiry()} whileTap={{ scale: 0.97 }}>
              Send an inquiry <ArrowRightIcon size={18} weight="bold" />
            </motion.button>
            <span className="closing-note">Free to ask. No account needed.</span>
          </div>

          <ul className="closing-reasons">
            {reasons.map(({ key, title, body }, i) => (
              <Reveal as="li" key={key} delay={0.08 + i * 0.07}>
                <strong>{title}</strong>
                <span>{body}</span>
              </Reveal>
            ))}
          </ul>

          {channels.length > 0 && (
            <div className="closing-direct">
              <span className="closing-direct-label">Or reach us directly</span>
              <ul className="closing-channels" aria-label="Contact us directly">
                {channels.map(({ key, Icon, label, href, external }) => (
                  <li key={key}>
                    <a
                      className="closing-channel"
                      href={href}
                      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
                    >
                      <Icon size={18} weight="duotone" />
                      {label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Reveal>
      </div>
    </section>
  );
}

// Footer details, kept in one place like about.js and partner.js.
//
// TODO(content): everything below is PLACEHOLDER information. Replace
// it with the real details before sharing the portal link.
//
// Phone and email: if PORTAL_CONTACT_PHONE / PORTAL_CONTACT_EMAIL are
// set in .env (the same values the closing "Ready to stock our scents?"
// band uses), the footer shows those instead of the ones below, so the
// two never disagree. Set them there and these become fallbacks only.
//
// Socials: remove any network you don't use; the footer only renders
// what's listed. `network` picks the logo (facebook, instagram, tiktok,
// youtube, messenger, x).

export const FOOTER = {
  blurb: 'Curated fragrance bundles for distributors and resellers, priced below our regular list.',
  phone: '+63 917 000 0000',
  email: 'partners@heavenandangel.example',
  address: 'Unit 1, Sample Building, Quezon City, Metro Manila',
  hours: 'Monday to Saturday, 9 AM to 6 PM',
  socials: [
    { network: 'facebook', label: 'Facebook', href: 'https://www.facebook.com/heavenandangel.example' },
    { network: 'instagram', label: 'Instagram', href: 'https://www.instagram.com/heavenandangel.example' },
    { network: 'tiktok', label: 'TikTok', href: 'https://www.tiktok.com/@heavenandangel.example' },
    { network: 'youtube', label: 'YouTube', href: 'https://www.youtube.com/@heavenandangel.example' },
    { network: 'messenger', label: 'Messenger', href: 'https://m.me/heavenandangel.example' },
  ],
};

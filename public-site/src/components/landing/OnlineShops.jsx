import { ArrowUpRightIcon, ShoppingBagIcon, ShoppingCartIcon, StorefrontIcon } from '@phosphor-icons/react';
import { FOOTER } from '../../content/footer.js';
import Reveal from '../Reveal.jsx';

// Phosphor has no official Shopee/Lazada/TikTok Shop marks, so each
// store gets a generic icon instead, tinted with its own brand color.
const SHOP_ICONS = {
  shopee: ShoppingBagIcon,
  lazada: ShoppingCartIcon,
  tiktokshop: StorefrontIcon,
};

const SHOP_COLORS = {
  shopee: '#EE4D2D',
  lazada: '#0F146D',
  tiktokshop: '#FE2C55',
};

const SHOP_BLURBS = {
  shopee: 'Browse the full catalog and check out with Shopee buyer protection.',
  lazada: 'Shop the collection with Lazada vouchers and cash on delivery.',
  tiktokshop: 'Order straight from our TikTok videos and lives.',
};

/**
 * For visitors who already know what they want and would rather order
 * now than send an inquiry: our official storefronts on the marketplaces
 * shoppers already trust. Reuses FOOTER.marketplaces (content/footer.js)
 * so the footer and this section never fall out of sync.
 */
export default function OnlineShops() {
  const shops = FOOTER.marketplaces || [];
  if (!shops.length) return null;

  return (
    <section className="section shop-online" id="shop-online">
      <div className="container">
        <Reveal className="section-head center">
          <div className="eyebrow">Buy direct</div>
          <h2 className="title-xl">Already know what you want?</h2>
          <p className="lede">Skip the inquiry and order straight from our official stores.</p>
        </Reveal>

        <div className="shop-grid">
          {shops.map(({ network, label, href }, i) => {
            const Icon = SHOP_ICONS[network];
            return (
              <Reveal
                as="a"
                key={network}
                className="shop-card"
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                delay={i * 0.08}
                style={{ '--brand': SHOP_COLORS[network] }}
                whileHover={{ y: -6 }}
                whileTap={{ scale: 0.98 }}
              >
                <span className="shop-card-icon">
                  {Icon && <Icon size={26} weight="fill" />}
                </span>
                <span className="shop-card-body">
                  <span className="shop-card-name">{label}</span>
                  <span className="shop-card-blurb">{SHOP_BLURBS[network]}</span>
                </span>
                <span className="shop-card-arrow">
                  <ArrowUpRightIcon size={18} weight="bold" />
                </span>
              </Reveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}

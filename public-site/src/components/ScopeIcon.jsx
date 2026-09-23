import { HandshakeIcon, StorefrontIcon, TruckIcon } from '@phosphor-icons/react';

/** Truck for Distributor packages, storefront for Reseller, handshake for Both. */
export default function ScopeIcon({ scope, size = 24 }) {
  const Icon = scope === 'Distributor' ? TruckIcon : scope === 'Reseller' ? StorefrontIcon : HandshakeIcon;
  return <Icon size={size} weight="duotone" aria-hidden="true" />;
}

/** "For distributors", "For resellers", "For everyone". */
export const scopeLabel = (scope) =>
  scope === 'Distributor' ? 'For distributors' : scope === 'Reseller' ? 'For resellers' : 'Distributors and resellers';

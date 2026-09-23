// Shared motion vocabulary, so every transition on the portal moves with
// the same physics. Springs for anything the visitor directly causes
// (tabs, cards, sheets); a long ease-out for things that simply arrive.

export const EASE_OUT = [0.22, 1, 0.36, 1];

/** Snappy, no overshoot: tab pills, highlights, layout shifts. */
export const SPRING = { type: 'spring', stiffness: 380, damping: 36, mass: 0.9 };

/** Softer, a hint of settle: cards entering, the hero bottles. */
export const SPRING_SOFT = { type: 'spring', stiffness: 140, damping: 22, mass: 1 };

/** Sheets and dialogs sliding in. */
export const SPRING_SHEET = { type: 'spring', stiffness: 300, damping: 34, mass: 1 };

export const ARRIVE = { duration: 0.9, ease: EASE_OUT };

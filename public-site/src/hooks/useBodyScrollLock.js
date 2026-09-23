import { useEffect } from 'react';

// Counted, so the inquiry form opening/closing inside the package
// dialog doesn't unlock the page behind that dialog.
let locks = 0;

export function useBodyScrollLock(active) {
  useEffect(() => {
    if (!active) return undefined;
    locks += 1;
    document.body.style.overflow = 'hidden';
    return () => {
      locks -= 1;
      if (locks === 0) document.body.style.overflow = '';
    };
  }, [active]);
}

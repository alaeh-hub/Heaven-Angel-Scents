import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'motion/react';
import { CheckCircleIcon, WarningCircleIcon } from '@phosphor-icons/react';
import { SPRING_SOFT } from '../motion.js';

// Short-lived notices ("that package is no longer available") that have
// to survive a route change, so they live above the router. Form errors
// don't come through here; they're shown inline in the form.

const ToastContext = createContext(() => {});
const VISIBLE_MS = 4500;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(0);
  const timers = useRef(new Set());

  const showToast = useCallback((text, tone = 'success') => {
    const id = ++nextId.current;
    setToasts((list) => [...list, { id, text, tone }]);
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      setToasts((list) => list.filter((t) => t.id !== id));
    }, VISIBLE_MS);
    timers.current.add(timer);
  }, []);

  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  return (
    <ToastContext.Provider value={showToast}>
      {children}
      {createPortal(
        <div className="toasts" role="status" aria-live="polite">
          <AnimatePresence initial={false}>
            {toasts.map((t) => (
              <motion.div
                key={t.id}
                layout
                className={`toast toast-${t.tone}`}
                initial={{ opacity: 0, y: -16, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -10, scale: 0.96 }}
                transition={SPRING_SOFT}
              >
                {t.tone === 'error' ? <WarningCircleIcon size={20} weight="fill" /> : <CheckCircleIcon size={20} weight="fill" />}
                {t.text}
              </motion.div>
            ))}
          </AnimatePresence>
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

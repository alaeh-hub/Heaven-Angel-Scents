import { useCallback, useEffect, useRef, useState } from 'react';

const MIN_THUMB = 44; // px, so the thumb stays easy to grab on a very long page
const IDLE_MS = 900; // how long the thumb stays bright after the last scroll

/**
 * A scrollbar drawn by the page, for one scrolling element (the package
 * dialog's body). The browser's own scrollbar is hidden on that element
 * (see `.sheet-scroll`); this one stays in sync with it:
 *   - the thumb's size and place follow the content and the scroll
 *   - drag the thumb, or click the track to jump there
 *   - it brightens while scrolling or hovered and rests quietly otherwise
 * Touch devices keep their normal momentum scrolling; the thumb is then a
 * thin position indicator that ignores touches. The scrolling element
 * itself is untouched, so keyboard, wheel and screen readers work as before.
 */
export default function CustomScrollbar({ target }) {
  const trackRef = useRef(null);
  const [thumb, setThumb] = useState({ top: 0, height: 0, visible: false });
  const [awake, setAwake] = useState(false);
  const [dragging, setDragging] = useState(false);
  const idle = useRef(0);
  const drag = useRef(null);

  const measure = useCallback(() => {
    const el = target.current;
    const track = trackRef.current;
    if (!el || !track) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    const trackH = track.clientHeight;
    if (scrollHeight <= clientHeight + 1 || trackH <= 0) {
      setThumb((t) => (t.visible ? { top: 0, height: 0, visible: false } : t));
      return;
    }
    const height = Math.max(MIN_THUMB, (clientHeight / scrollHeight) * trackH);
    const top = (scrollTop / (scrollHeight - clientHeight)) * (trackH - height);
    setThumb({ top, height, visible: true });
  }, [target]);

  const wake = useCallback(() => {
    setAwake(true);
    window.clearTimeout(idle.current);
    idle.current = window.setTimeout(() => setAwake(false), IDLE_MS);
  }, []);

  // Follow the element: its scroll, its own size, and its content's size
  // (the package loads after the sheet opens, so the height changes).
  useEffect(() => {
    const el = target.current;
    if (!el) return undefined;
    const onScroll = () => { measure(); wake(); };
    measure();
    el.addEventListener('scroll', onScroll, { passive: true });
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    // A child that replaces the first one (a different package opened
    // from inside the sheet) is picked up here.
    const mo = new MutationObserver(() => {
      if (el.firstElementChild) ro.observe(el.firstElementChild);
      measure();
    });
    mo.observe(el, { childList: true });
    window.addEventListener('resize', measure);
    return () => {
      el.removeEventListener('scroll', onScroll);
      ro.disconnect();
      mo.disconnect();
      window.removeEventListener('resize', measure);
      window.clearTimeout(idle.current);
    };
  }, [target, measure, wake]);

  const onThumbDown = (e) => {
    const el = target.current;
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { y: e.clientY, scrollTop: el.scrollTop };
    setDragging(true);
  };
  const onThumbMove = (e) => {
    const d = drag.current;
    const el = target.current;
    const track = trackRef.current;
    if (!d || !el || !track) return;
    const travel = track.clientHeight - thumb.height;
    if (travel <= 0) return;
    const ratio = (el.scrollHeight - el.clientHeight) / travel;
    el.scrollTop = d.scrollTop + (e.clientY - d.y) * ratio;
  };
  const onThumbUp = (e) => {
    drag.current = null;
    setDragging(false);
    if (e.currentTarget.hasPointerCapture?.(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    wake();
  };

  // A click on the empty track centres the thumb there.
  const onTrackDown = (e) => {
    if (e.target !== e.currentTarget) return;
    const el = target.current;
    const track = trackRef.current;
    if (!el || !track) return;
    const rect = track.getBoundingClientRect();
    const travel = track.clientHeight - thumb.height;
    if (travel <= 0) return;
    const p = Math.min(1, Math.max(0, (e.clientY - rect.top - thumb.height / 2) / travel));
    el.scrollTo({ top: p * (el.scrollHeight - el.clientHeight), behavior: 'smooth' });
  };

  return (
    <div
      ref={trackRef}
      className={`cscroll${thumb.visible ? '' : ' is-hidden'}${awake || dragging ? ' is-awake' : ''}${dragging ? ' is-dragging' : ''}`}
      aria-hidden="true"
      onPointerDown={onTrackDown}
      onPointerEnter={wake}
    >
      <div
        className="cscroll-thumb"
        style={{ height: thumb.height, transform: `translateY(${thumb.top}px)` }}
        onPointerDown={onThumbDown}
        onPointerMove={onThumbMove}
        onPointerUp={onThumbUp}
        onPointerCancel={onThumbUp}
      />
    </div>
  );
}

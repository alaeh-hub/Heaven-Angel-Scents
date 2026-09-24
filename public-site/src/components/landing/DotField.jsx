import { useEffect, useRef } from 'react';
import { useReducedMotion } from 'motion/react';

// Grid spacing and dot sizing, all in CSS px.
const SPACING = 34;
const BASE_R = 1.3;
const MAX_R = 3;
const REACH = 160; // how far a dot feels the pointer
const PUSH = 10; // how far a dot nudges away from the pointer, at its closest

// Resting and lit colours (matches --ink-3 and --gold-ink in tokens.css;
// a canvas can't read CSS custom properties, so these are copied in).
const REST = [117, 117, 125];
const NEAR = [224, 192, 104];
const REST_ALPHA = 0.4;
const NEAR_ALPHA = 0.9;
const REST_COLOR = `rgb(${REST.join(' ')} / ${REST_ALPHA})`;

const clamp01 = (x) => Math.min(1, Math.max(0, x));
const smooth = (x) => x * x * (3 - 2 * x);

/**
 * A grid of dots that light up, grow and nudge away from the pointer.
 * The pointer is tracked only over the element this renders into, so it
 * reacts to the cursor within that section and nowhere else on the page
 * (drop it into a `position: relative` container as a background layer,
 * e.g. absolutely positioned behind that section's real content).
 *
 * Static and non-interactive under reduced motion; the animation loop
 * pauses while off screen. Ships its own base CSS (`.dot-field` in
 * landing.css: `position: absolute; inset: 0`), so it fills its
 * positioned ancestor on its own rather than depending on the page
 * around it to size that div correctly.
 */
export default function DotField({ className = '' }) {
  const reduce = useReducedMotion();
  const hostRef = useRef(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    const canvas = document.createElement('canvas');
    canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;';
    canvas.setAttribute('aria-hidden', 'true');
    host.appendChild(canvas);
    const ctx = canvas.getContext('2d');

    let dots = [];
    let w = 0;
    let h = 0;

    /** Rebuild the grid to fill the host at its current size. */
    function layout() {
      const rect = host.getBoundingClientRect();
      w = Math.max(1, Math.round(rect.width));
      h = Math.max(1, Math.round(rect.height));
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const cols = Math.max(2, Math.round(w / SPACING) + 1);
      const rows = Math.max(2, Math.round(h / SPACING) + 1);
      const gx = w / (cols - 1);
      const gy = h / (rows - 1);
      const next = [];
      for (let j = 0; j < rows; j += 1) {
        for (let i = 0; i < cols; i += 1) {
          next.push({ rx: i * gx, ry: j * gy, x: i * gx, y: j * gy, r: BASE_R });
        }
      }
      dots = next;
    }
    layout();

    const drawStill = () => {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = REST_COLOR;
      ctx.beginPath();
      for (const d of dots) {
        ctx.moveTo(d.rx + BASE_R, d.ry);
        ctx.arc(d.rx, d.ry, BASE_R, 0, Math.PI * 2);
      }
      ctx.fill();
    };

    // Reduced motion: one flat, unmoving grid, no pointer tracking at all.
    if (reduce) {
      drawStill();
      const ro = new ResizeObserver(() => { layout(); drawStill(); });
      ro.observe(host);
      return () => { ro.disconnect(); canvas.remove(); };
    }

    const ro = new ResizeObserver(layout);
    ro.observe(host);

    let pointer = null; // {x, y} in the canvas's own CSS px, null when the cursor is elsewhere
    const onMove = (e) => {
      const rect = host.getBoundingClientRect();
      pointer = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };
    const onLeave = () => { pointer = null; };
    host.addEventListener('pointermove', onMove);
    host.addEventListener('pointerleave', onLeave);

    let frame = 0;
    let last = 0;
    const active = []; // dots lit enough this frame to need their own fillStyle

    const tick = (now) => {
      frame = requestAnimationFrame(tick);
      // Clamped both ends: never negative (the first tick can be kicked
      // off manually, from IntersectionObserver, with its own
      // performance.now() reading that isn't guaranteed to precede the
      // next rAF timestamp) and capped so a dropped frame doesn't jump
      // the ease.
      const dt = last ? Math.max(0, Math.min(now - last, 50)) : 16.7;
      last = now;
      const k = 1 - Math.exp(-dt / 90); // time-based ease toward the target, each frame

      ctx.clearRect(0, 0, w, h);
      active.length = 0;

      // Most dots sit at rest and share one fillStyle; only the few near
      // the pointer need their own colour, so those are drawn in a
      // second pass instead of one setFillStyle per dot every frame.
      ctx.fillStyle = REST_COLOR;
      ctx.beginPath();
      for (const d of dots) {
        let targetR = BASE_R;
        let tx = d.rx;
        let ty = d.ry;
        if (pointer) {
          const dx = d.rx - pointer.x;
          const dy = d.ry - pointer.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < REACH) {
            const f = smooth(1 - dist / REACH); // 1 at the pointer, 0 at REACH
            targetR = BASE_R + (MAX_R - BASE_R) * f;
            const ux = dist > 0.001 ? dx / dist : 0;
            const uy = dist > 0.001 ? dy / dist : 1;
            tx = d.rx + ux * PUSH * f;
            ty = d.ry + uy * PUSH * f;
          }
        }
        // Defensive floor: `ctx.arc` throws on a negative radius, and r
        // must never reach it however dt/k behave.
        d.r = Math.max(0, d.r + (targetR - d.r) * k);
        d.x += (tx - d.x) * k;
        d.y += (ty - d.y) * k;

        const glow = clamp01((d.r - BASE_R) / (MAX_R - BASE_R));
        if (glow > 0.01) {
          active.push(d, glow);
        } else {
          ctx.moveTo(d.x + d.r, d.y);
          ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        }
      }
      ctx.fill();

      for (let i = 0; i < active.length; i += 2) {
        const d = active[i];
        const glow = active[i + 1];
        const rr = Math.round(REST[0] + (NEAR[0] - REST[0]) * glow);
        const gg = Math.round(REST[1] + (NEAR[1] - REST[1]) * glow);
        const bb = Math.round(REST[2] + (NEAR[2] - REST[2]) * glow);
        const a = REST_ALPHA + (NEAR_ALPHA - REST_ALPHA) * glow;
        ctx.fillStyle = `rgb(${rr} ${gg} ${bb} / ${a.toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !frame) {
        last = 0;
        tick(performance.now());
      } else if (!entry.isIntersecting && frame) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
    });
    io.observe(host);

    return () => {
      io.disconnect();
      ro.disconnect();
      host.removeEventListener('pointermove', onMove);
      host.removeEventListener('pointerleave', onLeave);
      cancelAnimationFrame(frame);
      canvas.remove();
    };
  }, [reduce]);

  return <div ref={hostRef} className={`dot-field ${className}`} aria-hidden="true" />;
}

import { useEffect, useRef, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';

// Seconds. Also the CSS opacity transition on `.backdrop-video video`.
const CROSSFADE = 0.6;

/**
 * A full-screen looping clip fixed behind the page, visible only while
 * one of the sections in `sectionIds` is on screen (those sections have a
 * transparent background, see `.section-video`). Adjacent ids share one
 * uninterrupted clip. Everything else on the page has a solid
 * background, so the clip never shows anywhere else.
 *
 * Native `loop` re-seeks to frame 0 and stutters at the loop point, so
 * this holds two copies of the clip: just before the playing one ends,
 * the other (rewound) starts and crossfades in over it, then they swap
 * roles for the next cycle. Nothing plays while off screen, and under
 * reduced motion the clip is never shown at all.
 */
export default function BackdropVideo({ src, sectionIds }) {
  const reduce = useReducedMotion();
  const wrapRef = useRef(null);
  const [visible, setVisible] = useState(false);
  const idsKey = sectionIds.join(' ');

  useEffect(() => {
    const sections = idsKey.split(' ').map((id) => document.getElementById(id)).filter(Boolean);
    if (!sections.length || reduce) return undefined;
    const onScreen = new Set();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) onScreen.add(entry.target);
        else onScreen.delete(entry.target);
      });
      setVisible(onScreen.size > 0);
    }, {
      // Ignore the last sliver at the top and bottom edges, so a section
      // merely peeking in (or tucked under the nav) doesn't start playback.
      rootMargin: '-12% 0px -12% 0px',
    });
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, [idsKey, reduce]);

  useEffect(() => {
    const videos = wrapRef.current ? [...wrapRef.current.querySelectorAll('video')] : [];
    if (videos.length < 2) return undefined;

    if (!visible) {
      videos.forEach((v) => v.pause());
      return undefined;
    }

    let active = videos.findIndex((v) => v.classList.contains('is-active'));
    if (active < 0) active = 0;
    let swapping = false;
    let swapTimer = 0;

    const onTimeUpdate = (e) => {
      const v = videos[active];
      if (e.target !== v || !v.duration || swapping || v.duration - v.currentTime > CROSSFADE) return;
      swapping = true;
      const next = videos[1 - active];
      next.currentTime = 0;
      next.play()?.catch(() => {});
      next.classList.add('is-active');
      v.classList.remove('is-active');
      swapTimer = setTimeout(() => {
        v.pause();
        active = 1 - active;
        swapping = false;
      }, CROSSFADE * 1000);
    };

    videos.forEach((v) => {
      v.muted = true;
      v.addEventListener('timeupdate', onTimeUpdate);
    });
    videos[active].play()?.catch(() => {});

    return () => {
      clearTimeout(swapTimer);
      videos.forEach((v) => v.removeEventListener('timeupdate', onTimeUpdate));
    };
  }, [visible]);

  if (reduce) return null;

  return (
    <motion.div
      ref={wrapRef}
      className="backdrop-video"
      aria-hidden="true"
      initial={false}
      animate={{ opacity: visible ? 1 : 0 }}
      transition={{ duration: 0.6, ease: 'easeOut' }}
    >
      <video className="is-active" muted playsInline preload="auto" src={src} />
      <video muted playsInline preload="auto" src={src} />
      <div className="backdrop-video-veil" />
    </motion.div>
  );
}

import { useEffect, useRef, useState } from 'react';
import { motion, useInView, useReducedMotion, useScroll, useTransform } from 'motion/react';
import { PauseIcon, PlayIcon, SpeakerHighIcon, SpeakerSlashIcon } from '@phosphor-icons/react';
import Reveal from '../Reveal.jsx';

/**
 * The behind-the-scenes film. The frame grows to full width as it
 * scrolls into place, then plays (muted) only while it's on screen.
 * Visitors can pause it or turn the sound on; under reduced motion it
 * never starts by itself.
 */
export default function CraftFilm() {
  const frameRef = useRef(null);
  const videoRef = useRef(null);
  const reduce = useReducedMotion();
  const inView = useInView(frameRef, { amount: 0.4 });
  const [playing, setPlaying] = useState(!reduce);
  const [muted, setMuted] = useState(true);

  const { scrollYProgress } = useScroll({ target: frameRef, offset: ['start end', 'start 0.2'] });
  const scale = useTransform(scrollYProgress, [0, 1], [0.86, 1]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (inView && playing) video.play().catch(() => setPlaying(false));
    else video.pause();
  }, [inView, playing]);

  // React doesn't reliably keep the `muted` property in sync on its own.
  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = muted;
  }, [muted]);

  return (
    <section className="section section-video" id="craft">
      <div className="container">
        <Reveal className="craft-head">
          <div className="eyebrow">Behind the scent</div>
          <h2 className="title-xl">Made in small runs, from blending to bottling.</h2>
          <p className="lede">
            Every batch is made in small runs, so quality stays consistent across every package you order.
          </p>
        </Reveal>

        <motion.div className="craft-frame" ref={frameRef} style={reduce ? undefined : { scale }}>
          <video
            ref={videoRef}
            src="/static/video/behind-the-scent.mp4"
            muted
            loop
            playsInline
            preload="metadata"
            aria-label="How our fragrances are made"
          />
          <button
            type="button"
            className="craft-toggle craft-toggle-sound"
            aria-label={muted ? 'Turn sound on' : 'Turn sound off'}
            onClick={() => setMuted((m) => !m)}
          >
            {muted ? <SpeakerSlashIcon size={18} weight="fill" /> : <SpeakerHighIcon size={18} weight="fill" />}
          </button>
          <button
            type="button"
            className="craft-toggle"
            aria-label={playing ? 'Pause film' : 'Play film'}
            onClick={() => setPlaying((p) => !p)}
          >
            {playing ? <PauseIcon size={18} weight="fill" /> : <PlayIcon size={18} weight="fill" />}
          </button>
        </motion.div>
      </div>
    </section>
  );
}

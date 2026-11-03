"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion, useMotionValue, useSpring, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";

export interface CursorCardProps {
  children: React.ReactNode;
  image: string;
  /** Where the subject's centre line sits across the image, 0 to 1 (0.5 when it is centred). */
  imageCentre?: number;
  /** Heading shown on the card. Defaults to the link text when it is a string. */
  title?: string;
  description: string;
  /** Small line at the foot of the card, e.g. "See it in the collection". */
  hint?: string;
  href?: string;
  className?: string;
}

const CARD_W = 232;
const GAP = 18; // distance between the pointer and the card
const EDGE = 12; // never closer than this to the window edge

/**
 * A link with a preview card that follows the pointer.
 *
 * The card stays inside the window: it flips to the left of the pointer
 * near the right edge and above it near the bottom, using its measured
 * size. The link itself is not draggable (a drag across it used to pick
 * up the link and leave the card stranded), the card follows the pointer
 * on a soft spring, and on devices without hover nothing is shown.
 */
export function CursorCard({ children, image, imageCentre = 0.5, title, description, hint, href = "#", className }: CursorCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [canHover, setCanHover] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const springConfig = { damping: 30, stiffness: 360, mass: 0.7 };
  const springX = useSpring(x, springConfig);
  const springY = useSpring(y, springConfig);

  useEffect(() => {
    setCanHover(window.matchMedia("(hover: hover) and (pointer: fine)").matches);
  }, []);

  const place = useCallback(
    (e: React.MouseEvent) => {
      const h = cardRef.current?.offsetHeight ?? 330;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      let left = e.clientX + GAP;
      if (left + CARD_W > vw - EDGE) left = e.clientX - GAP - CARD_W;
      let top = e.clientY + GAP;
      if (top + h > vh - EDGE) top = e.clientY - GAP - h;
      x.set(Math.max(EDGE, left));
      y.set(Math.max(EDGE, top));
    },
    [x, y]
  );

  const show = (e: React.MouseEvent) => {
    if (!canHover) return;
    // Jump to the pointer first, so the card never sweeps in from a corner.
    place(e);
    springX.jump(x.get());
    springY.jump(y.get());
    setIsHovered(true);
  };

  const heading = title ?? (typeof children === "string" ? children : undefined);

  return (
    <>
      <a
        href={href}
        draggable={false}
        onDragStart={(e) => e.preventDefault()}
        className={cn("cursor-card-link relative inline-block font-medium transition-colors", className)}
        onMouseEnter={show}
        onMouseLeave={() => setIsHovered(false)}
        onMouseMove={(e) => isHovered && place(e)}
        onMouseDown={() => setIsHovered(false)}
        onFocus={() => undefined}
      >
        {children}
      </a>

      {canHover &&
        typeof document !== "undefined" &&
        createPortal(
          <AnimatePresence>
            {isHovered && (
              <motion.div
                ref={cardRef}
                role="presentation"
                initial={{ opacity: 0, scale: 0.94 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.97 }}
                transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
                style={{ x: springX, y: springY, width: CARD_W }}
                className="cursor-card-pop pointer-events-none fixed left-0 top-0 z-50 select-none"
              >
                <div className="cursor-card-stage">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={image}
                    alt=""
                    draggable={false}
                    className="cursor-card-img"
                    style={{ transform: `translateX(${(0.5 - imageCentre) * 100}%)` }}
                  />
                </div>
                <div className="cursor-card-body">
                  {heading && <p className="cursor-card-title">{heading}</p>}
                  <p className="cursor-card-text">{description}</p>
                  {hint && <p className="cursor-card-hint">{hint}</p>}
                </div>
              </motion.div>
            )}
          </AnimatePresence>,
          document.body
        )}
    </>
  );
}

export default CursorCard;

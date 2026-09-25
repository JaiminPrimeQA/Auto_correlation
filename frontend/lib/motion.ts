import type { Variants } from "motion/react";

export const EASE_OUT: [number, number, number, number] = [0.23, 1, 0.32, 1];
export const EASE_IN_OUT: [number, number, number, number] = [0.77, 0, 0.175, 1];
export const SPRING = { type: "spring" as const, bounce: 0, duration: 0.4 };

/** Wizard step change: slide 18px in the direction of travel, fade, 2px blur. */
export const stepVariants: Variants = {
  enter: (dir: 1 | -1) => ({ opacity: 0, x: 18 * dir, filter: "blur(2px)" }),
  center: { opacity: 1, x: 0, filter: "blur(0px)", transition: { duration: 0.3, ease: EASE_OUT } },
  exit: (dir: 1 | -1) => ({ opacity: 0, x: -18 * dir, filter: "blur(2px)", transition: { duration: 0.18, ease: EASE_OUT } }),
};

/** Reduced motion: opacity only. */
export const fadeVariants: Variants = {
  enter: { opacity: 0 },
  center: { opacity: 1, transition: { duration: 0.18 } },
  exit: { opacity: 0, transition: { duration: 0.12 } },
};

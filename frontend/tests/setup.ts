import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import { MotionGlobalConfig } from "motion/react";

// Tests assert on DOM state, not on frames: finish every Motion animation at once.
MotionGlobalConfig.skipAnimations = true;

afterEach(() => cleanup());

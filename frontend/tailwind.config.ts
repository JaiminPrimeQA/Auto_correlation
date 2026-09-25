import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

// Legacy names are TEMPORARY aliases so every screen switches theme before
// each component is migrated. Task 14 removes the `legacy` block.
const legacy = {
  ink: token("surface-2"),
  panel: token("surface"),
  edge: token("border"),
  brand: token("accent"),
  slate: {
    50: token("surface-2"), 100: token("ink"), 200: token("ink"), 300: token("ink"),
    400: token("muted"), 500: token("subtle"), 600: token("subtle"), 700: token("border-strong"),
    800: token("border"), 900: token("surface-2"), 950: token("bg"),
  },
};

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  future: { hoverOnlyWhenSupported: true },
  theme: {
    extend: {
      colors: {
        canvas: token("bg"),
        surface: token("surface"),
        surface2: token("surface-2"),
        line: token("border"),
        "line-strong": token("border-strong"),
        fg: token("ink"),
        "fg-muted": token("muted"),
        "fg-subtle": token("subtle"),
        accent: {
          DEFAULT: token("accent"),
          hover: token("accent-hover"),
          ink: token("accent-ink"),
          soft: token("accent-soft"),
          "soft-ink": token("accent-soft-ink"),
        },
        ok: { DEFAULT: token("ok"), soft: token("ok-soft") },
        warn: { DEFAULT: token("warn"), soft: token("warn-soft") },
        danger: { DEFAULT: token("danger"), soft: token("danger-soft") },
        ...legacy,
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "monospace"],
      },
      transitionTimingFunction: {
        out: "cubic-bezier(0.23, 1, 0.32, 1)",
        "in-out-strong": "cubic-bezier(0.77, 0, 0.175, 1)",
      },
    },
  },
  plugins: [],
};

export default config;

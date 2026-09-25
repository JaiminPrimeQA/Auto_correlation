# Calm Light Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the frontend a collection-only journey with an accurate privacy promise, the Calm Light visual language (plus a matching dark theme), and purposeful motion.

**Architecture:** Colours become RGB-channel CSS variables (`:root` = light, `[data-theme="dark"]` = dark) consumed through Tailwind colour names. Legacy Tailwind names (`ink`, `panel`, `edge`, `brand`, `slate-*`) are temporarily aliased to the new tokens so every screen changes theme at once, then renamed component by component and finally removed. Motion uses the `motion` library only where CSS cannot (step exit/enter, sliding tab indicator); everything else is CSS transitions driven by shared tokens.

**Tech Stack:** Next.js 15 (App Router), React 19, Tailwind CSS 3.4, TypeScript 5.7, Vitest 3 + Testing Library, Playwright; new: `motion` 13, `geist` 1.7, `@phosphor-icons/react` 2.1.

**Spec:** `docs/superpowers/specs/2026-09-25-calm-light-redesign-design.md`

## Global Constraints

- All work is in `frontend/`. No backend or API change. Run commands from `frontend/` unless stated.
- Commit messages carry **no** `Co-Authored-By` or any Claude/Anthropic attribution (repo owner's rule).
- Visible copy uses **no em-dash (`—`) or en-dash (`–`)**; use a hyphen, comma, colon or two sentences. Keep existing results-tab labels exactly: `Run health`, `Explorer`, `Candidates (n)`, `Classification (n)`, `Dependency graph`, `Add rule`, `Generate (n)`.
- One accent colour. Green/amber/red only for real status. Every text/background token pair ≥ 4.5:1 (enforced by `tests/tokens.test.ts`).
- Radius: controls `rounded-[10px]`, cards `rounded-[14px]`, badges/steps/toggle `rounded-full`.
- Fonts: Geist for text, Geist Mono only for real values (variables, paths, hosts, JSON).
- Motion values (verbatim from spec): ease-out `cubic-bezier(0.23, 1, 0.32, 1)`; ease-in-out `cubic-bezier(0.77, 0, 0.175, 1)`; press 140ms `scale(0.97)`; quick 150ms; enter 300ms; stagger 50ms (max 6 items); spring `{ type: "spring", bounce: 0, duration: 0.4 }`. Animate only `transform`, `opacity`, `filter` (blur ≤ 2px). Hover effects only under `(hover: hover) and (pointer: fine)`. Under `prefers-reduced-motion: reduce` all movement becomes a 180ms opacity fade.
- Privacy facts (verbatim):
  1. "Files and typed values are held in memory only. There is no database."
  2. "Each run's temporary workspace is deleted as soon as the run ends."
  3. "Results expire after 30 minutes. \"New analysis\" deletes them immediately."
  4. "Secrets are hidden as you type, never logged, and never written into the JMX."
  Promise line: "Your files stay private: processed in memory, never stored, and deleted after 30 minutes."
- Verification commands: `npm test`, `npm run typecheck`, `npm run build`, `npm run e2e` (needs the running stack, see Task 15).

## File Structure

| File | Responsibility |
|---|---|
| `app/globals.css` | tokens (light/dark), base element styles, component classes (`.btn*`, `.card`, `.badge*`, `.input`, `.mono`), motion CSS variables, reduced-motion block |
| `tailwind.config.ts` | maps colour names to tokens; temporary legacy aliases (removed in Task 14) |
| `lib/theme.ts` | theme preference read/write/apply + no-flash inline script string |
| `lib/motion.ts` | motion tokens for JS (`EASE_OUT`, `SPRING`, `stepVariants`) |
| `lib/nextStep.ts` | pure "what should the tester do next" logic for the results page |
| `lib/session.ts` | `discardSession()`: delete analysis + job server-side, never throws |
| `components/ThemeToggle.tsx` | Light / Dark / System segmented control |
| `components/AppHeader.tsx` | header (brand, theme toggle, New analysis slot) |
| `components/PrivacyNote.tsx` | promise line + "How we handle your data" disclosure |
| `components/ui/Stepper.tsx` | wizard stepper with filling connectors |
| `components/ui/Tabs.tsx` | tab bar with sliding underline |
| `components/ui/Toast.tsx` | success toast (`useToast` + `<ToastViewport>`) |
| `components/results/ResultsHeader.tsx` | title, readiness pill, six summary tiles |
| `components/results/NextStepCard.tsx` | guided Auto-correlate → Generate → Validate card |
| `components/collection/*` | restyled wizard steps; wizard owns step transitions |
| `app/layout.tsx`, `app/page.tsx` | fonts, header, footer; start on wizard; results page |
| deleted: `components/ModeChooser.tsx`, `components/Uploader.tsx`, `components/SummaryCounts.tsx`, `tests/ModeChooser.test.tsx` | |

---

## Phase 1: Foundations

### Task 1: Tokens, fonts and dependencies

**Files:**
- Modify: `frontend/package.json` (via npm), `frontend/app/globals.css`, `frontend/tailwind.config.ts`, `frontend/app/layout.tsx:1-10`
- Test: `frontend/tests/tokens.test.ts`

**Interfaces:**
- Produces: CSS variables `--bg --surface --surface-2 --border --border-strong --ink --muted --subtle --accent --accent-hover --accent-ink --accent-soft --accent-soft-ink --ok --ok-soft --warn --warn-soft --danger --danger-soft` (RGB channels, e.g. `246 247 249`); motion variables `--ease-out --ease-in-out --dur-press --dur-quick --dur-enter`; Tailwind colours `canvas surface surface2 line line-strong fg fg-muted fg-subtle accent(.hover .ink .soft .soft-ink) ok(.soft) warn(.soft) danger(.soft)`; font variables `--font-geist-sans --font-geist-mono`; CSS classes `.btn .btn-secondary .btn-ghost .card .badge .badge-high .badge-medium .badge-low .badge-rejected .badge-accent .input .mono .focus-ring`.

- [ ] **Step 1: Install dependencies**

Run: `npm install motion@^13.4.4 geist@^1.7.2 @phosphor-icons/react@^2.1.10`
Expected: `package.json` dependencies gain the three packages; `npm ls motion geist @phosphor-icons/react` lists them.

- [ ] **Step 2: Write the failing contrast test**

Create `frontend/tests/tokens.test.ts`:

```ts
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "..", "app", "globals.css"), "utf8");

function block(selector: string): Record<string, number[]> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`missing ${selector} block`);
  const body = css.slice(start, css.indexOf("}", start));
  const vars: Record<string, number[]> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g)) {
    vars[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return vars;
}

function luminance([r, g, b]: number[]): number {
  const c = [r, g, b].map((v) => v / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

function ratio(a: number[], b: number[]): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const PAIRS: [string, string][] = [
  ["ink", "bg"], ["ink", "surface"], ["muted", "bg"], ["muted", "surface"],
  ["subtle", "bg"], ["subtle", "surface"], ["subtle", "surface-2"],
  ["accent-ink", "accent"], ["accent-soft-ink", "accent-soft"], ["accent", "surface"],
  ["ok", "ok-soft"], ["warn", "warn-soft"], ["danger", "danger-soft"], ["danger", "surface"],
];

describe.each([[":root"], ['[data-theme="dark"]']])("tokens in %s", (selector) => {
  const vars = block(selector);
  it.each(PAIRS)("%s on %s meets WCAG AA", (fg, bg) => {
    expect(vars[fg], `--${fg}`).toBeDefined();
    expect(vars[bg], `--${bg}`).toBeDefined();
    expect(ratio(vars[fg], vars[bg])).toBeGreaterThanOrEqual(4.5);
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npx vitest run tests/tokens.test.ts`
Expected: FAIL with `missing :root block` (or undefined tokens).

- [ ] **Step 4: Replace `app/globals.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* Calm Light tokens: RGB channels so Tailwind can apply opacity (bg-accent/20). */
:root {
  color-scheme: light;
  --bg: 246 247 249;
  --surface: 255 255 255;
  --surface-2: 250 251 252;
  --border: 230 232 236;
  --border-strong: 211 216 223;
  --ink: 21 24 29;
  --muted: 85 93 105;
  --subtle: 100 108 121;
  --accent: 47 91 234;
  --accent-hover: 38 80 216;
  --accent-ink: 255 255 255;
  --accent-soft: 234 240 254;
  --accent-soft-ink: 36 71 196;
  --ok: 4 120 87;
  --ok-soft: 236 253 245;
  --warn: 180 83 9;
  --warn-soft: 255 247 230;
  --danger: 201 48 44;
  --danger-soft: 253 240 239;
  --shadow-card: 0 1px 2px rgb(20 30 50 / 0.04), 0 8px 24px rgb(20 30 50 / 0.06);
  --ring: 0 0 0 3px rgb(47 91 234 / 0.25);

  --ease-out: cubic-bezier(0.23, 1, 0.32, 1);
  --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);
  --dur-press: 140ms;
  --dur-quick: 150ms;
  --dur-enter: 300ms;
}

/* Calm Dark: soft colours are pre-blended over --surface (no alpha in tokens). */
[data-theme="dark"] {
  color-scheme: dark;
  --bg: 15 17 21;
  --surface: 22 25 31;
  --surface-2: 27 31 38;
  --border: 38 43 51;
  --border-strong: 52 58 68;
  --ink: 236 238 242;
  --muted: 164 171 182;
  --subtle: 138 146 158;
  --accent: 123 155 255;
  --accent-hover: 147 173 255;
  --accent-ink: 12 21 48;
  --accent-soft: 36 43 62;
  --accent-soft-ink: 179 198 255;
  --ok: 52 211 153;
  --ok-soft: 26 47 46;
  --warn: 251 191 36;
  --warn-soft: 49 45 32;
  --danger: 248 113 113;
  --danger-soft: 49 36 41;
  --shadow-card: 0 1px 2px rgb(0 0 0 / 0.3), 0 10px 30px rgb(0 0 0 / 0.35);
  --ring: 0 0 0 3px rgb(123 155 255 / 0.35);
}

html,
body {
  background: rgb(var(--bg));
  color: rgb(var(--ink));
  font-family: var(--font-geist-sans), system-ui, sans-serif;
}

body {
  transition: background-color var(--dur-enter) ease, color var(--dur-enter) ease;
}

@layer components {
  .focus-ring:focus-visible,
  .btn:focus-visible,
  .btn-secondary:focus-visible,
  .btn-ghost:focus-visible,
  .input:focus-visible {
    outline: none;
    box-shadow: var(--ring);
  }

  .btn,
  .btn-secondary,
  .btn-ghost {
    @apply inline-flex items-center justify-center gap-2 rounded-[10px] px-4 py-2.5 text-sm font-semibold
           disabled:cursor-not-allowed disabled:opacity-45;
    transition: transform var(--dur-press) var(--ease-out), background-color var(--dur-quick) ease,
      border-color var(--dur-quick) ease, color var(--dur-quick) ease, box-shadow var(--dur-quick) ease;
  }
  .btn:active:not(:disabled),
  .btn-secondary:active:not(:disabled),
  .btn-ghost:active:not(:disabled) {
    transform: scale(0.97);
  }
  .btn { @apply bg-accent text-accent-ink; }
  .btn-secondary { @apply border border-line-strong bg-surface text-fg; }
  .btn-ghost { @apply px-3 py-2 font-medium text-fg-muted; }

  @media (hover: hover) and (pointer: fine) {
    .btn:hover:not(:disabled) { @apply bg-accent-hover; }
    .btn-secondary:hover:not(:disabled) { @apply bg-surface2; }
    .btn-ghost:hover:not(:disabled) { @apply bg-surface2 text-fg; }
  }

  .card {
    @apply rounded-[14px] border border-line bg-surface;
    box-shadow: var(--shadow-card);
  }

  .input {
    @apply w-full rounded-[10px] border border-line-strong bg-surface px-3 py-2.5 text-sm text-fg
           placeholder:text-fg-subtle;
    transition: border-color var(--dur-quick) ease, box-shadow var(--dur-quick) ease;
  }
  .input:focus { @apply border-accent; }

  .badge { @apply inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium; }
  .badge-high { @apply bg-ok-soft text-ok; }
  .badge-medium { @apply bg-warn-soft text-warn; }
  .badge-low { @apply border border-line bg-surface2 text-fg-muted; }
  .badge-rejected { @apply bg-danger-soft text-danger; }
  .badge-accent { @apply bg-accent-soft text-accent-soft-ink; }

  .mono { font-family: var(--font-geist-mono), ui-monospace, SFMono-Regular, Menlo, monospace; }
}

@keyframes b11-rise {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}
@keyframes b11-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes b11-breathe {
  50% { transform: scale(0.6); opacity: 0.6; }
}

/* Content that rises in on a new screen: set --i on each child (0..5). */
.rise > * {
  animation: b11-rise 420ms var(--ease-out) both;
  animation-delay: calc(min(var(--i, 0), 5) * 50ms);
}

@media (prefers-reduced-motion: reduce) {
  .rise > * { animation: b11-fade 180ms ease both !important; }
  .breathe { animation: none !important; }
  *, *::before, *::after { transition-duration: 180ms !important; }
}
```

- [ ] **Step 5: Replace `tailwind.config.ts`**

```ts
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
```

- [ ] **Step 6: Load Geist in `app/layout.tsx`**

Add the imports and put both font variables on `<html>` (the rest of the file is rewritten in Task 2):

```tsx
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
// ...
<html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
```

- [ ] **Step 7: Run tests, typecheck and build**

Run: `npx vitest run tests/tokens.test.ts && npm test && npm run typecheck && npm run build`
Expected: tokens test 28 passed; all existing suites pass; typecheck clean; build succeeds (the app is now light-themed via aliases).

- [ ] **Step 8: Commit**

```bash
git add package.json package-lock.json app/globals.css tailwind.config.ts app/layout.tsx tests/tokens.test.ts
git commit -m "feat(ui): Calm Light and Calm Dark design tokens, Geist fonts, motion deps"
```

### Task 2: Theme preference, toggle and app header

**Files:**
- Create: `frontend/lib/theme.ts`, `frontend/components/ThemeToggle.tsx`, `frontend/components/AppHeader.tsx`
- Modify: `frontend/app/layout.tsx`
- Test: `frontend/tests/ThemeToggle.test.tsx`

**Interfaces:**
- Consumes: `[data-theme="dark"]` tokens (Task 1).
- Produces: `type ThemePreference = "light" | "dark" | "system"`; `readPreference(): ThemePreference`; `writePreference(p: ThemePreference): void`; `resolveTheme(p: ThemePreference, prefersDark: boolean): "light" | "dark"`; `applyTheme(p: ThemePreference): void`; `NO_FLASH_SCRIPT: string`; `<ThemeToggle />`; `<AppHeader right?: React.ReactNode />` (the `right` slot is filled by `page.tsx` in Task 9 via a portal-free prop; in this task the header renders the brand and toggle only).

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/ThemeToggle.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeToggle } from "@/components/ThemeToggle";
import { readPreference, resolveTheme } from "@/lib/theme";

function mockSystemDark(dark: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: q.includes("dark") ? dark : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  mockSystemDark(false);
});

describe("theme", () => {
  it("resolves system preference", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("defaults to system and survives unavailable storage", () => {
    expect(readPreference()).toBe("system");
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(readPreference()).toBe("system");
    spy.mockRestore();
  });

  it("switches the document theme and remembers the choice", () => {
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("b11-theme")).toBe("dark");
    expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();

    fireEvent.click(screen.getByRole("radio", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("System follows the OS setting", () => {
    mockSystemDark(true);
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("radio", { name: "System" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/ThemeToggle.test.tsx`
Expected: FAIL, `Cannot find module '@/components/ThemeToggle'`.

- [ ] **Step 3: Implement `lib/theme.ts`**

```ts
export type ThemePreference = "light" | "dark" | "system";

const KEY = "b11-theme";

export function readPreference(): ThemePreference {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function writePreference(p: ThemePreference): void {
  try {
    if (p === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, p);
  } catch {
    // storage blocked (private mode): the choice lasts for this page only
  }
}

export function resolveTheme(p: ThemePreference, prefersDark: boolean): "light" | "dark" {
  return p === "system" ? (prefersDark ? "dark" : "light") : p;
}

export function systemPrefersDark(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(p: ThemePreference): void {
  document.documentElement.dataset.theme = resolveTheme(p, systemPrefersDark());
}

/** Runs before first paint (inline in <head>) so there is no light flash. */
export const NO_FLASH_SCRIPT = `(function(){try{var p=localStorage.getItem("${KEY}");var d=p==="dark"||(p!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.dataset.theme=d?"dark":"light";}catch(e){}})();`;
```

- [ ] **Step 4: Implement `components/ThemeToggle.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { DesktopIcon, MoonIcon, SunIcon } from "@phosphor-icons/react";
import { applyTheme, readPreference, writePreference, type ThemePreference } from "@/lib/theme";

const OPTIONS: { value: ThemePreference; label: string; Icon: typeof SunIcon }[] = [
  { value: "light", label: "Light", Icon: SunIcon },
  { value: "dark", label: "Dark", Icon: MoonIcon },
  { value: "system", label: "System", Icon: DesktopIcon },
];

export function ThemeToggle() {
  const [pref, setPref] = useState<ThemePreference>("system");

  useEffect(() => {
    setPref(readPreference());
  }, []);

  useEffect(() => {
    if (pref !== "system" || typeof matchMedia !== "function") return;
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [pref]);

  function choose(p: ThemePreference) {
    setPref(p);
    writePreference(p);
    applyTheme(p);
  }

  return (
    <div role="radiogroup" aria-label="Theme" className="inline-flex rounded-full border border-line bg-surface2 p-[3px]">
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={pref === value}
          aria-label={label}
          title={label}
          onClick={() => choose(value)}
          className={`focus-ring inline-flex h-7 w-8 items-center justify-center rounded-full transition-colors duration-150 ${
            pref === value ? "bg-surface text-fg shadow-sm" : "text-fg-muted hover:text-fg"
          }`}
        >
          <Icon size={15} weight="regular" aria-hidden />
        </button>
      ))}
    </div>
  );
}
```

Note: `toBeChecked()` works on `role="radio"` with `aria-checked`.

- [ ] **Step 5: Implement `components/AppHeader.tsx`**

```tsx
import { ThemeToggle } from "./ThemeToggle";

export function AppHeader({ right }: { right?: React.ReactNode }) {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-surface/85 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <span aria-hidden className="h-[22px] w-[22px] rounded-[7px] bg-accent" />
          <span className="font-semibold tracking-tight">Baseline11</span>
          <span className="hidden text-sm text-fg-subtle sm:inline">Auto-Correlate</span>
        </div>
        <div className="flex-1" />
        <ThemeToggle />
        {right}
      </div>
    </header>
  );
}
```

- [ ] **Step 6: Rewrite `app/layout.tsx`**

```tsx
import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { AuthGate } from "@/components/AuthGate";
import { NO_FLASH_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: "Baseline11 Auto-Correlate",
  description: "Run a Postman collection twice and get a correlated JMeter test plan.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_SCRIPT }} />
      </head>
      <body className="min-h-[100dvh] antialiased">
        <AuthGate>{children}</AuthGate>
        <footer className="mx-auto max-w-6xl px-4 pb-8 pt-4 text-xs text-fg-subtle sm:px-6">
          <a className="hover:text-fg" href="https://jmeter.apache.org/usermanual/component_reference.html" target="_blank" rel="noreferrer">
            JMeter 5.6.3 reference
          </a>
        </footer>
      </body>
    </html>
  );
}
```

The header moves into `page.tsx` (Task 9) because it needs the "New analysis" action; until then `page.tsx` renders `<AppHeader />` at the top of its output. Add now at the very top of every `return` in `app/page.tsx`: wrap the existing JSX as `<><AppHeader /><main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">{/* existing JSX */}</main></>` and `import { AppHeader } from "@/components/AppHeader";`.

- [ ] **Step 7: Run tests**

Run: `npx vitest run tests/ThemeToggle.test.tsx && npm test && npm run typecheck`
Expected: ThemeToggle 4 passed; all suites pass; typecheck clean. Also check `tests/AuthGate.test.tsx` still passes (layout no longer wraps a header inside AuthGate).

- [ ] **Step 8: Commit**

```bash
git add lib/theme.ts components/ThemeToggle.tsx components/AppHeader.tsx app/layout.tsx app/page.tsx tests/ThemeToggle.test.tsx
git commit -m "feat(ui): light/dark/system theme toggle without first-paint flash; new header"
```

### Task 3: Motion tokens and UI primitives (Stepper, Tabs, Toast)

**Files:**
- Create: `frontend/lib/motion.ts`, `frontend/components/ui/Stepper.tsx`, `frontend/components/ui/Tabs.tsx`, `frontend/components/ui/Toast.tsx`
- Modify: `frontend/tests/setup.ts`
- Test: `frontend/tests/ui.test.tsx`

**Interfaces:**
- Produces:
  - `lib/motion.ts`: `EASE_OUT: [number, number, number, number]`, `SPRING: { type: "spring"; bounce: number; duration: number }`, `stepVariants` (Motion variants keyed `enter`/`center`/`exit`, custom = direction `1 | -1`).
  - `<Stepper steps={string[]} current={number} />`: renders `<ol aria-label="Progress">`, each `<li aria-current="step">` for current, `data-state="done" | "current" | "todo"`.
  - `<Tabs tabs={{ id: T; label: string }[]} active={T} onChange={(id: T) => void} />` generic `T extends string`; renders `role="tablist"` with `role="tab"` buttons (`aria-selected`).
  - `useToast(): { show(message: string): void }` and `<ToastProvider>{children}</ToastProvider>`; toast renders `role="status"`, auto-hides after 2400ms.

- [ ] **Step 1: Make Motion instant in tests**

Append to `frontend/tests/setup.ts`:

```ts
import { MotionGlobalConfig } from "motion/react";

// Tests assert on DOM state, not on frames: finish every Motion animation at once.
MotionGlobalConfig.skipAnimations = true;
```

- [ ] **Step 2: Write the failing tests**

Create `frontend/tests/ui.test.tsx`:

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Stepper } from "@/components/ui/Stepper";
import { Tabs } from "@/components/ui/Tabs";
import { ToastProvider, useToast } from "@/components/ui/Toast";

describe("Stepper", () => {
  it("marks done, current and upcoming steps", () => {
    render(<Stepper steps={["Files", "Variables", "Review", "Run"]} current={2} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((i) => i.dataset.state)).toEqual(["done", "done", "current", "todo"]);
    expect(items[2]).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("list", { name: "Progress" })).toBeInTheDocument();
  });
});

describe("Tabs", () => {
  it("selects a tab and reports changes", () => {
    const onChange = vi.fn();
    render(<Tabs tabs={[{ id: "a", label: "Run health" }, { id: "b", label: "Explorer" }]} active="a" onChange={onChange} />);
    expect(screen.getByRole("tab", { name: "Run health" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "Explorer" }));
    expect(onChange).toHaveBeenCalledWith("b");
  });
});

describe("Toast", () => {
  function Trigger() {
    const toast = useToast();
    return <button onClick={() => toast.show("2 correlations created")}>go</button>;
  }

  it("shows a status message and hides it again", () => {
    vi.useFakeTimers();
    render(<ToastProvider><Trigger /></ToastProvider>);
    fireEvent.click(screen.getByText("go"));
    expect(screen.getByRole("status")).toHaveTextContent("2 correlations created");
    act(() => { vi.advanceTimersByTime(2500); });
    expect(screen.getByRole("status")).toHaveTextContent("");
    vi.useRealTimers();
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `npx vitest run tests/ui.test.tsx`
Expected: FAIL, modules not found.

- [ ] **Step 4: Implement `lib/motion.ts`**

```ts
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
```

- [ ] **Step 5: Implement `components/ui/Stepper.tsx`**

```tsx
import { CheckIcon } from "@phosphor-icons/react";

export function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol aria-label="Progress" className="mb-8 flex max-w-xl items-center">
      {steps.map((label, i) => {
        const state = i < current ? "done" : i === current ? "current" : "todo";
        return (
          <li key={label} data-state={state} aria-current={state === "current" ? "step" : undefined} className="flex flex-1 items-center last:flex-none">
            <span className={`flex items-center gap-2 whitespace-nowrap text-[13px] ${state === "current" ? "font-medium text-fg" : "text-fg-subtle"}`}>
              <span
                className={`grid h-6 w-6 place-items-center rounded-full border text-xs font-semibold transition-colors duration-200 ${
                  state === "current"
                    ? "border-accent bg-accent text-accent-ink"
                    : state === "done"
                      ? "border-transparent bg-accent-soft text-accent-soft-ink"
                      : "border-line-strong bg-surface"
                }`}
              >
                {state === "done" ? <CheckIcon size={12} weight="bold" aria-hidden /> : i + 1}
              </span>
              {label}
            </span>
            {i < steps.length - 1 && (
              <span aria-hidden className="relative mx-3 h-0.5 min-w-6 flex-1 overflow-hidden rounded bg-line">
                <span
                  className="absolute inset-0 origin-left bg-accent"
                  style={{
                    transform: `scaleX(${i < current ? 1 : 0})`,
                    transition: "transform 360ms var(--ease-in-out)",
                  }}
                />
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
```

- [ ] **Step 6: Implement `components/ui/Tabs.tsx`**

```tsx
"use client";

import { useLayoutEffect, useRef, useState } from "react";

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: T; label: string }[];
  active: T;
  onChange: (id: T) => void;
}) {
  const refs = useRef(new Map<T, HTMLButtonElement>());
  const [bar, setBar] = useState({ left: 0, width: 0 });

  useLayoutEffect(() => {
    const el = refs.current.get(active);
    if (el) setBar({ left: el.offsetLeft, width: el.offsetWidth });
  }, [active, tabs]);

  return (
    <div role="tablist" className="relative flex gap-0.5 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id}
          ref={(el) => { if (el) refs.current.set(t.id, el); }}
          role="tab"
          type="button"
          aria-selected={t.id === active}
          onClick={() => onChange(t.id)}
          className={`focus-ring whitespace-nowrap px-3 py-2.5 text-[13.5px] font-medium transition-colors duration-150 ${
            t.id === active ? "text-fg" : "text-fg-muted hover:text-fg"
          }`}
        >
          {t.label}
        </button>
      ))}
      <span
        aria-hidden
        className="absolute -bottom-px left-0 h-0.5 rounded bg-accent"
        style={{
          width: bar.width,
          transform: `translateX(${bar.left}px)`,
          transition: "transform 280ms var(--ease-out), width 280ms var(--ease-out)",
        }}
      />
    </div>
  );
}
```

- [ ] **Step 7: Implement `components/ui/Toast.tsx`**

```tsx
"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import { CheckCircleIcon } from "@phosphor-icons/react";

const ToastContext = createContext<{ show: (message: string) => void }>({ show: () => {} });

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [message, setMessage] = useState("");
  const [visible, setVisible] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const show = useCallback((m: string) => {
    setMessage(m);
    setVisible(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setVisible(false);
      setMessage("");
    }, 2400);
  }, []);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-6 right-6 z-30 flex items-center gap-2 rounded-xl bg-fg px-4 py-3 text-sm text-canvas shadow-lg"
        style={{
          opacity: visible ? 1 : 0,
          transform: visible ? "none" : "translateY(12px)",
          transition: "opacity 300ms var(--ease-out), transform 300ms var(--ease-out)",
        }}
      >
        {visible && <CheckCircleIcon size={16} weight="fill" aria-hidden />}
        {message}
      </div>
    </ToastContext.Provider>
  );
}
```

- [ ] **Step 8: Run tests**

Run: `npx vitest run tests/ui.test.tsx && npm test && npm run typecheck`
Expected: ui tests 3 passed; all suites pass; typecheck clean.

- [ ] **Step 9: Commit**

```bash
git add lib/motion.ts components/ui tests/ui.test.tsx tests/setup.ts
git commit -m "feat(ui): motion tokens plus Stepper, Tabs and Toast primitives"
```

---

## Phase 2: Journey

### Task 4: Privacy note

**Files:**
- Create: `frontend/components/PrivacyNote.tsx`
- Test: `frontend/tests/PrivacyNote.test.tsx`

**Interfaces:**
- Produces: `<PrivacyNote mode?: "local" | "aws" />`; default reads `process.env.NEXT_PUBLIC_PRIVACY_MODE` (`"aws"` → aws copy, anything else → local).

- [ ] **Step 1: Write the failing test**

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PrivacyNote } from "@/components/PrivacyNote";

describe("PrivacyNote", () => {
  it("states the promise and reveals the four facts on demand", () => {
    render(<PrivacyNote mode="local" />);
    expect(screen.getByText(/processed in memory, never stored, and deleted after 30 minutes/i)).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: /how we handle your data/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    for (const fact of [
      /held in memory only\. there is no database/i,
      /temporary workspace is deleted as soon as the run ends/i,
      /results expire after 30 minutes\. "new analysis" deletes them immediately/i,
      /never logged, and never written into the jmx/i,
    ]) {
      expect(screen.getByText(fact)).toBeVisible();
    }
  });

  it("never claims 'no database' for the AWS deployment", () => {
    render(<PrivacyNote mode="aws" />);
    fireEvent.click(screen.getByRole("button", { name: /how we handle your data/i }));
    expect(document.body.textContent).not.toMatch(/no database/i);
    expect(screen.getByText(/stored encrypted and deleted after 30 minutes/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run tests/PrivacyNote.test.tsx` → FAIL, module not found.

- [ ] **Step 3: Implement `components/PrivacyNote.tsx`**

```tsx
"use client";

import { useId, useState } from "react";
import { CaretRightIcon, CheckIcon, LockSimpleIcon } from "@phosphor-icons/react";

const COPY = {
  local: {
    promise: "Your files stay private: processed in memory, never stored, and deleted after 30 minutes.",
    facts: [
      "Files and typed values are held in memory only. There is no database.",
      "Each run's temporary workspace is deleted as soon as the run ends.",
      'Results expire after 30 minutes. "New analysis" deletes them immediately.',
      "Secrets are hidden as you type, never logged, and never written into the JMX.",
    ],
  },
  aws: {
    promise: "Your files stay private: stored encrypted and deleted after 30 minutes.",
    facts: [
      "Files and results are stored encrypted and only your account can read them.",
      "Each run happens in a fresh, isolated task that is discarded when it ends.",
      'Results expire after 30 minutes. "New analysis" deletes them immediately.',
      "Secrets are hidden as you type, never logged, and never written into the JMX.",
    ],
  },
} as const;

export function PrivacyNote({ mode }: { mode?: "local" | "aws" }) {
  const resolved = mode ?? (process.env.NEXT_PUBLIC_PRIVACY_MODE === "aws" ? "aws" : "local");
  const copy = COPY[resolved];
  const [open, setOpen] = useState(false);
  const panelId = useId();

  return (
    <div className="border-t border-line pt-4">
      <div className="flex items-start gap-2.5 text-[13.5px] leading-snug text-fg">
        <span className="grid h-[22px] w-[22px] flex-none place-items-center rounded-md bg-ok-soft text-ok">
          <LockSimpleIcon size={13} weight="bold" aria-hidden />
        </span>
        <span>{copy.promise}</span>
      </div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="focus-ring ml-8 mt-2 inline-flex items-center gap-1 rounded text-[13px] font-medium text-accent-soft-ink"
      >
        <CaretRightIcon
          size={12}
          weight="bold"
          aria-hidden
          style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 220ms var(--ease-out)" }}
        />
        How we handle your data
      </button>
      <div
        id={panelId}
        inert={!open}
        aria-hidden={!open}
        className="ml-8 grid"
        style={{ gridTemplateRows: open ? "1fr" : "0fr", transition: "grid-template-rows 260ms var(--ease-out)" }}
      >
        <ul className="overflow-hidden text-[13px] leading-snug text-fg-muted" style={{ visibility: open ? "visible" : "hidden" }}>
          {copy.facts.map((f) => (
            <li key={f} className="flex gap-2 pt-1.5">
              <CheckIcon size={13} weight="bold" className="mt-0.5 flex-none text-ok" aria-hidden />
              <span>{f}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
```

(Height opens via the `grid-template-rows` 0fr to 1fr transition from the spec. `inert` + `aria-hidden` keep the closed panel out of focus order and the accessibility tree; `visibility` makes `toBeVisible()` meaningful in tests and flips instantly while the height animates.)

- [ ] **Step 4: Run tests** → `npx vitest run tests/PrivacyNote.test.tsx` PASS (2).

- [ ] **Step 5: Commit**

```bash
git add components/PrivacyNote.tsx tests/PrivacyNote.test.tsx
git commit -m "feat(ui): privacy promise with an accurate 'How we handle your data' disclosure"
```

### Task 5: Files step redesign

**Files:**
- Modify: `frontend/components/collection/FilesStep.tsx`
- Test: `frontend/tests/FilesStep.test.tsx` (existing; must stay green, add one assertion)

**Interfaces:**
- Consumes: `<PrivacyNote />` (Task 4).
- Produces: unchanged props `FilesStep({ initial?, onInspected })`; file inputs keep the accessible names "Collection file" and "Environment file".

- [ ] **Step 1: Add the failing assertion** to the first test in `tests/FilesStep.test.tsx` (right after render):

```tsx
expect(screen.getByRole("button", { name: /how we handle your data/i })).toBeInTheDocument();
expect(screen.getByRole("heading", { name: /turn a postman collection into a correlated jmeter plan/i })).toBeInTheDocument();
```

- [ ] **Step 2: Run** `npx vitest run tests/FilesStep.test.tsx` → FAIL (no such button/heading).

- [ ] **Step 3: Rewrite `FilesStep.tsx`** (keep the `inspect()` logic byte-for-byte; replace the JSX and `FilePicker`):

```tsx
"use client";

import { useState } from "react";
import { FileArrowUpIcon } from "@phosphor-icons/react";
import { api, type CollectionInspection } from "@/lib/api";
import { PrivacyNote } from "@/components/PrivacyNote";

export interface CollectionFiles {
  collection: File;
  environment: File | null;
}

function DropZone({
  id, label, optional, emptyTitle, emptyHint, file, onPick,
}: {
  id: string; label: string; optional?: boolean; emptyTitle: string; emptyHint: string;
  file: File | null; onPick: (file: File | null) => void;
}) {
  return (
    <div>
      <span className="mb-2 block text-[13px] font-medium">
        {label} {optional && <span className="font-normal text-fg-subtle">(optional)</span>}
      </span>
      <label
        htmlFor={id}
        className={`focus-within:ring-accent/30 flex cursor-pointer items-center gap-3.5 rounded-xl border-[1.5px] p-4 transition-colors duration-150 focus-within:ring-[3px] ${
          file ? "border-solid border-accent/40 bg-accent-soft/40" : "border-dashed border-line-strong bg-surface2 hover:border-accent"
        }`}
      >
        <span
          className={`grid h-10 w-10 flex-none place-items-center rounded-[10px] transition-transform duration-200 ${
            file ? "scale-[1.04] bg-accent text-accent-ink" : "bg-accent-soft text-accent-soft-ink"
          }`}
        >
          <FileArrowUpIcon size={20} aria-hidden />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{file ? file.name : emptyTitle}</span>
          <span className="block text-[13px] text-fg-subtle">
            {file ? `${(file.size / 1024).toFixed(1)} KiB` : emptyHint}
          </span>
        </span>
        <input
          id={id}
          aria-label={label}
          type="file"
          accept="application/json,.json"
          className="sr-only"
          onChange={(e) => onPick(e.target.files?.[0] ?? null)}
        />
      </label>
    </div>
  );
}

const HOW = [
  ["Upload and check", "We read the collection and list any values we still need."],
  ["Run twice, safely", "Two fresh Newman runs in locked-down containers."],
  ["Correlate and export", "Review what changed, then download a JMeter 5.6.3 plan."],
] as const;

/** Step 1: choose files and inspect them - nothing is executed here. */
export function FilesStep({
  initial,
  onInspected,
}: {
  initial?: CollectionFiles | null;
  onInspected: (files: CollectionFiles, inspection: CollectionInspection) => void;
}) {
  const [collection, setCollection] = useState<File | null>(initial?.collection ?? null);
  const [environment, setEnvironment] = useState<File | null>(initial?.environment ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function inspect() {
    if (!collection || busy) return;
    setBusy(true);
    setError(null);
    try {
      const inspection = await api.inspectCollection(collection, environment);
      onInspected({ collection, environment }, inspection);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid items-start gap-10 lg:grid-cols-[1fr_1.05fr]">
      <div className="rise">
        <h1 style={{ "--i": 0 } as React.CSSProperties} className="mb-3 max-w-[16ch] text-[32px] font-semibold leading-[1.12] tracking-[-0.03em]">
          Turn a Postman collection into a correlated JMeter plan
        </h1>
        <p style={{ "--i": 1 } as React.CSSProperties} className="mb-6 max-w-[44ch] text-[15px] leading-relaxed text-fg-muted">
          Upload your collection. We run it twice in an isolated sandbox, find the values that change between runs,
          and wire them into a ready-to-run test plan.
        </p>
        <ol style={{ "--i": 2 } as React.CSSProperties} className="space-y-3.5">
          {HOW.map(([title, text], i) => (
            <li key={title} className="flex gap-3 text-sm leading-normal text-fg-muted">
              <span className="grid h-7 w-7 flex-none place-items-center rounded-lg bg-accent-soft text-xs font-semibold text-accent-soft-ink">{i + 1}</span>
              <span><b className="block font-medium text-fg">{title}</b>{text}</span>
            </li>
          ))}
        </ol>
      </div>

      <div className="card rise space-y-3 p-5">
        <div style={{ "--i": 1 } as React.CSSProperties}>
          <DropZone id="collection-file" label="Collection file" emptyTitle="Choose or drop a collection"
            emptyHint="Postman v2.0 or v2.1 JSON, up to 10 MiB" file={collection} onPick={setCollection} />
        </div>
        <div style={{ "--i": 2 } as React.CSSProperties}>
          <DropZone id="environment-file" label="Environment file" optional emptyTitle="Choose or drop an environment"
            emptyHint="Values such as baseUrl and password, up to 2 MiB" file={environment} onPick={setEnvironment} />
        </div>
        <div style={{ "--i": 3 } as React.CSSProperties}>
          <PrivacyNote />
        </div>
        {error && (
          <p role="alert" className="rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>
        )}
        <div style={{ "--i": 4 } as React.CSSProperties} className="flex items-center justify-between gap-3 pt-2">
          <span className="text-[12.5px] text-fg-subtle">Nothing runs until you confirm on step 3.</span>
          <button className="btn" disabled={!collection || busy} onClick={inspect}>
            {busy ? "Inspecting..." : "Inspect collection"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

Drag-and-drop works because the `<input type="file">` sits inside the `<label>`: browsers deliver a dropped file to the input. The `sr-only` input keeps keyboard focus; `focus-within` shows the ring on the zone.

- [ ] **Step 4: Run** `npx vitest run tests/FilesStep.test.tsx tests/CollectionWizard.test.tsx` → PASS.

- [ ] **Step 5: Commit**

```bash
git add components/collection/FilesStep.tsx tests/FilesStep.test.tsx
git commit -m "feat(ui): split Files step with drop zones, how-it-works and the privacy note"
```

### Task 6: Variables and Review steps

**Files:**
- Modify: `frontend/components/collection/VariablesStep.tsx`, `frontend/components/collection/ReviewStep.tsx`
- Test: `frontend/tests/VariablesStep.test.tsx` (unchanged, must pass), `frontend/tests/ReviewStep.test.tsx`, `frontend/tests/CollectionWizard.test.tsx` (scope interaction)

**Interfaces:**
- Produces: unchanged props. Scope becomes a radio group: `role="radiogroup"` named "Scope", options are `<input type="radio" name="scope">` with labels `Whole collection` and each folder `path`.

- [ ] **Step 1: Update the scope test** in `tests/ReviewStep.test.tsx`, replacing the two `fireEvent.change(screen.getByLabelText(/scope/i) ...)` lines:

```tsx
fireEvent.click(screen.getByRole("radio", { name: /^auth/i }));
expect(props.onFolderChange).toHaveBeenCalledWith("auth");
fireEvent.click(screen.getByRole("radio", { name: /whole collection/i }));
expect(props.onFolderChange).toHaveBeenLastCalledWith(null);
```

and in `tests/CollectionWizard.test.tsx` replace
`fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: "auth" } });` with
`fireEvent.click(screen.getByRole("radio", { name: /^auth/i }));`.

- [ ] **Step 2: Run** `npx vitest run tests/ReviewStep.test.tsx` → FAIL (no radio).

- [ ] **Step 3: ReviewStep: replace the `<select>` block** with:

```tsx
<fieldset>
  <legend className="mb-2 text-[13px] font-medium">Scope</legend>
  <div role="radiogroup" aria-label="Scope" className="grid gap-2">
    {[{ id: null as string | null, label: "Whole collection", count: inspection.request_count_estimate },
      ...inspection.folders.map((f) => ({ id: f.id as string | null, label: f.path, count: f.request_count }))].map((o) => {
      const on = (folderId ?? null) === o.id;
      return (
        <label
          key={o.id ?? "__all"}
          className={`flex cursor-pointer items-center gap-3 rounded-xl border px-3.5 py-3 text-sm transition-colors duration-150 ${
            on ? "border-accent bg-accent-soft/40" : "border-line hover:border-line-strong"
          }`}
        >
          <input
            type="radio"
            name="scope"
            className="accent-[rgb(var(--accent))]"
            checked={on}
            onChange={() => onFolderChange(o.id)}
          />
          <span>{o.label}</span>
          <span className="ml-auto text-[12.5px] text-fg-subtle">{o.count} requests</span>
        </label>
      );
    })}
  </div>
</fieldset>
```

Then restyle the rest of ReviewStep with these exact class swaps (no logic change):

| Old | New |
|---|---|
| `card space-y-5 p-6` (root) | `mx-auto max-w-3xl space-y-5` and wrap the content below the heading in `<div className="card space-y-5 p-5">` |
| `text-lg font-semibold` (h2) | `text-lg font-semibold tracking-tight` |
| `text-slate-400` / `text-slate-500` | `text-fg-muted` / `text-fg-subtle` |
| `border-edge/60` | `border-line` |
| `text-xs uppercase tracking-wide text-slate-500` (dt) | `text-[13px] text-fg-subtle` |
| `rounded bg-ink/60 px-1.5 text-xs` | `rounded-md border border-line bg-surface2 px-1.5 text-xs` |
| `rounded border border-danger/40 bg-danger/10 p-3` | `rounded-xl bg-danger-soft p-3` |
| `rounded border border-warn/40 bg-warn/10 p-3` | `rounded-xl bg-warn-soft p-3` |
| `rounded bg-danger/15 px-3 py-2 text-sm text-danger` | `rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger` |
| Back button `btn-ghost` | `btn-secondary` |
| `"Starting…"` | `"Starting..."` |
| em-dashes in copy ("Destination problems — the job…", `" — {u.location}"`) | "Destination problems: the job will fail validation", `" ({u.location})"` |
| Limits row `·` separators | commas: `{RUN_LIMITS.runs} runs, {RUN_LIMITS.minutesPerRun} min per run, {RUN_LIMITS.maxReportMiB} MiB report per run, public HTTPS only, redirects not followed` |

- [ ] **Step 4: VariablesStep** class swaps (no logic change):

| Old | New |
|---|---|
| root `card space-y-5 p-6` | `mx-auto max-w-3xl space-y-5`; content under the header wrapped in `<div className="card space-y-5 p-5">` |
| heading text "Variables" | "Check the variables" |
| `text-slate-400` / `text-slate-500` | `text-fg-muted` / `text-fg-subtle` |
| `rounded bg-ok/10 px-3 py-2 text-sm text-ok` | `rounded-[10px] bg-ok-soft px-3 py-2 text-sm text-ok` |
| `badge badge-medium` text "secret · hidden" | `badge badge-medium` text "secret" |
| input classes `mono mt-1 w-full rounded-md border border-edge bg-ink/60 px-3 py-2 text-sm focus:border-brand focus:outline-none` | `input mono mt-1` |
| already-resolved row `rounded bg-ink/50 px-3 py-1.5` | `rounded-[10px] border border-line bg-surface2 px-3 py-2` |
| for `v.source === "script"` rows add `<span className="badge badge-accent">set by script</span>` before the source text |
| Back `btn-ghost` | `btn-secondary` |
| `" …"` in locations | `"..."` |

Keep `SOURCE_TEXT` strings unchanged (e2e asserts "set at runtime by a script").

- [ ] **Step 5: Run** `npx vitest run tests/ReviewStep.test.tsx tests/VariablesStep.test.tsx tests/CollectionWizard.test.tsx && npm run typecheck` → PASS.

- [ ] **Step 6: Commit**

```bash
git add components/collection/VariablesStep.tsx components/collection/ReviewStep.tsx tests/ReviewStep.test.tsx tests/CollectionWizard.test.tsx
git commit -m "feat(ui): restyle Variables and Review; scope becomes a radio group"
```

### Task 7: Run timeline

**Files:**
- Modify: `frontend/components/collection/ExecutionProgress.tsx`
- Test: `frontend/tests/ExecutionProgress.test.tsx` (existing assertions must pass; add one)

**Interfaces:**
- Produces: unchanged props; each stage `<li data-status=...>` preserved; stage labels from `STAGES` unchanged.

- [ ] **Step 1: Add assertion** to "names the current stage while the job runs":

```tsx
expect(screen.getByRole("heading", { name: /running your collection/i })).toBeInTheDocument();
```

- [ ] **Step 2: Run** → FAIL (heading text is "Executing your collection").

- [ ] **Step 3: Restyle.** Replace `STATUS_ICON`/`STATUS_CLASS` and the header/`<ol>` JSX with:

```tsx
import { CheckIcon, XIcon } from "@phosphor-icons/react";

function StageDot({ status }: { status: StageStatus }) {
  if (status === "done")
    return <span className="z-[1] grid h-8 w-8 flex-none place-items-center rounded-full bg-accent text-accent-ink"><CheckIcon size={14} weight="bold" aria-hidden /></span>;
  if (status === "failed")
    return <span className="z-[1] grid h-8 w-8 flex-none place-items-center rounded-full bg-danger text-accent-ink"><XIcon size={14} weight="bold" aria-hidden /></span>;
  if (status === "current")
    return (
      <span className="z-[1] grid h-8 w-8 flex-none place-items-center rounded-full border-2 border-accent bg-surface">
        <span className="breathe h-2.5 w-2.5 rounded-full bg-accent" style={{ animation: "b11-breathe 1.1s ease-in-out infinite" }} />
      </span>
    );
  return <span className="z-[1] h-8 w-8 flex-none rounded-full border-2 border-line bg-surface" />;
}
```

Header:

```tsx
<div className="flex flex-wrap items-center justify-between gap-2">
  <div>
    <h2 className="text-lg font-semibold tracking-tight">Running your collection</h2>
    <p className="mt-1 text-sm text-fg-muted">
      You can leave this page open. It moves on by itself when the analysis is ready.
    </p>
  </div>
  {active && (
    <button className="btn-secondary" onClick={cancel} disabled={cancelling}>
      {cancelling ? "Cancelling..." : "Cancel run"}
    </button>
  )}
</div>
```

List:

```tsx
<ol className="relative before:absolute before:bottom-4 before:left-[15px] before:top-4 before:w-0.5 before:bg-line">
  {STAGES.map((stage) => {
    const status = fatal && !job ? "pending" : stageStatus(stage.state, view);
    return (
      <li key={stage.state} data-status={status}
          className={`relative flex items-center gap-3.5 py-3 text-[14.5px] transition-colors duration-200 ${
            status === "pending" ? "text-fg-subtle" : status === "current" ? "font-medium text-fg" : "text-fg"
          }`}>
        <StageDot status={status} />
        <span>{stage.label}</span>
      </li>
    );
  })}
</ol>
```

Root: `className="card mx-auto max-w-2xl space-y-5 p-6"`. Failure panel classes: `rounded border border-danger/40 bg-danger/10 p-4` → `rounded-xl bg-danger-soft p-4`; `text-slate-200` → `text-fg`; `text-slate-300` → `text-fg-muted`; `text-slate-400` → `text-fg-muted`; `text-slate-500` → `text-fg-subtle`; Start over `btn-ghost` → `btn-secondary`. Ready text: `"Both runs analysed. Opening results..."`. Connection text: `"Lost contact with the server. Still retrying..."`. Replace the "Job {jobId}" line removal: keep `<span className="mono">{jobId.slice(0, 12)}</span>` inside the failure panel next to the code line as `job {id}` so support can still reference it.

- [ ] **Step 4: Run** `npx vitest run tests/ExecutionProgress.test.tsx tests/CollectionWizard.test.tsx` → PASS.

- [ ] **Step 5: Commit**

```bash
git add components/collection/ExecutionProgress.tsx tests/ExecutionProgress.test.tsx
git commit -m "feat(ui): vertical run timeline with breathing active stage"
```

### Task 8: Wizard shell, stepper and direction-aware transitions

**Files:**
- Modify: `frontend/components/collection/CollectionWizard.tsx`
- Test: `frontend/tests/CollectionWizard.test.tsx`

**Interfaces:**
- Consumes: `Stepper` (Task 3), `stepVariants`/`fadeVariants` (Task 3).
- Produces: `CollectionWizard({ onDone: (summary: AnalysisSummary, jobId: string) => void })`. The `onBack` prop is **removed**.

- [ ] **Step 1: Update tests.** In `tests/CollectionWizard.test.tsx`: replace every `render(<CollectionWizard onDone={X} onBack={() => {}} />)` with `render(<CollectionWizard onDone={X} />)`; change the first test's expectation to `await waitFor(() => expect(onDone).toHaveBeenCalledWith(summary, "job_1"));`; add:

```tsx
it("shows a four-step progress indicator and no mode switch", async () => {
  render(<CollectionWizard onDone={() => {}} />);
  const steps = screen.getAllByRole("listitem").filter((li) => li.dataset.state);
  expect(steps.map((s) => s.textContent)).toEqual(["1Files", "2Variables", "3Review", "4Run"]);
  expect(screen.queryByRole("button", { name: /choose another mode/i })).toBeNull();
});
```

- [ ] **Step 2: Run** → FAIL (onDone called with one argument; old step labels; mode button present).

- [ ] **Step 3: Rewrite the component shell.** Keep all state and handlers; change these parts:

```tsx
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Stepper } from "@/components/ui/Stepper";
import { fadeVariants, stepVariants } from "@/lib/motion";

type Step = "files" | "variables" | "review" | "progress";
const ORDER: Step[] = ["files", "variables", "review", "progress"];
const STEP_LABELS = ["Files", "Variables", "Review", "Run"];

export function CollectionWizard({ onDone }: { onDone: (summary: AnalysisSummary, jobId: string) => void }) {
  const [step, setStepState] = useState<Step>("files");
  const [direction, setDirection] = useState<1 | -1>(1);
  const reduce = useReducedMotion();
  function setStep(next: Step) {
    setDirection(ORDER.indexOf(next) >= ORDER.indexOf(step) ? 1 : -1);
    setStepState(next);
  }
  // ...all existing state, clearSecrets, startAttempt, submit, retry, startOver unchanged...

  return (
    <div>
      <Stepper steps={STEP_LABELS} current={ORDER.indexOf(step)} />
      <AnimatePresence mode="wait" custom={direction} initial={false}>
        <motion.div
          key={step}
          custom={direction}
          variants={reduce ? fadeVariants : stepVariants}
          initial="enter"
          animate="center"
          exit="exit"
        >
          {/* the four existing `{step === ... && (...)}` blocks, unchanged, except: */}
          {/* ExecutionProgress: onReady={(summary) => onDone(summary, jobId)} */}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
```

Delete the old `<h1>Run a Postman collection</h1>`, the "Choose another mode" button and the old `<ol>` step pills. In the progress block use `jobId` from state: `onReady={(summary) => onDone(summary, jobId)}` (guarded by the existing `jobId &&`).

- [ ] **Step 4: Run** `npx vitest run tests/CollectionWizard.test.tsx && npm run typecheck`. Expected: wizard tests PASS; typecheck FAILS only in `app/page.tsx` (still passes `onBack`); that is fixed in Task 9, which must immediately follow in the same session.

- [ ] **Step 5: Commit** (typecheck is restored by Task 9's commit)

```bash
git add components/collection/CollectionWizard.tsx tests/CollectionWizard.test.tsx
git commit -m "feat(ui): wizard stepper with direction-aware step transitions"
```

### Task 9: Collection-only start page and "New analysis" that deletes server data

**Files:**
- Create: `frontend/lib/session.ts`
- Modify: `frontend/lib/api.ts` (add `deleteAnalysis`), `frontend/app/page.tsx`
- Delete: `frontend/components/ModeChooser.tsx`, `frontend/components/Uploader.tsx`, `frontend/tests/ModeChooser.test.tsx`
- Test: `frontend/tests/session.test.ts`, `frontend/tests/api.test.ts` (add one case)

**Interfaces:**
- Consumes: `CollectionWizard({ onDone(summary, jobId) })` (Task 8), `AppHeader({ right })` (Task 2), `ToastProvider` (Task 3).
- Produces: `api.deleteAnalysis(id: string): Promise<void>`; `discardSession(ids: { analysisId: string | null; jobId: string | null }): Promise<void>` (never rejects).

- [ ] **Step 1: Write failing tests.** Create `tests/session.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { discardSession } from "@/lib/session";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, deleteAnalysis: vi.fn(), deleteExecutionJob: vi.fn() } };
});
const m = vi.mocked(api);

beforeEach(() => {
  m.deleteAnalysis.mockReset();
  m.deleteExecutionJob.mockReset();
});

describe("discardSession", () => {
  it("deletes the analysis and its job", async () => {
    m.deleteAnalysis.mockResolvedValue(undefined);
    m.deleteExecutionJob.mockResolvedValue(undefined);
    await discardSession({ analysisId: "an_1", jobId: "job_1" });
    expect(m.deleteAnalysis).toHaveBeenCalledWith("an_1");
    expect(m.deleteExecutionJob).toHaveBeenCalledWith("job_1");
  });

  it("never throws when a delete fails, and skips missing ids", async () => {
    m.deleteAnalysis.mockRejectedValue(new Error("network"));
    await expect(discardSession({ analysisId: "an_1", jobId: null })).resolves.toBeUndefined();
    expect(m.deleteExecutionJob).not.toHaveBeenCalled();
  });
});
```

Add to `tests/api.test.ts` (follow the file's existing fetch-mock pattern; the case asserts method and path):

```ts
it("deleteAnalysis sends DELETE /api/v1/analyses/{id}", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  await api.deleteAnalysis("an_1");
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toMatch(/\/analyses\/an_1$/);
  expect(init.method).toBe("DELETE");
});
```

- [ ] **Step 2: Run** `npx vitest run tests/session.test.ts tests/api.test.ts` → FAIL.

- [ ] **Step 3: Implement.** In `lib/api.ts` inside `export const api = {`, next to `getAnalysis`:

```ts
  deleteAnalysis: (id: string) => send<void>(`/analyses/${encodeURIComponent(id)}`, { method: "DELETE" }),
```

Create `lib/session.ts`:

```ts
import { api } from "./api";

/** Best effort: remove server-side data for the current session. The UI
 * resets regardless; anything left behind still expires after 30 minutes. */
export async function discardSession({ analysisId, jobId }: { analysisId: string | null; jobId: string | null }): Promise<void> {
  await Promise.allSettled([
    analysisId ? api.deleteAnalysis(analysisId) : Promise.resolve(),
    jobId ? api.deleteExecutionJob(jobId) : Promise.resolve(),
  ]);
}
```

- [ ] **Step 4: Rewrite the start of `app/page.tsx`.** Remove imports of `Uploader`, `ModeChooser`, `InputMode`; remove `mode` state. Add:

```tsx
import { AppHeader } from "@/components/AppHeader";
import { ToastProvider } from "@/components/ui/Toast";
import { discardSession } from "@/lib/session";

const [jobId, setJobId] = useState<string | null>(null);
const [wizardKey, setWizardKey] = useState(0);

function openAnalysis(s: AnalysisSummary, job: string | null = null) {
  currentAnalysis.current = s.analysis_id;
  autoSubmitted.current = false;
  setAutoBusy(false); setAutoMsg(null); setTab("health");
  setSummary(s); setRuleCount(s.rule_count); setJobId(job);
}

async function newAnalysis() {
  const ids = { analysisId: summary?.analysis_id ?? null, jobId };
  currentAnalysis.current = null; autoSubmitted.current = false;
  setAutoBusy(false); setAutoMsg(null); setSummary(null); setJobId(null);
  setWizardKey((k) => k + 1); // fresh wizard state
  await discardSession(ids);
}
```

Render shell (both branches):

```tsx
return (
  <ToastProvider>
    <AppHeader right={<button className="btn-ghost" onClick={newAnalysis}>New analysis</button>} />
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      {!summary ? (
        <CollectionWizard key={wizardKey} onDone={(s, job) => openAnalysis(s, job)} />
      ) : (
        <div className="space-y-4">
          {/* Paste the CURRENT results JSX here unchanged: the tab <nav>, autoMsg paragraph and
              every `{tab === ... && (...)}` block. Delete only the old title block (<h1> collection
              name + "mode: ..." line + "Start new analysis" button); Tasks 10-11 replace the rest. */}
        </div>
      )}
    </main>
  </ToastProvider>
);
```

Delete `components/ModeChooser.tsx`, `components/Uploader.tsx`, `tests/ModeChooser.test.tsx` (`git rm`). Confirm nothing else imports them: `grep -rn "ModeChooser\|Uploader" app components lib tests` returns nothing.

- [ ] **Step 5: Run** `npm test && npm run typecheck && npm run build` → all PASS (typecheck green again).

- [ ] **Step 6: Commit**

```bash
git add -A app/page.tsx lib/api.ts lib/session.ts tests/session.test.ts tests/api.test.ts components/ModeChooser.tsx components/Uploader.tsx tests/ModeChooser.test.tsx
git commit -m "feat(ui): collection-only start page; New analysis deletes the analysis and job"
```

---

## Phase 3: Results

### Task 10: Results header, summary tiles and next-step card

**Files:**
- Create: `frontend/lib/nextStep.ts`, `frontend/components/results/ResultsHeader.tsx`, `frontend/components/results/NextStepCard.tsx`
- Modify: `frontend/app/page.tsx`, `frontend/components/HealthPanel.tsx` (drop its `SummaryCounts` usage)
- Delete: `frontend/components/SummaryCounts.tsx`
- Test: `frontend/tests/nextStep.test.ts`, `frontend/tests/results.test.tsx`

**Interfaces:**
- Consumes: `AnalysisSummary` (`readiness.state`, `summary`, `auto_correlation_status`, `jmx_status`, `rule_count`), `useToast` (Task 3), `ReadinessPill` (`components/Badge.tsx`).
- Produces:
  - `type NextStep = "auto_correlate" | "generate" | "validate" | "done" | "blocked"`
  - `nextStep(s: Pick<AnalysisSummary, "readiness" | "auto_correlation_status" | "jmx_status" | "rule_count" | "summary">): NextStep`
  - `<ResultsHeader summary={AnalysisSummary} />`
  - `<NextStepCard summary={AnalysisSummary} busy={boolean} onAutoCorrelate={() => void} onOpenTab={(tab: "generate") => void} />`

- [ ] **Step 1: Failing tests.** `tests/nextStep.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { nextStep } from "@/lib/nextStep";
import { makeSummary } from "./fixtures";

const base = {
  readiness: { state: "ready", reasons: [], blockers: [], scenario_warnings: [] },
  auto_correlation_status: "not_started",
  jmx_status: null,
  rule_count: 0,
  summary: { correlations: 2, parameterizations: 0, external_credentials: 0, cookie_managed: 0, noise: 5, review_required: 0 },
} as const;

describe("nextStep", () => {
  it("walks auto-correlate, generate, validate, done", () => {
    expect(nextStep(makeSummary(base))).toBe("auto_correlate");
    expect(nextStep(makeSummary({ ...base, auto_correlation_status: "completed", rule_count: 2 }))).toBe("generate");
    expect(nextStep(makeSummary({ ...base, auto_correlation_status: "completed", rule_count: 2, jmx_status: "generated" }))).toBe("validate");
    expect(nextStep(makeSummary({ ...base, auto_correlation_status: "completed", rule_count: 2, jmx_status: "validated" }))).toBe("done");
  });

  it("goes straight to generate when there is nothing to correlate", () => {
    expect(nextStep(makeSummary({ ...base, summary: { ...base.summary, correlations: 0 } }))).toBe("generate");
  });

  it("is blocked when the runs are not ready", () => {
    expect(nextStep(makeSummary({ ...base, readiness: { ...base.readiness, state: "not_ready" } }))).toBe("blocked");
  });
});
```

`tests/results.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NextStepCard } from "@/components/results/NextStepCard";
import { ResultsHeader } from "@/components/results/ResultsHeader";
import { makeSummary } from "./fixtures";

const summary = makeSummary({
  collection_name: "Booking Flow",
  readiness: { state: "ready", reasons: [], blockers: [], scenario_warnings: [] },
  auto_correlation_status: "not_started", jmx_status: null, rule_count: 0,
  summary: { correlations: 2, parameterizations: 0, external_credentials: 0, cookie_managed: 0, noise: 5, review_required: 0 },
});

describe("ResultsHeader", () => {
  it("shows the collection, readiness and six counts", () => {
    render(<ResultsHeader summary={summary} />);
    expect(screen.getByRole("heading", { name: "Booking Flow" })).toBeInTheDocument();
    expect(screen.getByText(/ready/i)).toBeInTheDocument();
    for (const label of ["Correlations", "Parameters", "Credentials", "Cookies", "Noise", "Needs review"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});

describe("NextStepCard", () => {
  it("offers auto-correlation first", () => {
    const onAuto = vi.fn();
    render(<NextStepCard summary={summary} busy={false} onAutoCorrelate={onAuto} onOpenTab={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Auto-correlate" }));
    expect(onAuto).toHaveBeenCalledOnce();
  });

  it("then sends the tester to Generate", () => {
    const onOpen = vi.fn();
    render(<NextStepCard summary={makeSummary({ ...summary, auto_correlation_status: "completed", rule_count: 2 })}
      busy={false} onAutoCorrelate={() => {}} onOpenTab={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Generate" }));
    expect(onOpen).toHaveBeenCalledWith("generate");
  });
});
```

- [ ] **Step 2: Run** → FAIL (modules missing).

- [ ] **Step 3: Implement `lib/nextStep.ts`**

```ts
import type { AnalysisSummary } from "./api";

export type NextStep = "auto_correlate" | "generate" | "validate" | "done" | "blocked";

export function nextStep(
  s: Pick<AnalysisSummary, "readiness" | "auto_correlation_status" | "jmx_status" | "rule_count" | "summary">,
): NextStep {
  if (s.readiness.state === "not_ready") return "blocked";
  if (s.summary.correlations > 0 && s.auto_correlation_status !== "completed") return "auto_correlate";
  if (s.jmx_status === "validated") return "done";
  if (s.jmx_status === "generated") return "validate";
  return "generate";
}
```

- [ ] **Step 4: Implement `components/results/ResultsHeader.tsx`**

```tsx
import type { AnalysisSummary } from "@/lib/api";
import { ReadinessPill } from "@/components/Badge";

const TILES: { key: keyof AnalysisSummary["summary"]; label: string; hint: string }[] = [
  { key: "correlations", label: "Correlations", hint: "Proven producer to consumer; each gets an extractor" },
  { key: "parameterizations", label: "Parameters", hint: "Changed request inputs with no producer" },
  { key: "external_credentials", label: "Credentials", hint: "Sensitive; supplied at run time" },
  { key: "cookie_managed", label: "Cookies", hint: "Handled by the HTTP Cookie Manager" },
  { key: "noise", label: "Noise", hint: "Runtime or transport values, ignored" },
  { key: "review_required", label: "Needs review", hint: "Ambiguous or placeholder values" },
];

export function ResultsHeader({ summary }: { summary: AnalysisSummary }) {
  return (
    <section className="rise mb-4">
      <div style={{ "--i": 0 } as React.CSSProperties} className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="mb-1 text-[12.5px] text-fg-subtle">Analysis results</p>
          <h1 className="text-2xl font-semibold tracking-[-0.02em]">{summary.collection_name || "Analysis"}</h1>
        </div>
        <ReadinessPill state={summary.readiness.state} />
      </div>
      <div style={{ "--i": 1 } as React.CSSProperties} className="grid grid-cols-3 gap-2.5 lg:grid-cols-6">
        {TILES.map((t) => {
          const n = summary.summary[t.key];
          const hl = t.key === "correlations" && n > 0;
          return (
            <div key={t.key} title={t.hint}
                 className={`rounded-xl border px-3.5 py-3 ${hl ? "border-accent/30 bg-accent-soft/50" : "border-line bg-surface"}`}>
              <b className={`block text-[22px] font-semibold tabular-nums tracking-[-0.02em] ${hl ? "text-accent-soft-ink" : ""}`}>{n}</b>
              <span className="text-[12.5px] text-fg-subtle">{t.label}</span>
            </div>
          );
        })}
      </div>
      {summary.readiness.blockers.map((b) => (
        <p key={b} role="alert" className="mt-3 rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger">{b}</p>
      ))}
      {summary.readiness.reasons.map((r) => (
        <p key={r} className="mt-2 rounded-[10px] bg-warn-soft px-3 py-2 text-sm text-warn">{r}</p>
      ))}
    </section>
  );
}
```

Also update `ReadinessPill` in `components/Badge.tsx` to render friendly text: `ready` → "Ready for correlation", `ready_with_review` → "Ready, review advised", other → "Not ready".

- [ ] **Step 5: Implement `components/results/NextStepCard.tsx`**

```tsx
"use client";

import { CaretRightIcon } from "@phosphor-icons/react";
import type { AnalysisSummary } from "@/lib/api";
import { nextStep } from "@/lib/nextStep";

const JOURNEY = ["Auto-correlate", "Generate JMX", "Validate with JMeter"];

export function NextStepCard({
  summary, busy, onAutoCorrelate, onOpenTab,
}: {
  summary: AnalysisSummary; busy: boolean; onAutoCorrelate: () => void; onOpenTab: (tab: "generate") => void;
}) {
  const step = nextStep(summary);
  if (step === "blocked") return null;
  const index = step === "auto_correlate" ? 0 : step === "generate" ? 1 : 2;
  const content = {
    auto_correlate: {
      title: `Next: auto-correlate the ${summary.summary.correlations} reused value${summary.summary.correlations === 1 ? "" : "s"}`,
      text: "Adds an extractor and a variable for each value, and substitutes it everywhere it is reused.",
      action: <button className="btn" disabled={busy} onClick={onAutoCorrelate}>{busy ? "Correlating..." : "Auto-correlate"}</button>,
    },
    generate: {
      title: "Next: generate the JMeter plan",
      text: "Builds a JMeter 5.6.3 test plan with every accepted correlation wired in.",
      action: <button className="btn" onClick={() => onOpenTab("generate")}>Open Generate</button>,
    },
    validate: {
      title: "Next: prove it works in JMeter 5.6.3",
      text: "Runs the generated plan once and checks every request and extracted variable.",
      action: <button className="btn" onClick={() => onOpenTab("generate")}>Open Generate</button>,
    },
    done: {
      title: "Done: your plan is validated",
      text: "Download it from the Generate tab and run it in JMeter.",
      action: <button className="btn-secondary" onClick={() => onOpenTab("generate")}>Open Generate</button>,
    },
  }[step];

  return (
    <div className="card mb-4 flex flex-wrap items-center justify-between gap-4 border-accent/30 p-4">
      <div>
        <p className="text-[15px] font-semibold">{content.title}</p>
        <p className="mt-0.5 text-[13.5px] text-fg-muted">{content.text}</p>
        <ol aria-label="Steps" className="mt-2 flex items-center gap-1.5 text-[12.5px] text-fg-subtle">
          {JOURNEY.map((j, i) => (
            <li key={j} className="flex items-center gap-1.5">
              <span className={i === index && step !== "done" ? "font-medium text-accent-soft-ink" : i < index || step === "done" ? "text-fg-muted line-through decoration-line-strong" : ""}>{j}</span>
              {i < JOURNEY.length - 1 && <CaretRightIcon size={10} aria-hidden />}
            </li>
          ))}
        </ol>
      </div>
      {content.action}
    </div>
  );
}
```

- [ ] **Step 6: Wire into `page.tsx`** (results branch): render `<ResultsHeader summary={summary} />` then `<NextStepCard summary={summary} busy={autoBusy} onAutoCorrelate={autoCorrelateAll} onOpenTab={setTab} />` before the tabs. In `autoCorrelateAll`, on success call `toast.show(r.total > 0 ? \`${r.total} correlations created\` : "Nothing to correlate")` where `const toast = useToast();` is declared in a child component. Because `page.tsx`'s `Page` renders the provider itself, move the results JSX into a `function Results(...)` component in the same file that calls `useToast()` and receives `summary`, `tab`, `setTab`, `ruleCount`, `refresh`, `autoBusy`, `autoCorrelateAll`, `autoMsg`. Replace the em-dash in the existing `setAutoMsg` string with a colon: ```Correlation completed: ${r.total} variable(s): ${r.variables.join(", ")}. Open Generate to build the JMX.` ``` (keeps the e2e match `/correlation completed/i`).

Remove `SummaryCounts` from `HealthPanel.tsx` (the header now shows those counts) and delete `components/SummaryCounts.tsx`.

- [ ] **Step 7: Run** `npm test && npm run typecheck` → PASS.

- [ ] **Step 8: Commit**

```bash
git add -A lib/nextStep.ts components/results components/Badge.tsx components/HealthPanel.tsx components/SummaryCounts.tsx app/page.tsx tests/nextStep.test.ts tests/results.test.tsx
git commit -m "feat(ui): results header with summary tiles and a guided next-step card"
```

### Task 11: Results tabs and collapsible help

**Files:**
- Modify: `frontend/app/page.tsx`, `frontend/components/HelpNote.tsx`
- Test: `frontend/tests/results.test.tsx` (add HelpNote case)

**Interfaces:**
- Consumes: `Tabs` (Task 3).
- Produces: `HelpNote` same props; renders a closed `<details>` whose summary reads "What is this page?" followed by the title.

- [ ] **Step 1: Failing test** (append to `tests/results.test.tsx`):

```tsx
import { HelpNote } from "@/components/HelpNote";

describe("HelpNote", () => {
  it("is collapsed by default behind 'What is this page?'", () => {
    render(<HelpNote title="Run health" steps={["It checks both runs."]} />);
    const details = screen.getByText(/what is this page\?/i).closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByText("It checks both runs.")).not.toBeVisible();
  });
});
```

- [ ] **Step 2: Run** → FAIL (details is `open`, text is "💡 title").

- [ ] **Step 3: Rewrite `components/HelpNote.tsx`**

```tsx
"use client";

import { QuestionIcon } from "@phosphor-icons/react";

export function HelpNote({ title, steps, tip }: { title: string; steps: string[]; tip?: string }) {
  return (
    <details className="group my-3 text-sm">
      <summary className="focus-ring inline-flex cursor-pointer list-none items-center gap-1.5 rounded text-[13px] font-medium text-accent-soft-ink [&::-webkit-details-marker]:hidden">
        <QuestionIcon size={14} aria-hidden />
        What is this page? <span className="font-normal text-fg-subtle">{title}</span>
      </summary>
      <div className="card mt-2 p-4">
        <ol className="list-decimal space-y-1 pl-5 text-fg-muted">
          {steps.map((s, i) => <li key={i}>{s}</li>)}
        </ol>
        {tip && <p className="mt-3 rounded-[10px] bg-surface2 px-3 py-2 text-[13px] text-fg-muted">Tip: {tip}</p>}
      </div>
    </details>
  );
}
```

Check `QuestionIcon` exists (`node -e "import('@phosphor-icons/react').then(p=>console.log('QuestionIcon' in p))"` prints `true`); if not, use `InfoIcon`.

- [ ] **Step 4: Replace the tab `<nav>` in `page.tsx`** with:

```tsx
<div className="card px-4 pb-4 pt-1">
  <Tabs tabs={tabs} active={tab} onChange={setTab} />
  {/* existing per-tab content unchanged */}
</div>
```

Keep the `tabs` array labels exactly as today. Replace the `autoMsg` paragraph classes with `rounded-[10px] bg-surface2 px-3 py-2 text-[13px] text-fg`. Replace the Candidates "⚡ Auto-correlate everything" block with the same content restyled: container `rounded-xl border border-accent/30 bg-accent-soft/40 p-4`, heading "Auto-correlate everything" with `<LightningIcon size={16} aria-hidden />` (phosphor), paragraph text without em-dashes: "Scans every response and, wherever a value is reused in a later request, creates the correlation automatically (extractor, variable and substitution). No manual rules needed." Button labels: `"Correlating..."`, `"Correlation completed"` (with `<CheckIcon />`), `"Auto-correlate all reused values"` (e2e depends on this label). Also replace em-dashes in every `HelpNote` step/tip string in `page.tsx` with a colon or a period.

- [ ] **Step 5: Run** `npm test && npm run typecheck` → PASS. `grep -n "—\|–" app/page.tsx components/HelpNote.tsx` → no matches.

- [ ] **Step 6: Commit**

```bash
git add app/page.tsx components/HelpNote.tsx tests/results.test.tsx
git commit -m "feat(ui): sliding results tabs and collapsible page help"
```

### Task 12: Restyle the results panels with semantic tokens

**Files:**
- Modify: `frontend/components/HealthPanel.tsx`, `Explorer.tsx`, `CandidateTable.tsx`, `InsightsPanel.tsx`, `ManualRuleForm.tsx`, `PreviewGenerate.tsx`, `Badge.tsx`, `AuthGate.tsx`
- Test: existing suites + `frontend/tests/noLegacyClasses.test.ts`

**Interfaces:** none new; purely presentational.

- [ ] **Step 1: Write the failing guard test** `tests/noLegacyClasses.test.ts`:

```ts
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = path.join(__dirname, "..");
const LEGACY = /\b(?:text|bg|border|ring|fill|stroke|divide|placeholder|from|to)-(?:slate-\d+|ink|panel|edge|brand|amber-\d+|white|black)(?:\/\d+)?\b/g;
const DASH = /[—–]/;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = path.join(dir, f);
    return statSync(p).isDirectory() ? files(p) : p.endsWith(".tsx") ? [p] : [];
  });
}

describe("migrated UI", () => {
  const all = [...files(path.join(ROOT, "components")), ...files(path.join(ROOT, "app"))];
  it.each(all.map((f) => [path.relative(ROOT, f)]))("%s uses only semantic colour classes", (rel) => {
    const src = readFileSync(path.join(ROOT, rel), "utf8");
    expect(src.match(LEGACY) ?? []).toEqual([]);
  });
  it.each(all.map((f) => [path.relative(ROOT, f)]))("%s has no em or en dashes in copy", (rel) => {
    const src = readFileSync(path.join(ROOT, rel), "utf8");
    const lines = src.split("\n").filter((l) => DASH.test(l) && !l.trim().startsWith("//") && !l.trim().startsWith("*"));
    expect(lines).toEqual([]);
  });
});
```

- [ ] **Step 2: Run** `npx vitest run tests/noLegacyClasses.test.ts` → FAIL listing every legacy class per file.

- [ ] **Step 3: Apply the class map** to each listed file (`text-white` only where it sits on an accent/danger background; otherwise `text-fg`):

| Legacy | Semantic |
|---|---|
| `text-slate-200`, `text-slate-300`, `text-white` (on neutral bg) | `text-fg` |
| `text-slate-400` | `text-fg-muted` |
| `text-slate-500`, `text-slate-600` | `text-fg-subtle` |
| `text-white` (on `bg-brand`/`bg-danger`) | `text-accent-ink` |
| `bg-ink`, `bg-ink/40`, `bg-ink/50`, `bg-ink/60`, `bg-ink/80`, `bg-slate-700/60` | `bg-surface2` |
| `bg-panel`, `bg-panel/60` | `bg-surface` |
| `border-edge`, `border-edge/40`, `border-edge/50`, `border-edge/60` | `border-line` |
| `bg-edge`, `bg-edge/30`..`bg-edge/60` | `bg-surface2` |
| `text-brand` | `text-accent-soft-ink` |
| `bg-brand` | `bg-accent` |
| `bg-brand/5`, `bg-brand/10`, `bg-brand/15`, `bg-brand/30` | `bg-accent-soft` |
| `border-brand`, `border-brand/40`, `border-brand/60` | `border-accent/40` |
| `bg-ok/10`..`bg-ok/20` | `bg-ok-soft` |
| `bg-warn/10`..`bg-warn/20` | `bg-warn-soft` |
| `bg-danger/10`..`bg-danger/20` | `bg-danger-soft` |
| `border-warn/40`, `border-warn/50`, `border-danger/40`, `border-danger/50`, `border-amber-500/30` | drop the border (soft backgrounds carry the meaning) |
| `text-amber-200`, `text-amber-300`, `bg-amber-500/10`, `bg-amber-600/25` | `text-warn`, `text-warn`, `bg-warn-soft`, `bg-warn-soft` |
| raw `<input>`/`<select>`/`<textarea>` class strings with borders | `input` (plus existing width/mono utilities) |
| `rounded` / `rounded-md` / `rounded-lg` on cards | `rounded-[14px]` via `.card`; on controls `rounded-[10px]` |
| emoji glyphs (`⚠`, `⛔`, `💡`, `⚡`, `✓`, `▲`, `▼`) | phosphor icons: `WarningIcon`, `ProhibitIcon`, `QuestionIcon`, `LightningIcon`, `CheckIcon`, `ArrowUpIcon`, `ArrowDownIcon` at `size={14}` with `aria-hidden` |
| `…` in button/busy labels | `...` |
| em/en dashes in visible copy | colon, comma, parentheses or a period |

Verify each icon name exists before use (`node -e "import('@phosphor-icons/react').then(p=>console.log(['WarningIcon','ProhibitIcon','LightningIcon','ArrowUpIcon','ArrowDownIcon'].map(k=>k+':'+(k in p)).join(' ')))"` all `true`).

Do not change any `aria-*`, labels, button names or test ids: e2e depends on `"Generate JMX"`, `"Download Generated JMX"`, `"Validate with JMeter"`, `"Preview Draft"`, `"Accept all high"`, `"Evidence"`, `"Accept"`, `"Reject"`.

- [ ] **Step 4: Run** `npm test && npm run typecheck` → the guard test passes for every file except `components/DependencyGraph.tsx` (Task 13); all other suites pass.

- [ ] **Step 5: Commit**

```bash
git add components tests/noLegacyClasses.test.ts
git commit -m "feat(ui): results panels on semantic tokens; emoji replaced by icons"
```

### Task 13: Dependency graph theming

**Files:**
- Modify: `frontend/components/DependencyGraph.tsx`, `frontend/app/globals.css`
- Test: `frontend/tests/noLegacyClasses.test.ts` (now passes for the graph), plus a colour-literal assertion

**Interfaces:**
- Produces: CSS variables `--graph-produce`, `--graph-consume`, `--graph-edge`, `--graph-edge-high`, `--graph-edge-medium`, `--graph-edge-low`, `--graph-node`, `--graph-node-border`, `--graph-text`, `--graph-grid` (RGB channels, light + dark).

- [ ] **Step 1: Failing assertion.** Append to `tests/noLegacyClasses.test.ts`:

```ts
it("DependencyGraph has no hard-coded hex colours", () => {
  const src = readFileSync(path.join(ROOT, "components", "DependencyGraph.tsx"), "utf8");
  expect(src.match(/#[0-9a-fA-F]{6}\b/g) ?? []).toEqual([]);
});
```

- [ ] **Step 2: Run** → FAIL listing `#64748b`, `#4f9cff`, `#f59e0b`, `#a855f7`, `#22c55e`, `#e6edf7`, `#cbd5e1`, `#94a3b8`, `#334155`, `#22d3ee`, `#111a2e`, `#0b1220`.

- [ ] **Step 3: Add graph tokens** to `globals.css` inside `:root`:

```css
  --graph-node: 255 255 255;
  --graph-node-border: 211 216 223;
  --graph-text: 21 24 29;
  --graph-grid: 230 232 236;
  --graph-edge: 100 108 121;
  --graph-edge-high: 4 120 87;
  --graph-edge-medium: 180 83 9;
  --graph-edge-low: 100 108 121;
  --graph-produce: 47 91 234;
  --graph-consume: 124 58 237;
```

and inside `[data-theme="dark"]`:

```css
  --graph-node: 22 25 31;
  --graph-node-border: 52 58 68;
  --graph-text: 236 238 242;
  --graph-grid: 38 43 51;
  --graph-edge: 138 146 158;
  --graph-edge-high: 52 211 153;
  --graph-edge-medium: 251 191 36;
  --graph-edge-low: 138 146 158;
  --graph-produce: 123 155 255;
  --graph-consume: 196 167 255;
```

- [ ] **Step 4: Replace each literal** in `DependencyGraph.tsx` with `rgb(var(--graph-*))` by role (read the surrounding code for each literal to pick the role):

| Literal (current role) | Replacement |
|---|---|
| `#0b1220`, `#111a2e` (canvas / node fill) | `rgb(var(--bg))` / `rgb(var(--graph-node))` |
| `#334155` (node border, grid) | `rgb(var(--graph-node-border))` |
| `#e6edf7`, `#cbd5e1` (text) | `rgb(var(--graph-text))` |
| `#94a3b8`, `#64748b` (secondary text, low edges) | `rgb(var(--graph-edge-low))` |
| `#22c55e` (high confidence edge) | `rgb(var(--graph-edge-high))` |
| `#f59e0b` (medium edge) | `rgb(var(--graph-edge-medium))` |
| `#4f9cff`, `#22d3ee` (produce marker, selection) | `rgb(var(--graph-produce))` |
| `#a855f7` (consume marker) | `rgb(var(--graph-consume))` |

For SVG attributes use `style={{ fill: "rgb(var(--graph-node))" }}` / `stroke` style props (CSS variables do not resolve in presentation attributes in every browser). Also apply the Task 12 class map to the graph's toolbar/filter controls and panels, and replace the `▲`/`▼` legend glyphs with `ArrowUpIcon`/`ArrowDownIcon`.

- [ ] **Step 5: Run** `npm test && npm run typecheck && npm run build` → PASS (guard test fully green).

- [ ] **Step 6: Commit**

```bash
git add components/DependencyGraph.tsx app/globals.css tests/noLegacyClasses.test.ts
git commit -m "feat(ui): dependency graph colours from theme tokens (light and dark)"
```

---

## Phase 4: Polish and QA

### Task 14: Remove legacy aliases, update e2e and the tester guide

**Files:**
- Modify: `frontend/tailwind.config.ts`, `frontend/e2e/collection-mode.spec.ts`, `frontend/e2e/modes-and-failures.spec.ts`, `docs/HOW_TO_RUN_AND_TEST.md`, `README.md` (Running Postman collections section)

- [ ] **Step 1: Delete the `legacy` block** and `...legacy` from `tailwind.config.ts`.

- [ ] **Step 2: Run** `npm run build && npm test && npm run typecheck` → PASS (build proves no class still needs an alias; the guard test already enforces it).

- [ ] **Step 3: Update e2e specs.**
  - `modes-and-failures.spec.ts`: delete the `"report mode: ..."` test and the unused `SAMPLES` constant; in the remaining tests delete every `await page.getByRole("button", { name: /run a postman collection/i }).click();` line (the app opens on step 1).
  - `collection-mode.spec.ts`: delete the same mode click; replace `await expect(page.getByText(/executing your collection/i)).toBeVisible();` with `await expect(page.getByRole("heading", { name: /running your collection/i })).toBeVisible();`; replace `await expect(page.getByText(/mode: two_run/)).toBeVisible();` with `await expect(page.getByText("Analysis results")).toBeVisible();`; replace `getByRole("button", { name: "Run health" })` and `getByRole("button", { name: /^Candidates/ })` / `/^Generate/` with `getByRole("tab", { name: ... })` (tabs are now `role="tab"`). Keep `getByRole("heading", { name: "E2E Checkout Demo" })`.
  - Add to the first test, before inspecting: `await expect(page.getByRole("button", { name: /how we handle your data/i })).toBeVisible();`

- [ ] **Step 4: Update docs.** In `docs/HOW_TO_RUN_AND_TEST.md`: Test A becomes "Run the booking-flow collection (Test D)" note, i.e. remove the "Upload Newman reports" UI steps and state that report upload is API-only (`POST /api/v1/analyses`); Test D step 1 becomes "The app opens on step 1 (Files)"; add a "Theme" line (toggle in the header; follows the system by default); add the privacy-note check to Test E: "Open **How we handle your data** under the upload and check the four facts." Remove "choose Run a Postman collection" from README's step 4.

- [ ] **Step 5: Commit**

```bash
git add tailwind.config.ts e2e ../docs/HOW_TO_RUN_AND_TEST.md ../README.md
git commit -m "chore(ui): drop legacy colour aliases; e2e and guide follow the collection-only start"
```

### Task 15: Full verification and screenshots

**Files:**
- Create: `frontend/scripts/capture-redesign-screenshots.mjs`, `reports/assets/redesign/*.png`

- [ ] **Step 1: Static checks**

Run (in `frontend/`): `npm test && npm run typecheck && npm run build`
Expected: all suites pass (including tokens, noLegacyClasses, ui, PrivacyNote, session, nextStep, results); typecheck clean; build succeeds.

- [ ] **Step 2: Start the real stack** (two terminals, from repo root):

```powershell
cd backend; $env:B11_NEWMAN_RUNNER="docker"; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend; npm run dev
```

- [ ] **Step 3: Run e2e**

Run: `npm run e2e`
Expected: collection-mode, blocked-destination and unresolved-variables tests pass (report-mode test removed).

- [ ] **Step 4: Capture screenshots** with this script (`frontend/scripts/capture-redesign-screenshots.mjs`):

```js
import { chromium } from "@playwright/test";
import path from "node:path";

const OUT = path.join(import.meta.dirname, "..", "..", "reports", "assets", "redesign");
const FIX = path.join(import.meta.dirname, "..", "..", "samples");
const base = process.env.BASE_URL ?? "http://localhost:3000";

const browser = await chromium.launch();
for (const theme of ["light", "dark"]) {
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, colorScheme: theme });
  const page = await ctx.newPage();
  await page.goto(base);
  await page.screenshot({ path: path.join(OUT, `${theme}-1-files.png`), fullPage: true });
  await page.getByLabel("Collection file").setInputFiles(path.join(FIX, "booking-flow.postman_collection.json"));
  await page.getByLabel("Environment file").setInputFiles(path.join(FIX, "booking-flow.postman_environment.json"));
  await page.getByRole("button", { name: /how we handle your data/i }).click();
  await page.screenshot({ path: path.join(OUT, `${theme}-1b-files-privacy.png`), fullPage: true });
  await page.getByRole("button", { name: /inspect collection/i }).click();
  await page.getByRole("button", { name: /continue/i }).waitFor();
  await page.screenshot({ path: path.join(OUT, `${theme}-2-variables.png`), fullPage: true });
  await page.getByRole("button", { name: /continue/i }).click();
  await page.screenshot({ path: path.join(OUT, `${theme}-3-review.png`), fullPage: true });
  await page.getByRole("button", { name: /run collection twice/i }).click();
  await page.getByRole("heading", { name: /running your collection/i }).waitFor();
  await page.screenshot({ path: path.join(OUT, `${theme}-4-run.png`), fullPage: true });
  await page.getByText("Analysis results").waitFor({ timeout: 240_000 });
  await page.screenshot({ path: path.join(OUT, `${theme}-5-results.png`), fullPage: true });
  for (const tab of ["Explorer", "Candidates", "Classification", "Dependency graph", "Add rule", "Generate"]) {
    await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, `${theme}-6-${tab.toLowerCase().replace(/ /g, "-")}.png`), fullPage: true });
  }
  await ctx.close();
}
await browser.close();
console.log("screenshots in", OUT);
```

Run: `New-Item -ItemType Directory -Force ..\reports\assets\redesign; node scripts/capture-redesign-screenshots.mjs`
Expected: 26 PNGs (13 per theme). Open each and check: no low-contrast text, no clipped headings, consistent radii, dark graph readable.

- [ ] **Step 5: Reduced motion check.** Re-run Step 4's first two screenshots with `reducedMotion: "reduce"` in `newContext` and confirm the step transition is an instant fade (content visible immediately, no horizontal offset).

- [ ] **Step 6: Commit**

```bash
git add scripts/capture-redesign-screenshots.mjs ../reports/assets/redesign
git commit -m "test(ui): redesign screenshots in light and dark via Playwright"
```

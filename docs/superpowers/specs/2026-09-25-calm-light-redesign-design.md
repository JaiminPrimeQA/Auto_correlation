# Calm Light redesign: collection-only journey, privacy promise, motion

Date: 2026-09-25 · Status: approved in brainstorming, awaiting spec review
Mockups (local, not committed): `.superpowers/brainstorm/2262-1790331810/content/`
(`visual-direction.html`, `foundations.html`, `journey.html`)

## 1. Goal

Make Baseline11 feel modern, calm and trustworthy for performance testers:

1. **One journey only.** The app opens directly on the "Run a Postman collection" wizard.
   The "Upload existing Newman reports" mode is removed from the UI.
2. **Privacy promise.** Users are told, accurately, what happens to their files.
3. **Calm Light visual language** across the whole app, with a matching dark theme that
   follows the system setting and can be switched in the header.
4. **Purposeful motion**: every animation communicates direction, progress, feedback or a
   state change, and degrades to fades under "reduce motion".

Non-goals: backend behaviour changes, new analysis features, renaming results tabs,
changing any API.

## 2. Decisions made

| Topic | Decision |
|---|---|
| Newman-report mode | Hidden in the UI only. `POST /api/v1/analyses` (report upload) stays; the collection flow and backend tests use the same pipeline. |
| Privacy message | Short promise under the upload plus a "How we handle your data" disclosure. |
| Visual direction | C, Calm Light (Stripe-dashboard feel). |
| Scope | Whole app: wizard, progress, results header and every results tab. |
| Dark mode | Light + dark; follows the system by default; Light / Dark / System toggle. |
| Implementation | CSS-variable design tokens + the Motion library for exit/step/layout motion; CSS transitions for hover/press/fade. |

## 3. Foundations

### 3.1 Colour tokens

All colours are CSS custom properties. Light values live on `:root`; dark values on
`[data-theme="dark"]`. Components only ever use tokens.

| Token | Light | Dark |
|---|---|---|
| `--bg` | `#f6f7f9` | `#0f1115` |
| `--surface` | `#ffffff` | `#16191f` |
| `--surface-2` | `#fafbfc` | `#1b1f26` |
| `--border` | `#e6e8ec` | `#262b33` |
| `--border-strong` | `#d3d8df` | `#343a44` |
| `--ink` | `#15181d` | `#eceef2` |
| `--muted` | `#555d69` | `#a4abb6` |
| `--subtle` | `#646c79` | `#8a929e` |
| `--accent` | `#2f5bea` | `#7b9bff` |
| `--accent-hover` | `#2650d8` | `#93adff` |
| `--accent-ink` (text on accent) | `#ffffff` | `#0c1530` |
| `--accent-soft` / `--accent-soft-ink` | `#eaf0fe` / `#2447c4` | `rgba(123,155,255,.14)` / `#b3c6ff` |
| `--ok` / `--ok-soft` | `#047857` / `#ecfdf5` | `#34d399` / `rgba(52,211,153,.12)` |
| `--warn` / `--warn-soft` | `#b45309` / `#fff7e6` | `#fbbf24` / `rgba(251,191,36,.12)` |
| `--danger` / `--danger-soft` | `#c9302c` / `#fdf0ef` | `#f87171` / `rgba(248,113,113,.12)` |

Rules: one accent, used only for primary actions, focus and progress. Green/amber/red
only for real status. Every text/background pair meets WCAG AA (lowest measured: 4.71:1).

### 3.2 Type, shape, depth

- Fonts: **Geist** for all text, **Geist Mono** only for real values (variable names,
  JSONPaths, hosts, JSON). Self-hosted via the `geist` package and `next/font`.
- Scale: page title 28-32px / 1.12 / -0.03em; section 18px / 600; body 14px / 1.55;
  small 12.5px. Large text gets negative tracking; body stays at 0.
- Radius: controls (buttons, inputs) 10px; cards 14px; badges, steps, toggle fully round.
- Shadow: `0 1px 2px rgba(20,30,50,.04), 0 8px 24px rgba(20,30,50,.06)` (light);
  darker equivalent in dark. Never pure black.
- Focus: 3px accent ring at 25% (light) / 35% (dark) opacity, always visible on keyboard focus.
- Icons: `@phosphor-icons/react`, one weight across the app. Emoji in UI copy are removed.

### 3.3 Theme switching

`ThemeToggle` (Light / Dark / System) in the header. The choice is stored in
`localStorage` (per-browser convenience only; wrapped in try/catch). An inline script in
`layout.tsx` sets `data-theme` before first paint to avoid a flash. "System" follows
`prefers-color-scheme` live.

## 4. Journey

### 4.1 Header

Brand mark + "Baseline11 Auto-Correlate", theme toggle, "New analysis". **New
behaviour:** "New analysis" now deletes server-side data before resetting: it calls
`DELETE /api/v1/analyses/{id}` for the open analysis and `DELETE /api/v1/execution-jobs/{id}`
for its job (both endpoints already exist; today the button only resets the UI). A failed
delete does not block the reset; the data still expires after 30 minutes. The JMeter
reference link moves to a small footer link.

### 4.2 Wizard (Files → Variables → Review → Run)

- **Stepper**: four numbered steps joined by lines that fill as you progress; current
  step accent, completed steps show a check. "Analysis results" is no longer a wizard step.
- **Files** (start page): split layout. Left: headline "Turn a Postman collection into a
  correlated JMeter plan", one-sentence lede, three "how it works" points. Right card:
  collection drop zone, optional environment drop zone, **privacy note**, "Inspect
  collection". Picked files turn the zone solid with file name and counts.
- **Variables**: same fields and validation as today, in a two-column card. Badges:
  `environment`, `secret`, `set by script`. Secrets stay hidden inputs, cleared on start.
- **Review**: scope choice as selectable rows (whole collection or a folder, with request
  counts), target domains and run facts, "Run collection twice".
- **Run**: vertical timeline (Validating, Run 1 baseline, Run 2 comparison, Analysing) with
  per-stage description and elapsed time; failures show the existing guidance and Retry.
  Moves to results automatically when ready.
- "Choose another mode" is removed everywhere.

### 4.3 Privacy note (`PrivacyNote`)

Promise: "Your files stay private: processed in memory, never stored, and deleted after
30 minutes." Disclosure "How we handle your data" lists exactly:

1. Files and typed values are held in memory only. There is no database.
2. Each run's temporary workspace is deleted as soon as the run ends.
3. Results expire after 30 minutes. "New analysis" deletes them immediately.
4. Secrets are hidden as you type, never logged, and never written into the JMX.

These statements describe the local/default deployment and were verified against the
code (`job_ttl_seconds`, `session_ttl_seconds` = 30 min; `newman_workspace` rmtree;
JMX secret externalisation). Fact 3's "deletes them immediately" depends on the new
"New analysis" behaviour in 4.1. **Constraint:** when `B11_JOB_BACKEND=aws` the frontend must
not claim "no database"; the note reads its wording from a build-time flag
`NEXT_PUBLIC_PRIVACY_MODE` (`local` default, `aws` variant: "stored encrypted and
deleted after 30 minutes").

### 4.4 Results page

- **Header**: "Analysis results" label, collection name, readiness pill
  (ready / ready with review / not ready, existing data).
- **Summary tiles**: the six existing classification counts (Correlations highlighted).
- **Next-step card**: guides Auto-correlate → Generate JMX → Validate with JMeter using the
  existing endpoints and states (`auto_correlation_status`, `jmx_status`). It is a shortcut;
  the same actions remain in their tabs.
- **Tabs**: same seven tabs and labels; sliding underline; `HelpNote` content collapsed
  behind "What is this page?".
- Every tab restyled with tokens. `DependencyGraph` SVG colours move to CSS variables so
  it renders correctly in dark mode.

## 5. Motion system

Shared values in `lib/motion.ts` (and matching CSS variables):

| Token | Value | Use |
|---|---|---|
| ease-out | `cubic-bezier(0.23, 1, 0.32, 1)` | entering / responding |
| ease-in-out | `cubic-bezier(0.77, 0, 0.175, 1)` | on-screen movement |
| press | 140ms, `scale(0.97)` | buttons, clickable cards |
| quick | 150ms | hover, colour |
| enter | 300ms | step change, panels, toasts |
| stagger | 50ms per item, max 6 | screen content rise-in |
| spring | `{ type: "spring", bounce: 0, duration: 0.4 }` | tab underline, expanding panels |

| Moment | Motion |
|---|---|
| Wizard step change | slide 18px in travel direction + fade + 2px blur (Motion `AnimatePresence`) |
| New screen content | 8px rise, stagger |
| Stepper connector | fill left to right (ease-in-out, 360ms) |
| File picked | zone to solid, icon settle |
| Privacy disclosure | height open via grid-rows transition |
| Run timeline | active stage breathes; done stages tick with time |
| Tabs | underline slides (spring) |
| Next-step / slow actions | busy label, then success toast |
| Tables, graph | hover highlight only |

Not animated: keyboard actions, typing, sorting/filtering, graph pan/zoom.
Animate only `transform`, `opacity` (and `filter` blur ≤ 2px). Hover effects gated by
`(hover: hover) and (pointer: fine)`. Under `prefers-reduced-motion: reduce` all movement
becomes a 180ms opacity fade and the breathing indicator is static.

## 6. Architecture and files

New dependencies: `motion`, `geist`, `@phosphor-icons/react`.

| File | Change |
|---|---|
| `app/globals.css` | tokens (light/dark), rebuilt `.btn`, `.card`, `.badge`, `.input`, `.tab`, reduced-motion block |
| `tailwind.config.ts` | colours mapped to CSS variables; legacy names (`ink`, `panel`, `edge`, `brand`, `ok`, `warn`, `danger`) kept as aliases during migration, removed in phase 4 |
| `app/layout.tsx` | Geist fonts, header with `ThemeToggle`, no-flash theme script, footer |
| `app/page.tsx` | starts on `CollectionWizard`; `ModeChooser` / `Uploader` usage removed; results header, next-step card, tabs; "New analysis" deletes the analysis and job |
| `lib/api.ts` | add `deleteAnalysis(id)` (`DELETE /analyses/{id}`); `CollectionWizard.onDone` also passes the job id it already holds (no backend change) |
| `components/ModeChooser.tsx`, `components/Uploader.tsx` | deleted |
| `lib/motion.ts` | new: motion tokens + helpers |
| `components/ThemeToggle.tsx` | new |
| `components/PrivacyNote.tsx` | new |
| `components/ui/Stepper.tsx`, `components/ui/Tabs.tsx`, `components/ui/Toast.tsx` | new |
| `components/results/ResultsHeader.tsx`, `components/results/NextStepCard.tsx` | new |
| `components/collection/*` | restyled; step transitions; Files split layout |
| all other components | restyled with tokens only (no behaviour change) |

Data flow is unchanged apart from the "New analysis" deletes: the same `lib/api.ts`
calls, same state in `CollectionWizard` and `page.tsx`.

## 7. Error handling

Behaviour unchanged. Errors keep their current messages and guidance, restyled as inline
alerts (`--danger-soft` / `--warn-soft`). Toasts are only for success; errors never
auto-dismiss. Retry flows (secrets re-entered) unchanged.

## 8. Testing and acceptance

- Vitest: delete `ModeChooser.test.tsx`; update `CollectionWizard.test.tsx`; add
  `ThemeToggle`, `PrivacyNote`, `Stepper`, `NextStepCard` tests, and a test that
  "New analysis" calls `deleteAnalysis` (and still resets when the call fails). All suites pass;
  `npm run typecheck` clean; `npm run build` succeeds.
- Playwright: remove the "report mode" test (UI removed; API covered by backend tests);
  update the collection, blocked-destination and unresolved-variables tests to the new
  layout; all pass against the real stack (Docker Newman + JMeter).
- Manual QA with evidence (screenshots, light and dark) of: Files, Variables, Review,
  Run (in progress and failed), Results header, every tab.
- Contrast: every token pair re-checked in both themes (AA).
- Reduced motion: verified with the OS/browser setting on.
- Tester guide `docs/HOW_TO_RUN_AND_TEST.md` updated (no report-upload test; new start page).

## 9. Delivery phases (review after each)

1. **Foundations**: deps, tokens, fonts, theme toggle, motion lib, base classes.
2. **Journey**: header, wizard + stepper + transitions, privacy note, run timeline, mode removal.
3. **Results**: header, tiles, next-step card, tabs, restyle every tab incl. graph.
4. **Polish and QA**: alias removal, tests, e2e, contrast, reduced motion, screenshots, guide.

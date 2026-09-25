# Frontend Two-Mode Wizard and Progress (Phase 4) Implementation Plan

**Goal:** A tester can pick "Run a Postman collection" (recommended) or "Upload existing Newman reports", and the collection mode walks them through Files → Variables → Scope & review → Execution progress → the existing analysis pages, using the Phase 1–3 endpoints.

**Spec:** [docs/specs/2026-09-24-postman-collection-execution-design.md](../../specs/2026-09-24-postman-collection-execution-design.md) §3 steps 1–7 and 14, §7 (frontend design), §12 criteria 3, 4, 8, 9, 12; §13 item 4.

**Tech stack:** Next.js 15 / React 19 client components, Tailwind (existing `card`/`btn`/`btn-ghost`/`badge` classes), Vitest + Testing Library + jsdom (new, dev-only).

## Global constraints

- All HTTP goes through `lib/api.ts` (`/api/v1`, proxied by `next.config.mjs`). Problem+JSON errors surface as `ApiError` with `status`, `code`, `detail`.
- Secret variables (`sensitive: true`) are `type="password"` inputs, are never redisplayed after submission, and are cleared from component state as soon as the job is created.
- One submission attempt = one `Idempotency-Key` (`crypto.randomUUID()`); an in-flight guard (ref, not just state) blocks a second POST from a double click. A retry is a new attempt with a new key.
- Unresolved variables must all have non-empty values before the review step can be reached; script-set variables (`source: "script"`) are shown as "set at runtime by a script", not asked for.
- Progress copy names the actual backend stage (`queued`, `validating`, `running_baseline`, `running_comparison`, `analyzing`). Failures stay on the progress page with guidance keyed by `error_code` and a retry action.
- On `ready`, fetch `GET /analyses/{analysis_id}` and hand the summary to the existing results view (Run health tab first) — no second results UI.
- The existing report-upload flow (`Uploader`) keeps working unchanged.
- TDD: pure logic in `lib/executionJob.ts` gets unit tests first; each step component gets behaviour tests. `npm test`, `npm run typecheck`, and `npm run build` must pass.

## Tasks

1. **API client** — `lib/api.ts`: `ApiError`; types `PostmanFolder`, `PostmanVariable`, `UnsupportedFeature`, `CollectionInspection`, `ExecutionJobState`, `ExecutionJob`; `inspectCollection(collection, environment?)`, `createExecutionJob({collection, environment, folderId, suppliedValues, idempotencyKey})` (multipart, `confirm=true`, `supplied_values_json`, `Idempotency-Key` header), `getExecutionJob(id)`, `deleteExecutionJob(id)`. Tests: `tests/api.test.ts` with a stubbed `fetch` (form fields, header, error mapping).
2. **Job logic** — `lib/executionJob.ts`: `STAGES`, `stageLabel`, `stageIndex`, `isTerminal`, `failureGuidance(error_code)`, `variablesToAsk(inspection)`, `missingValues(names, values)`, `suppliedValuesFor(names, values)`, `POLL_INTERVAL_MS`. Tests: `tests/executionJob.test.ts`.
3. **Mode chooser** — `components/ModeChooser.tsx`; `app/page.tsx` shows it when no analysis is loaded, collection mode first and marked recommended; each mode has a way back. Test: `tests/ModeChooser.test.tsx`.
4. **Files step** — `components/collection/FilesStep.tsx`: collection (required) + environment (optional) pickers, "Inspect collection" → `inspectCollection`; errors shown inline. Test.
5. **Variables step** — `components/collection/VariablesStep.tsx`: required inputs for unresolved names (password for sensitive), read-only list of resolved ones with their source, Continue disabled until every required value is filled. Test.
6. **Review step** — `components/collection/ReviewStep.tsx`: collection/environment names, folder select (whole collection or one folder, request count updates), request estimate, public target domains, domain warnings, unsupported features, supplied-value names (never values), execution limits (2 runs, 5 min each); "Run collection twice" with the in-flight guard. Test (incl. double-click → one call).
7. **Progress step** — `components/collection/ExecutionProgress.tsx`: polls `getExecutionJob`, stage checklist, Cancel (DELETE) while active, failure panel with guidance + Retry + Start over, on `ready` loads the analysis and calls `onReady`. Tests with fake timers.
8. **Wizard** — `components/collection/CollectionWizard.tsx`: step state machine wiring 4–7, step indicator, clears secrets after job creation, retry returns to Variables (secrets blank, others kept) with a new attempt. Test for the full happy path with a stubbed api.
9. **Verify** — `npm test`, `npm run typecheck`, `npm run build`; manual run against the live backend with `B11_NEWMAN_RUNNER=docker` using `backend/tests/fixtures/newman/echo_collection.json`; README "Running Postman collections" section gains the UI steps.

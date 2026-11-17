# UI redesign release — 12 November 2026

## Changes

- Refined neutral surfaces, typography, spacing and source selection for the existing app.
- A four-step HAR workflow with a searchable request review, separate recorded/selected/removed
  counts, selection restoration and a persistent conversion action.
- Session pages are labelled accurately alongside API calls. Default cleaned size is no longer
  presented as the size of a changed selection.
- Separate request, correlation and replacement counts in the JMX export screen.
- Local JMeter credential setup is visible before download. HAR users are told to replace the
  complete property expression with a plain value; collection users get property-based instructions.
- Inline download errors, action-specific progress labels, invalid thread/loop protection and
  temporary validation fields that clear after validation.
- File pickers and drag/drop are locked during inspection to prevent a new upload being paired
  with an old review. Keyboard source selection has associated tab panels and arrow navigation.
- Header wrapping at tablet widths; a non-sticky mobile header avoids obscuring form fields.

An implementation/review agent worked on the generation screen and independently reviewed the
integration. Its upload-race and keyboard-navigation findings were addressed.

## Verification

- Edge browser end-to-end test passed using the actual local frontend and API: upload an
  extensionless synthetic HAR, remove its script, filter requests, auto-correlate, generate,
  inspect password instructions and download a JMX. The download contains the extractor and
  token reference and does not contain the fixture password.
- Browser checks passed at 390px and 768px without horizontal document overflow, plus desktop
  at 1440px and dark mode at 768px. Screenshots were inspected.
- All 28 targeted component tests passed (upload, HAR review, generation and results). The
  preceding full suite had 233 passing tests and one outdated heading expectation; that
  expectation was corrected and passed in the targeted run, which also adds the upload-race test.
- Diff whitespace check passed.
- Production build: pending final result.

The first browser attempt hit the initial development compilation timeout. A later responsive
check found tablet header overflow; after fixing it the full browser test passed. No live
customer website was replayed and no production deployment was performed.

## Run and deploy

The ZIP contains the frontend/backend source, dependency manifests, tests, Dockerfiles and
existing infrastructure configuration. It excludes local credentials, recordings, databases,
dependencies and build caches. Follow `HAR_RELEASE.md` for local startup and deployment setup.
Build the frontend again after extracting the new package (`npm ci`, then `npm run build`).

To reproduce the browser check, start a local API with account requirements disabled and the
frontend proxy pointing to that API. Then run from `frontend`:

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:3102"
npx playwright test e2e/har-design.spec.ts
```

This test converts synthetic data without replaying the recorded hosts. Screenshots are saved
under `reports/ui-redesign`. Full target-site JMeter acceptance remains a separate check.

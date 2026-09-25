# Postman Collection Execution — Acceptance Evidence (Phase 6)

Spec: [2026-09-24-postman-collection-execution-design.md](../specs/2026-09-24-postman-collection-execution-design.md) §12.
Verified 2026-09-25 on Windows 11, Docker Desktop 29.8.0, Python 3.14 (and 3.12 in Docker, the CI version),
Node 24, Microsoft Edge (Playwright), JMeter 5.6.3.

## Results

| Suite | Command | Result |
|-------|---------|--------|
| Backend unit + integration (AWS via moto) | `backend> pytest -q` | 535 passed, 4 skipped (opt-in suites + one POSIX-only test) |
| Backend on Python 3.12 (Linux) | `docker run python:3.12-slim … pytest` | 536 passed, 3 skipped; ruff + mypy clean |
| Backend lint / types | `ruff check app tests`, `mypy app` | clean |
| Real Newman containers (Phase 3) | `B11_RUN_DOCKER_TESTS=1 pytest -m docker` | 4 passed |
| AWS path locally (moto + runner containers) | `B11_RUN_AWS_LOCAL_TESTS=1 pytest -m aws_local` | 1 passed |
| Frontend unit / component | `frontend> npm test`, `npm run typecheck` | 56 passed; clean |
| Browser end-to-end | `frontend> npm run e2e` (running app, Docker runner, JMeter) | 4 passed |
| Browser OIDC sign-in | `E2E_OIDC_BASE_URL=… npm run e2e -- e2e/oidc.spec.ts` (mock OIDC provider) | 1 passed |
| Runner entrypoint | `node --test docker/newman-runner/run.test.mjs` | 3 passed |
| Terraform | `terraform fmt -check`, `validate`, Trivy config scan | clean; no HIGH/CRITICAL |
| Container images | `docker build` api, frontend, newman, newman-runner | build; API refuses unsafe production config |

## Acceptance criteria

| # | Criterion | Evidence |
|---|-----------|----------|
| 1 | Existing Newman report upload continues to work | e2e `modes-and-failures.spec.ts` › report mode; `tests/integration/test_flow.py` |
| 2 | Collection + environment inspected without execution | `test_execution_jobs_inspect.py`, `test_postman_inspector.py`; e2e collection mode (inspect step sends nothing) |
| 3 | Missing variables shown before job creation | e2e › "unresolved variables block the run"; `VariablesStep.test.tsx`; 422 in `test_execution_job_service_create.py` |
| 4 | Tester supplies missing values and selects a folder | e2e collection mode (secret supplied); `ReviewStep.test.tsx`, `CollectionWizard.test.tsx` (folder → `folder_id`) |
| 5 | Exactly two isolated reports from identical starting input | `test_execution_job_service_run.py` (fresh deep-copied input per run); `test_docker_newman_runner.py` (fresh workspace + container per run); `test_ecs_newman_runner.py` (own S3 objects per run) |
| 6 | Run-local variable mutations work within each run | e2e collection mode (script-set `session_token` used by later requests, correlated); `test_postman_script_variables.py` |
| 7 | Generated reports pass through the current analysis pipeline | `test_job_vs_upload_parity.py` (job analysis == direct upload analysis); Docker e2e `test_full_job_reaches_ready_with_a_correlation_candidate` |
| 8 | UI reaches the existing Run Health page via the analysis id | e2e collection mode (Run health tab, `mode: two_run`); `ExecutionProgress.test.tsx` |
| 9 | Duplicate submissions do not start duplicate jobs | `ReviewStep.test.tsx` (triple click → one call); `CollectionWizard.test.tsx` (attempt keys); `test_execution_jobs_lifecycle.py`, `test_execution_jobs_aws_backend.py` (one enqueue per key); DynamoDB concurrency test |
| 10 | Public HTTPS allowed; private and metadata blocked | `test_destination_policy_*.py`; e2e › blocked destination (10.0.0.5); runner container DNS isolation (`test_unvalidated_hostnames_cannot_be_resolved_inside_the_container`); runner-subnet NACL (Terraform) |
| 11 | Secrets absent from logs, status, errors, JMX | e2e collection mode asserts the supplied secret is absent from every job API response, the server log and the downloaded JMX (and JMeter still validates it via `-J`); `test_docker_newman_runner.py` / `test_ecs_newman_runner.py` (never in argv/env); `test_metrics.py` |
| 12 | Timeouts, failed assertions, auth failures, malformed reports, unsupported features shown accurately | runner tests (timeout, exit 1 = report kept, malformed/missing/oversized report codes); `test_flow.py::test_all_401_is_blocked`; `test_postman_unsupported.py` + `ReviewStep.test.tsx`; e2e failure panel with guidance |
| 13 | Correlation never presented as successful when gates fail | `test_business_health.py` (readiness not_ready / downgraded); `test_all_401_is_blocked`; JMX builder refuses unwritten correlations (`test_echo_flow_generation.py`) |
| 14 | Full suites, security cases, frontend build, e2e fixture pass | table above |

## Defects found by Phase 6 end-to-end testing (fixed)

1. **Auto-correlation targeted headers the plan never sends.** Echo-style APIs return the client's
   own `Host`/`User-Agent`; auto-correlation proposed those, and JMX generation then failed its
   structural check, leaving no plan. Fixed with a shared header policy
   (`backend/app/services/header_policy.py`); regression `test_echo_flow_generation.py`.
2. **Sign-in ended on the Sign in screen.** The auth gate checked the user once on the callback
   page, before the code exchange, and never re-checked after returning to the app. Fixed in
   `frontend/components/AuthGate.tsx`; covered by `AuthGate.test.tsx` and the OIDC browser test.

## Not verified here

- A real `terraform apply` and runs on real ECS/Fargate (by decision: no real AWS account was
  used). Worker autoscaling and the runner NACL can only be observed in AWS.
- CI on GitHub (no remote configured); the same commands were run locally, including Python 3.12.
- Real identity providers (Cognito/Auth0/Entra); verified with a standards-compliant mock provider.

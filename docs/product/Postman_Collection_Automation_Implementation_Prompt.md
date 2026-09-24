# Baseline11 Postman Collection Automation — Implementation Prompt

Use the following prompt when assigning the implementation to a software engineer or coding agent.

---

You are working in the existing `D:\Auto_correlation` project, which contains a FastAPI backend and Next.js frontend for **Baseline11 Auto-Correlate**.

## Existing functionality

The application currently accepts one or two Newman JSON execution reports. Its existing pipeline:

1. safely parses Newman JSON reports;
2. normalizes recorded executions;
3. calculates transport and business health;
4. aligns baseline and comparison requests;
5. identifies dynamic response values reused by later requests;
6. classifies correlations, parameterizations, credentials, cookies, noise, and review-required values;
7. presents candidates, an explorer, classifications, and a dependency graph;
8. creates and edits correlation rules;
9. generates a structurally valid Apache JMeter 5.6.3 JMX file and manifest;
10. optionally executes the JMX with JMeter and distinguishes generated from runtime-validated plans.

Preserve all existing behavior, APIs, tests, safety checks, and the direct Newman-report upload workflow.

## Feature to build

Add a user-friendly input mode that accepts a Postman Collection v2.0/v2.1 JSON file and an optional Postman environment JSON file. The server must discover required variables, ask the user only for missing values, run the collection twice with Newman 6.2.2 in isolated workers, generate baseline and comparison JSON reports internally, and submit those reports to the existing correlation pipeline.

The application will be hosted on AWS or another shared server and used by multiple authenticated testers. Users may call any **public HTTPS API**. They must not be able to reach localhost, private networks, link-local networks, cloud metadata endpoints, or other internal services.

## Required user experience

On the initial page, provide two modes:

1. `Run a Postman collection` — recommended.
2. `Upload existing Newman reports` — advanced and unchanged.

For Postman collection mode, implement these steps:

1. Upload the collection and optionally an environment.
2. Inspect files without executing requests.
3. Show detected folders and variables.
4. Prefill values available in the environment without redisplaying sensitive values.
5. Ask for unresolved variables only; use password inputs for credentials.
6. Let the user select an optional folder.
7. Show a review screen with collection, folder, request estimate, target domains, warnings, and limits.
8. Start the job once and display real progress: validating, baseline, comparison, analysis, ready.
9. On success, open the existing Run Health and correlation interface using the generated `analysis_id`.
10. On failure, show the exact failed stage and an actionable, sanitized reason.

Make submission idempotent so double-clicks and retries cannot start duplicate executions.

## Execution semantics

- Create exactly two independent Newman runs.
- Both runs must start from the same original collection, environment, and user-supplied values.
- Do not use the mutated environment produced by Run A as the initial environment for Run B.
- Preserve environment and collection mutations within each run so producer scripts can supply downstream variables.
- Use Newman's JSON reporter to create `baseline.json` and `comparison.json`.
- Feed their bytes to the existing `analysis_service.build_analysis` function rather than implementing a second correlation pipeline.
- Keep generated Newman reports private and ephemeral.

## Runner architecture

Create a typed `NewmanRunner` interface. Provide:

- a fake runner for unit and API integration tests;
- a local isolated Docker runner for development;
- an interface boundary that allows an AWS ECS/Fargate runner to be added without changing API or domain logic.

Never execute Newman in the FastAPI process. Never construct a shell command string from user input. Use fixed executable/runtime configuration and typed argument arrays with shell execution disabled.

Pin Newman to version 6.2.2 in the worker image.

## Asynchronous jobs

Add an execution job model with these states:

`uploaded`, `awaiting_variables`, `queued`, `validating`, `running_baseline`, `running_comparison`, `analyzing`, `ready`, `failed`, `cancelled`, `expired`.

Implement API endpoints to inspect input, create/start a job, read status, cancel/delete a job, and obtain the resulting analysis identifier. Return `202 Accepted` for queued execution. Enforce authenticated ownership, expiration, rate limits, and configurable per-user concurrency limits.

Do not keep the original upload request open while Newman performs both runs.

## Public HTTPS network policy

Permit arbitrary public HTTPS destinations. Reject:

- plain HTTP in production;
- loopback and localhost;
- RFC1918 private IPv4 ranges;
- unique-local and private IPv6 ranges;
- link-local, multicast, unspecified, and reserved destinations;
- `169.254.169.254` and cloud metadata hostnames;
- public hostnames that resolve to blocked IP ranges;
- redirects to blocked destinations.

Validate destinations during inspection when possible, immediately before execution, and during redirect handling. Design against DNS rebinding. If dynamic scripts prevent complete URL discovery, either enforce the network policy at the worker network boundary or reject the collection with a clear unsupported-feature message.

## Isolation and limits

Run every job in a non-root isolated container/task with:

- read-only root filesystem;
- temporary per-job working directory;
- no host mounts or Docker socket;
- no privileged mode and no unnecessary capabilities;
- CPU, memory, process, report-size, and wall-clock limits;
- outbound policy blocking internal/private networks;
- cleanup after success, failure, cancellation, or expiry.

Use configurable initial defaults:

- 10 MiB collection;
- 2 MiB environment;
- 25 MiB JSON report per run;
- five minutes per run;
- two active jobs per user;
- exactly two runs for automatic two-run analysis.

## Secret handling

- Never log collection/environment bodies, request/response bodies, Authorization values, tokens, API keys, passwords, client secrets, or supplied variable values.
- Never include secrets in process arguments, job identifiers, filenames, status responses, tracebacks, or generated JMX files.
- Use temporary files with restrictive permissions and encrypted storage/transport where job material crosses services.
- Mask sensitive values in the UI and do not send imported sensitive values back to the browser.
- Delete secret material when the job expires or is deleted.
- Preserve the current JMeter runtime-property approach for external credentials.

## Accuracy requirements

Return a verified result or a specific failure. Never create false success states.

- Newman process exit, report presence, JSON validity, execution count, run health, business-error signals, assertions, and alignment must be evaluated separately.
- HTTP 2xx alone does not prove business success.
- Unresolved variables must block execution instead of becoming empty strings.
- A generated JMX is only structurally generated until Apache JMeter executes it successfully.
- A value is correlated only when the existing evidence rules prove a valid producer-to-later-consumer relationship.
- If evidence is insufficient, report `ready_with_review` or `not_ready` with reasons.
- Unsupported Postman features must be reported; do not silently omit requests.

## Required error behavior

Return stage-specific, actionable errors for malformed collection/environment files, unresolved variables, blocked destinations, missing data files, unsupported protocols, interactive authentication, runner unavailable, Newman timeout, report-size limit, process failure, malformed output, failed assertions, authentication failures, and cancellation.

All error responses and logs must be sanitized.

## Test-driven implementation

Follow strict red-green-refactor development. Write each failing test before production code and record the expected failure reason.

At minimum, add:

- collection/environment parser tests;
- variable discovery and precedence tests;
- folder selection tests;
- sensitive-variable tests;
- public/private IPv4 and IPv6 policy tests;
- localhost, metadata, DNS rebinding, and redirect-policy tests;
- job transition, idempotency, cancellation, expiry, ownership, and concurrency tests;
- timeout, oversized report, malformed report, process failure, and cleanup tests;
- command-injection and secret-redaction tests;
- fake-runner API integration tests;
- regression tests proving internally generated reports produce the same analysis as direct report upload;
- frontend behavior tests where practical, TypeScript checks, and a production build;
- an end-to-end controlled fixture that produces two successful reports, one real dynamic correlation, a generated JMX, and successful JMeter validation.

Run the complete existing backend suite after focused tests. Run lint/type checks and the frontend production build. Do not claim success without fresh command output showing zero failures.

## Documentation and operations

Update the README and tester guide with:

- both supported input modes;
- environment-variable handling;
- execution stages;
- supported and unsupported Postman features;
- security and data retention behavior;
- Docker development setup;
- AWS worker deployment model;
- troubleshooting for authentication, missing variables, timeouts, and blocked destinations.

Add configuration documentation for all limits, worker settings, queue/storage settings, retention, and network policy.

## Acceptance criteria

The feature is complete only when:

1. Existing direct Newman-report uploads still pass all regression tests.
2. Valid collection/environment input can be inspected without execution.
3. Missing variables are requested before execution.
4. Two isolated runs begin from identical supplied inputs.
5. Mutated variables work inside each run.
6. Both generated reports enter the existing analysis pipeline.
7. The frontend reaches the existing analysis UI with the resulting identifier.
8. Public HTTPS targets work and internal/private/metadata destinations are blocked.
9. Secrets are absent from logs, API responses, status output, and generated JMX.
10. Duplicate submissions are idempotent.
11. Failure and cancellation paths clean up temporary data.
12. Health and evidence gates prevent false correlation-success messages.
13. Full backend, security, frontend, and end-to-end verification passes.

Do not weaken security or accuracy checks to make a demonstration pass. Fix the collection, configuration, runner, or product behavior and report any remaining limitation truthfully.

---

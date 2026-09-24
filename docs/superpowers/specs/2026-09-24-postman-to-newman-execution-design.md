# Postman Collection to Newman Execution Design

**Date:** 2026-09-24  
**Product:** Baseline11 Auto-Correlate  
**Status:** Approved design awaiting implementation planning

## 1. Objective

Extend Baseline11 so a tester can upload a Postman collection, optionally import a Postman environment, supply only missing variables, and have the server produce two independent Newman JSON execution reports. Those reports must enter the existing analysis, correlation, dependency graph, JMX generation, and JMeter validation pipeline without duplicating correlation logic.

The product must preserve the existing direct Newman-report upload mode.

## 2. Current System

The current application accepts one or two Newman JSON reports through `POST /api/v1/analyses`.

- One report creates a lower-confidence single-run analysis.
- Two reports create a baseline/comparison analysis.
- The backend parses and normalizes Newman executions, evaluates run health, aligns requests, discovers producer-to-consumer relationships, classifies values, and creates correlation candidates.
- Users can accept or edit rules, inspect the dependency graph, generate a JMeter 5.6.3 JMX file, download its manifest, and validate the generated plan with JMeter.
- Secrets are classified as external credentials and supplied to JMeter at runtime.
- Uploaded analysis data is held in an expiring session store.

The current system does not execute Postman collections and does not create Newman reports.

## 3. Requested System

Add a new **Postman collection** input mode with this workflow:

1. Upload one Postman Collection v2.0 or v2.1 JSON file.
2. Optionally upload a Postman environment JSON file.
3. Parse collection and environment variables without executing the collection.
4. Display unresolved variables and let the tester supply their values.
5. Mask variables classified as credentials or secrets.
6. Optionally choose one folder from the collection.
7. Start an asynchronous execution job.
8. Validate every resolved request target against the public-HTTPS network policy.
9. Run the collection twice in isolated Newman 6.2.2 workers.
10. Reset Run B to the original uploaded environment and user-supplied input values.
11. Preserve mutations within each individual run so producer scripts can populate downstream variables.
12. Capture `baseline.json` and `comparison.json` using Newman's JSON reporter.
13. Pass both report byte streams directly to the existing `analysis_service.build_analysis` pipeline.
14. Present run health and correlation results only after both reports are parsed successfully.

## 4. Accuracy Contract

The application must not promise that every arbitrary Postman collection can be converted successfully. It must produce an accurate result or an explicit, actionable failure.

The system must never:

- report correlation readiness when either Newman execution did not complete;
- treat HTTP 2xx alone as proof of business success;
- create a correlation without a demonstrable earlier producer and later consumer;
- silently replace unresolved variables with empty strings;
- reuse the mutated Run A environment as the starting environment for Run B;
- expose uploaded secrets in logs, API responses, job status, or generated JMX files;
- describe a structurally valid JMX as runtime validated;
- execute a collection directly in the web/API process.

When evidence is insufficient, the result must be labelled `ready_with_review`, `not_ready`, or failed with reasons.

## 5. Architecture

### 5.1 Components

1. **Collection intake service**
   - Validates JSON, supported Postman schema, file size, nesting, and collection structure.
   - Extracts folders, variables, request templates, authentication references, and file dependencies.

2. **Variable resolution service**
   - Applies Postman precedence intentionally: supplied runtime values, environment values, collection variables, and globals supported by the product.
   - Returns unresolved variables with source locations.
   - Marks likely credentials as sensitive.

3. **Destination policy service**
   - Allows public HTTPS targets.
   - Rejects HTTP in production, loopback, link-local, private IPv4/IPv6, multicast, unspecified addresses, and cloud metadata endpoints.
   - Resolves hostnames before execution and prevents redirects to blocked addresses.
   - Revalidates DNS results to reduce DNS rebinding risk.

4. **Execution job service**
   - Creates an opaque job identifier and state machine.
   - States: `uploaded`, `awaiting_variables`, `queued`, `validating`, `running_baseline`, `running_comparison`, `analyzing`, `ready`, `failed`, `cancelled`, `expired`.
   - Enforces ownership, expiry, cancellation, rate limits, and concurrency limits.

5. **Newman runner interface**
   - Receives typed execution input rather than a constructed shell command.
   - Uses a pinned Newman 6.2.2 runtime.
   - Supports a local isolated-container implementation and a future AWS ECS/Fargate implementation.
   - Uses argument arrays with shell execution disabled.

6. **Correlation adapter**
   - Reads the two generated JSON reports.
   - Calls the existing analysis service unchanged wherever possible.
   - Associates the resulting analysis identifier with the execution job.

### 5.2 Production deployment

The API enqueues jobs. A separate execution worker runs Newman in an isolated container or task. The worker receives temporary encrypted job material, executes both runs, uploads private results, reports status, and terminates.

For AWS, the preferred target is an API service plus queue plus isolated ECS/Fargate workers. A Docker worker can be used during local development while keeping the same runner interface.

## 6. API Design

### `POST /api/v1/execution-jobs/inspect`

Multipart input:

- `collection`: required Postman collection JSON;
- `environment`: optional Postman environment JSON.

Returns collection metadata, folders, resolved variable names, unresolved variables, sensitivity flags, warnings, and unsupported features. It never returns imported secret values.

### `POST /api/v1/execution-jobs`

Multipart input:

- collection and optional environment;
- selected folder identifier or path;
- supplied variable values;
- explicit confirmation for execution.

Returns `202 Accepted`, job identifier, state, and status URL.

### `GET /api/v1/execution-jobs/{job_id}`

Returns current state, stage progress, sanitized warnings/errors, and `analysis_id` when ready.

### `DELETE /api/v1/execution-jobs/{job_id}`

Cancels an active job or deletes an owned completed job and its temporary material.

The existing `POST /api/v1/analyses` endpoint remains supported for direct report upload.

## 7. Frontend Design

The first page presents two clear modes:

1. **Run a Postman collection** — recommended for most testers.
2. **Upload existing Newman reports** — advanced/existing workflow.

The collection mode is a step-by-step form:

1. Files
2. Variables
3. Scope and review
4. Execution progress
5. Analysis results

The review step shows the selected collection, environment, folder, request count estimate, public domains, unresolved values, and execution limits. Secret fields are password inputs and are never redisplayed after submission.

Progress copy must identify the actual stage. Failures remain on the progress page with corrective guidance and a retry action that does not accidentally submit twice.

## 8. Isolation and Security

- Execute Newman outside the API process.
- Use a non-root container with a read-only root filesystem and a temporary writable working directory.
- Disable privileged mode, host mounts, Docker socket access, and unnecessary Linux capabilities.
- Apply CPU, memory, process, file-size, and wall-clock limits.
- Use outbound network controls that block private and metadata ranges.
- Use per-job temporary files with restrictive permissions.
- Never place secret values in process arguments, logs, filenames, or status messages.
- Delete job files when the job completes, is cancelled, or expires.
- Keep generated Newman reports private because they may contain request and response data.
- Authenticate users and enforce job ownership before production deployment.

## 9. Initial Limits

- Collection: 10 MiB
- Environment: 2 MiB
- Generated report: 25 MiB per run
- Runtime: 5 minutes per run
- Runs: exactly two per automatic analysis
- Concurrent active jobs: two per user by default
- Protocol: public HTTPS only in production
- Newman runtime: pinned to 6.2.2

All limits must be configurable.

## 10. Unsupported and Review-Required Cases

The inspection stage must identify cases such as:

- missing local data files;
- interactive OAuth or browser login;
- client certificates not supplied through a supported secure mechanism;
- WebSocket or gRPC requests;
- requests whose target cannot be resolved before runtime;
- scripts or dynamic URL construction that prevents complete preflight analysis;
- requests requiring access to private networks;
- collection behavior that depends on Run A mutations carrying into Run B.

Unsupported cases are rejected or clearly marked for review. They are never silently skipped.

## 11. Testing Strategy

Development follows test-driven development.

- Unit tests for schema validation, variable discovery, precedence, sensitivity classification, URL/IP policy, redirect policy, command construction, limits, state transitions, and cleanup.
- Integration tests with a fake runner for job endpoints and failure behavior.
- Runner contract tests using a fixed Newman fixture collection.
- Regression tests confirming generated reports still produce the same analysis as direct report upload.
- Security tests for localhost, IPv4/IPv6 private ranges, metadata addresses, DNS changes, redirect targets, command injection strings, oversized output, timeouts, and secret redaction.
- Frontend build/type verification and component behavior tests for both input modes.
- End-to-end demonstration against a controlled public or local-development fixture, including two successful runs, correlation, JMX generation, and JMeter validation.

Tests must prove failure behavior as well as success behavior.

## 12. Acceptance Criteria

1. Existing Newman report upload continues to work.
2. A valid collection and environment can be inspected without execution.
3. Missing variables are shown before job creation.
4. A tester can supply missing values and select a folder.
5. Exactly two isolated Newman reports are generated from identical starting input.
6. Run-local variable mutations work within each run.
7. Generated reports pass through the current analysis pipeline.
8. The UI reaches the existing Run Health page using the generated analysis identifier.
9. Duplicate submissions do not start duplicate jobs.
10. Public HTTPS APIs are allowed; private and metadata destinations are blocked.
11. Secrets are absent from logs, status responses, error messages, and generated JMX content.
12. Timeouts, failed assertions, authentication failures, malformed reports, and unsupported features are shown accurately.
13. Automatic correlation is never presented as successful when health or evidence gates fail.
14. The complete backend test suite, security cases, frontend build, and end-to-end fixture pass before release.

## 13. Delivery Phases

1. Domain contracts, inspection, variable discovery, and destination policy.
2. Job state model, API, persistence boundary, and fake runner integration.
3. Isolated Docker Newman runner and generated-report adapter.
4. Frontend two-mode wizard and progress experience.
5. Production AWS worker adapter, authentication/ownership integration, observability, and deployment hardening.
6. Full end-to-end verification and tester documentation update.


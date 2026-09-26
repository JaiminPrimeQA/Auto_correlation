# Functional audit — 26 September 2026

## Verdict

The tested local collection-to-JMX workflow works, and concrete defects found during this audit have been corrected. The product should not be described as perfect, universally accurate, or fully verified for shared production hosting.

The principal remaining implementation gap is isolated hosted JMeter execution. Real AWS deployment and a real identity provider have not been exercised in this audit. Analysis state is still tied to one API process.

## Scope examined

Collection inspection, variable discovery, destination policy, Newman execution, AWS job finalization, analysis ownership, health gating, correlation actions, JMX generation, validation status, runtime credentials, privacy claims, tester guidance, Docker execution tests, frontend components, and browser integration. Existing visual edits in the working tree were retained.

This is an application audit with executable regression evidence, not a formal penetration test or exhaustive proof for every Postman collection.

## Defects corrected

| Finding | Correction and evidence |
|---|---|
| A signed-in user could delete another user's analysis if they knew its ID | DELETE now uses the same ownership dependency as read/download. The OIDC integration test proves the other user receives 404 and the owner can still read/delete it. |
| Automatic correlation and candidate acceptance could bypass unhealthy-run blockers | Server-side health checks now reject those actions before mutation. The main UI action is also disabled for blocked captures. |
| JMeter launched with missing or blank required credentials | Both UI and runner reject missing required values before execution. The UI explains complete Authorization header values. |
| Credentials appeared in JMeter process arguments and could be interpreted through Windows command processing | Runtime values are written to a restricted temporary Java properties file and supplied with `-q`. Escaping tests cover spaces, quotes, percent signs, backslashes, newlines and Unicode. The real browser flow validates successfully with this mechanism. |
| Validation diagnostics could echo supplied credentials | Known supplied values are redacted from the returned report, including sampler labels/messages and log lines. Regression tests reproduce the original leak using test credentials. This is not a claim that arbitrary unrecognized secrets in API responses are universally detectable. |
| A previous validation report remained after regenerating the plan | Generation clears previous validation. A result from an in-flight validation cannot mark a replaced/invalidated plan as validated. |
| One successful extraction could hide a failed extraction in a later iteration | `__NOT_FOUND__` and unresolved `${variable}` markers now fail validation even when the value was previously seen. |
| Editing generation options left stale download/validation controls visible | Changing options clears the displayed generated result, preview, validation and credentials. |
| Candidate action errors were unhandled | Load/accept/reject errors are shown in an alert; controls are disabled while the action is running. |
| The file picker advertised dropping files without implementing it | Collection/environment drop areas now accept a dropped file. |
| Privacy copy claimed memory-only processing, exact deletion timing and no secrets in JMX | Copy now explains temporary files, configurable expiry, asynchronous hosted cleanup and the explicit embed-secrets option. |
| Production API could execute uploaded-plan targets through the local JMeter runner without production isolation | Production mode now refuses this runner with a clear download-and-validate-locally explanation. Implementing the isolated worker remains necessary to enable hosted validation. |

New behavioral regression tests were run before each functional fix and failed on the original implementation. They pass after the corrections.

## Fresh verification results

| Check | Result |
|---|---|
| Full backend suite | **557 passed, 3 skipped** |
| Backend lint | `ruff check app tests`: passed |
| Backend typing | `mypy app`: passed, 78 source files |
| Frontend component/unit suite | **179 passed**, 19 files |
| Production frontend build | Passed, including `/guide`, type checking and static page generation |
| Real Docker Newman suite | **4 passed** |
| AWS workflow using local moto services and real runner containers | **1 passed** |
| Newman runner entrypoint | **3 passed** |
| Browser suite | **4 passed**: real collection → two Newman runs → JMX → JMeter, guide, blocked destination/retry, missing variables |
| Fresh seven-request webhook replay | Both Newman captures: **7/7 HTTP 200**, zero Newman failures; generated **3 extractors and 7 dependency edges**; real JMeter replay: **7/7 passed with fresh IDs** |

The full backend suite skips the opt-in Docker/AWS-local modules and one POSIX-only case on Windows; the Docker and AWS-local modules were exercised separately as listed above. Two upstream test-library deprecation warnings remain. No real AWS resources were deployed.

The webhook run used `--output-dir .smoke/audit-webhook --port 8099` so existing sample captures were not overwritten. The generated plan, manifest and validation result are in that directory for local inspection.

## What testers can use now

1. Postman v2.0/v2.1 collection plus optional environment.
2. Missing-value prompts, scope selection, destination review and two Newman executions.
3. Run health, candidate evidence, rule review, dependency graph and JMX generation.
4. Local JMeter validation with explicit runtime credentials.
5. **User guide** in the header and [the shareable tester guide](../TESTER_QUICK_START.md).
6. Existing Newman reports through the analysis API; the current UI starts with a collection wizard.

## Remaining work before claiming a complete hosted product

1. **Isolated JMeter worker:** implement queued execution with restricted egress, bounded resources and runtime-secret handling. Generated JMX can contain expressions as well as target URLs, so a simple host check in the API is insufficient. The new production guard prevents use of the current unisolated implementation.
2. **Shared analysis storage:** implement and test a persistent/shared store before increasing the API replica count. Current Terraform uses one API task, and `get_store()` returns an in-memory store. Setting a Redis URL alone does not implement this feature. Restarts lose analyses.
3. **Real AWS acceptance:** deploy to an authorized test account and verify IAM/KMS, Fargate networking, metadata/private-address blocking, cancellation, queue recovery, autoscaling, HTTPS, cleanup and operational monitoring. Moto verifies service interactions; it does not verify AWS networking or IAM enforcement.
4. **Real identity provider acceptance:** configure the intended Cognito/Auth0/Entra provider and verify sign-in, expiry, logout and ownership. Signed-token ownership tests pass locally; real provider integration is not established by them.
5. **Collection compatibility:** arbitrary scripts, file uploads, redirects and business assertions are not universally reproduced in JMeter. Unsupported features must remain explicit. Verify the actual collections your testers will use and add business assertions before performance testing.

These are material boundaries, not cosmetic refinements. A successful JMeter run establishes the checks actually executed; it does not prove every business requirement or load capacity.

## Running the updated application

Restart the backend process after pulling these changes, and refresh/restart the frontend. For local collection execution, keep Docker running and set `B11_NEWMAN_RUNNER=docker` before starting the backend. The default runner is disabled.

The normal UI remains at `http://localhost:3000`; the guide is at `/guide`. `NEXT_DIST_DIR` can select a separate Next.js build directory when running an audit server alongside an existing development instance.

Reference used for the credential transport change: [Apache JMeter command-line options](https://jmeter.apache.org/usermanual/get-started.html#options), which documents the `-q` additional-properties-file option.

# Production AWS Worker, Authentication, Observability and Hardening (Phase 5) Implementation Plan

**Goal:** The same product runs in AWS as "API service + queue + isolated ECS/Fargate workers" (spec §5.2) with authenticated users who own their jobs and analyses (spec §8 last bullet), observable job outcomes, and production-safe configuration — while local development keeps working with the Phase 3 Docker runner.

**Decisions (user, 2026-09-25):** build AWS adapters + Terraform and test them locally (no real AWS account touched); OIDC/JWT authentication (any OIDC provider); Terraform for infrastructure.

**Spec:** [2026-09-24-postman-collection-execution-design.md](../../specs/2026-09-24-postman-collection-execution-design.md) §5.1 item 4–5, §5.2, §8, §9, §12 criteria 9–11, §13 item 5.

## Architecture

```
browser ──OIDC login──▶ IdP
   │ Bearer JWT
   ▼
API (ECS service, 1 task) ── create job ──▶ DynamoDB (jobs, owner slots, idempotency)
   │  put material (SSE-KMS) ─────────────▶ S3 jobs/{id}/material.json
   │  enqueue {job_id} ───────────────────▶ SQS (+ DLQ)
   │  GET job: state=analyzing & reports ─▶ S3 reports → build analysis (in memory, as today) → READY
   ▼
Worker (ECS service, 0..N tasks, scales on queue depth, one job per task then exits)
   │  validate destinations, per run: put run input to S3, presign GET input / PUT report
   ▼
Runner task (Fargate RunTask per run; NO task role; read-only root; runner subnet whose NACL
denies 10/8, 172.16/12, 192.168/16, 100.64/10, 169.254/16 except the VPC resolver)
   runs newman 6.2.2 with the Phase 3 flags, uploads report via presigned PUT, exits
```

- The runner task has no AWS credentials, so a hostile collection script cannot reach the worker's permissions; its only capabilities are two presigned URLs for its own objects (expiry = run timeout + grace).
- Supplied values travel only inside the SSE-KMS encrypted material/run-input objects, never in env vars, task overrides, arguments, logs or status.
- Analyses stay in the API process (existing product constraint), so the API service runs as a single task; documented as a known limitation.
- Fargate cannot pin hosts (`--add-host`); DNS rebinding to private/metadata ranges is stopped by the runner-subnet NACL instead.

## Global constraints

- Everything new is behind the existing seams: `ExecutionJobStore`, `NewmanRunner`, the job dispatcher. `newman_runner`/`job_backend` settings select local vs AWS implementations; defaults stay local.
- Unit tests use `moto` (in-process AWS mocks) and never touch real AWS. Opt-in `aws_local` tests run a moto server + the real runner container.
- Secrets never in logs, metrics, task overrides, S3 keys, DynamoDB attributes, or job status.
- TDD per task; `pytest`, `ruff`, `mypy`, frontend `npm test`/`typecheck`/`build` stay green; `terraform fmt -check` and `terraform validate` (via the `hashicorp/terraform` image) pass.

## Tasks

### A. Authentication and ownership
1. **OIDC verifier** — `app/core/auth.py`: `Principal(subject, email)`; `OidcVerifier(issuer, audience, jwks_client, algorithms, leeway)` verifying signature, `iss`, `aud`, `exp`/`nbf`, required `sub`; JWKS URL from settings or `{issuer}/.well-known/openid-configuration`. Settings: `auth_mode: Literal["disabled","oidc"]`, `oidc_issuer`, `oidc_audience`, `oidc_jwks_url`, `oidc_algorithms`. Tests with a locally generated RSA key (valid, expired, wrong aud/iss, bad signature, missing sub, `alg=none`).
2. **Request principal + ownership** — `get_principal` dependency (401 problem with `WWW-Authenticate: Bearer` when oidc mode and token missing/invalid); `get_owner_key` returns `user:{sub}` in oidc mode, client IP when disabled. All `/api/v1` routers depend on it; `/health` stays open. `Analysis.owner_key` set on creation (upload and job finalize); `require_analysis` 404s for other owners. Tests for every router.
3. **Frontend login** — `oidc-client-ts` Authorization Code + PKCE when `NEXT_PUBLIC_OIDC_AUTHORITY`/`NEXT_PUBLIC_OIDC_CLIENT_ID` are set (no-op otherwise); `api.ts` attaches the bearer token; file downloads switch from bare `<a href>` to authenticated fetch + blob. Tests.

### B. AWS execution backend
4. **Settings + dependencies** — `boto3`, `PyJWT[crypto]`; dev: `moto[s3,sqs,dynamodb,ecs]`. Settings: `job_backend: Literal["local","aws"]`, `aws_region`, `aws_endpoint_url` (local testing), `jobs_table`, `material_bucket`, `kms_key_id`, `job_queue_url`, `ecs_cluster`, `runner_task_definition`, `runner_subnets`, `runner_security_groups`, `runner_container_name`, `presign_grace_seconds`.
5. **DynamoDB job store** — `DynamoDbExecutionJobStore(ExecutionJobStore)`: single table; job item `pk=JOB#{id}` with `version` for optimistic `mutate` (bounded retries); `create_if_allowed` = one `TransactWriteItems` of job put (`attribute_not_exists`), idempotency item put (`pk=IDEM#{owner}#{key}`, TTL = window) and owner slot counter update (`active < :max`); cancellation of the transaction decodes which condition failed (idempotency hit → return existing job; slots → 429). Slot released exactly once when the job's worker finishes (`slot_released` flag). DynamoDB TTL on `expires_at`. Tests against moto mirror the in-memory store's contract tests.
6. **S3 material store** — `S3ObjectStore`: put/get/delete JSON with SSE-KMS, `head` size check before get, presigned GET/PUT with expiry; key layout `jobs/{id}/material.json`, `jobs/{id}/runs/{n}/input.json`, `jobs/{id}/runs/{n}/report.json`, `jobs/{id}/reports/{baseline|comparison}.json`. Tests (moto).
7. **Queue dispatch (API side)** — `SqsJobQueue.enqueue(job_id)`; in `aws` backend the create endpoint writes material then enqueues instead of dispatching `run_job`; an enqueue/material failure marks the job FAILED `runner_unavailable` and releases the slot. Tests.
8. **Runner image** — `docker/newman-runner/`: `node:22-alpine` + `newman@6.2.2` + `run.mjs` (download input via `RUN_INPUT_URL`, write 0600 files in `/tmp/job`, spawn newman with the Phase 3 argument set without a shell and with output discarded, upload `report.json` via `REPORT_UPLOAD_URL` if ≤ limit, exit with Newman's code / 3 for runner errors). Node unit tests for argument construction + an opt-in real-container test.
9. **ECS task runner** — `EcsTaskNewmanRunner(NewmanRunner)`: per run put input, presign, `RunTask` (FARGATE, awsvpc, runner subnets/SGs, no public IP, env overrides = URLs + folder + timeouts only), poll `DescribeTasks` honouring `should_cancel` and the wall-clock deadline (`StopTask` on either), map stop reason / exit code (0/1 → fetch report with size check; 137/OOM → `resource_limit`; capacity/launch failures → `runner_unavailable`; else `process_failed`), always delete run objects. A `DockerTaskLauncher` implements the same launcher interface with local `docker run` of the runner image, for local end-to-end. Tests with a fake launcher + moto S3.
10. **Worker** — `app/worker.py` (`python -m app.worker [--once]`): long-poll SQS, load job + material, drive the job through the existing state machine with `EcsTaskNewmanRunner`, then upload both reports and set `analyzing` + `reports_ready`; delete message and material; release the owner slot; exceptions → FAILED with a fixed message. Visibility timeout covers two runs. Tests with moto + fake launcher.
11. **API finalize** — `GET /execution-jobs/{id}` on an `analyzing` job with `reports_ready`: claim finalization atomically (`mutate`), download reports, `build_analysis`, store with owner, write READY + `analysis_id`, delete report objects. Tests incl. two concurrent GETs → one analysis.
12. **Local AWS end-to-end (opt-in)** — `docker compose -f docker/compose.aws-local.yml` (moto server) + worker with `DockerTaskLauncher`; `aws_local` pytest marker drives create → worker → finalize → READY with the fixture collection.

### C. Observability
13. **Metrics + request tracing** — CloudWatch Embedded Metric Format log lines (`Baseline11/Execution`: `JobsCreated`, `JobsCompleted{outcome,error_code}`, `RunDurationSeconds{stage}`, `QueueWaitSeconds`); `X-Request-ID` accepted/echoed; job id on every job log line; `/health` (liveness) + `/health/ready` (store/queue/bucket reachability in aws mode). Tests that metric lines carry no secrets.

### D. Hardening + deployment
14. **Production settings guard** — at startup in production: `auth_mode=oidc` with issuer/audience, `newman_runner` ∈ {docker, ecs}, no wildcard CORS, `job_backend=aws` requires every AWS setting; otherwise refuse to start with a clear message. Tests.
15. **Container images** — `docker/api/Dockerfile` (python:3.12-slim, non-root, uvicorn) used for API and worker; `.dockerignore`. Build verified locally.
16. **Terraform** — `infra/terraform/`: VPC (2 AZ public/private/runner subnets, NAT), runner NACL, KMS key, S3 bucket (private, SSE-KMS, TLS-only policy, 1-day lifecycle), DynamoDB table (TTL, PITR, SSE), SQS + DLQ (KMS), ECR repos, ECS cluster, API service behind ALB (HTTPS listener with ACM cert variable), worker service with queue-depth autoscaling, runner task definition (no task role, read-only root), least-privilege IAM, CloudWatch log groups + alarms (DLQ depth, failed jobs, worker errors). `terraform fmt -check`, `validate`; README for applying it.
17. **CI + docs** — CI runs frontend tests, backend mypy (blocking), terraform fmt/validate, image builds; README production section (architecture, OIDC setup, env vars, known limitations).

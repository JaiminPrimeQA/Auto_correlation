# Baseline11 Auto-Correlate

Transform two Newman JSON run reports of the same API journey into a **correlation-ready Apache JMeter 5.6.3 test plan** — deterministically, with no LLM in the analysis path.

The app reconstructs the executed HTTP workflow, detects dynamic values passed from earlier responses into later requests, lets you review/edit/add correlation rules through a request/response explorer, and generates a valid, directly usable `.jmx` with real post-processors (JSON/XPath2/Regex/Boundary extractors) on producer samplers and `${variable}` references on every approved consumer.

---

## Why two successful runs?

A value is only a *dynamic correlation* if it **changes between independent executions** and is **produced upstream then consumed downstream**. One run cannot prove a value is dynamic (a repeated static id looks identical to a token). So the recommended input is:

- **Run A (baseline)** — first execution of a Postman collection.
- **Run B (comparison)** — a second, independent execution with fresh session data.

The engine confirms a high-confidence correlation only when the value **differs between A and B**, its source location exists in **both** aligned producer responses, and the differing value is consumed at the **equivalent location** in **both** aligned downstream requests (full rules below).

A single file still works in a clearly-labelled **lower-confidence manual mode** (producer→consumer discovery within one run; dynamism cannot be proven).

### Run-health gating

If the comparison run is unhealthy, automatic correlation is **blocked** (not silently "successful"). Default blockers (all configurable):

- more than **30%** of requests returned `401`/`403`,
- sequence alignment coverage below **80%**,
- no valid (non-error) producer responses before consumers.

Example: the classic "all seven requests returned 401" comparison run surfaces
`Run is invalid for automatic correlation: 7 of 7 requests returned 401/403 (100%). Capture another successful run with fresh valid authentication.`

---

## Architecture

```
backend/app/
  main.py                 FastAPI app factory, CORS, security headers, error handlers
  api/v1/                 analyses.py · rules.py · downloads.py  (+ deps.py)
  core/                   config · errors (RFC 9457) · logging (redacting) · security
  domain/                 enums · models (canonical, order/duplicate-preserving)
  services/
    newman_parser.py      safe JSON + complexity bounds + structure validation
    normalizer.py         raw Newman -> typed domain model
    run_health.py         status/auth/error metrics + blockers
    run_aligner.py        signature-based, occurrence-aware alignment
    value_indexer.py      producer sources / consumer sinks + safe transforms
    correlation_engine.py deterministic 8-condition two-run algorithm + scoring
    rule_validator.py     specific, coded manual-rule errors
    jmx_builder.py        JMeter 5.6.3 XML via ElementTree (never string concat)
    jmx_validator.py      re-parse, hashTree pairing, ${var} defined-before-use
    manifest_builder.py   generation manifest + dependency flow
    graph_builder.py      API dependency graph (nodes + producer->consumer edges)
  repositories/           ephemeral TTL session store (Redis-swappable interface)
  utils/                  masking · encoding · naming · xml
frontend/                 Next.js App Router + TypeScript + Tailwind
scripts/                  mock_api.py · smoke_test.py · provision_jmeter.(sh|ps1)
samples/                  example fixtures + a generated correlated .jmx
```

Domain logic is independent of FastAPI and the UI. Parsing, flattening, matching, scoring and transformations are pure functions. Headers and query parameters are kept as **ordered lists of pairs** (duplicates preserved), never flattened to dicts.

### The deterministic algorithm (two-run, high confidence)

All of the following must hold:

1. A scalar value exists in an earlier producer **response** in Run A.
2. The same source location exists in the **aligned** producer response in Run B.
3. The Run A and Run B source values **differ** (proves dynamic).
4. The Run A value appears in a **later** Run A request.
5. The Run B value appears in the aligned later Run B request at the **equivalent** location.
6. The producer occurs **before** every consumer.
7. The producer response is **usable** (not an auth/server error).
8. The replacement is **token-aware** (whole value or a known wrapper such as `Bearer `).

Scoring is explainable (positive factors and penalties, no unexplained percentages) → **High / Medium / Low / Rejected**. Static/common values, booleans, nulls, short values, HTTP constants and never-consumed timestamps are excluded by default.

### Extractor selection

| Producer source            | Extractor                          |
|----------------------------|------------------------------------|
| JSON body                  | JSON Extractor (`JSONPostProcessor`, JSONPath) |
| XML body                   | `XPath2Extractor`                  |
| Response header / cookie   | `RegexExtractor` (escaped literals)|
| HTML / unstructured text   | `RegexExtractor` / `BoundaryExtractor` |
| Ordinary session cookies   | handled by the HTTP Cookie Manager |
| (advanced fallback)        | `JSR223PostProcessor` (Groovy)     |

Every extractor gets a sanitised unique variable name (`[A-Za-z_][A-Za-z0-9_]*`) and a visible default (`__NOT_FOUND__`) so extraction failures are obvious.

---

## Local setup

### Prerequisites
- Python 3.12+ (this repo was validated on 3.14; use the `py` launcher on Windows)
- Node.js 20+
- Java 8+ only if you want to *run* the generated JMX (JMeter 5.6.3)

### Backend
```bash
cd backend
py -m venv .venv                       # Windows;  python3 -m venv .venv elsewhere
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # Windows path
# source .venv/bin/activate && pip install -r requirements-dev.txt  # macOS/Linux
cp .env.example .env                   # optional; sensible defaults otherwise
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```
API docs: http://127.0.0.1:8000/api/docs

### Frontend
```bash
cd frontend
npm install
cp .env.local.example .env.local       # BACKEND_URL=http://127.0.0.1:8000
npm run dev                            # http://localhost:3000
```
The dev server proxies `/api/*` to the backend (see `next.config.mjs`).

### Running Postman collections (Docker Newman runner)

Collection execution is disabled by default (`POST /api/v1/execution-jobs` answers 503).
To enable it locally:

1. Start Docker Desktop (or the Docker daemon).
2. Build the pinned Newman 6.2.2 image once: `docker build -t baseline11/newman:6.2.2 docker/newman`
3. Start the backend with `B11_NEWMAN_RUNNER=docker`.
4. Open the frontend and choose **Run a Postman collection**: pick the collection (and optional
   environment) → supply any missing variable values (secret-looking ones are hidden inputs and are
   cleared once the job starts) → choose the whole collection or one folder and review the target
   domains → **Run collection twice**. The progress page names the current stage; when both runs are
   analysed it opens the usual Run health page. Failures stay on the progress page with guidance
   and a Retry button (secret values must be re-entered).

Each job runs the collection twice (baseline, comparison) in fresh, short-lived containers:
read-only root filesystem, all Linux capabilities dropped, CPU/memory/process/file-size limits,
a 5-minute wall-clock limit per run, and only the per-run temporary workspace mounted.
Supplied variable values are written to a private environment file, never passed as
command-line arguments. The container's DNS is disabled; it can reach only the hostnames that
passed destination validation, pinned to the exact addresses that were checked.

Known limitations of the local worker: redirects are not followed; a script that builds a URL
from a raw IP address is not blocked at the network layer (production egress rules handle that);
a request whose host comes only from a script-set variable cannot be validated in advance and
fails the job.

Real-Docker tests are opt-in: `B11_RUN_DOCKER_TESTS=1 ./.venv/Scripts/python.exe -m pytest -m docker`.

### Sign-in (OIDC)

Locally the app runs without sign-in and the owner of a job/analysis is the client IP.
To require sign-in (always on in production), register an OIDC application for the UI
(Authorization Code + PKCE, redirect URI `<origin>/auth/callback`) and an API audience, then:

| Where | Setting |
|-------|---------|
| backend | `B11_AUTH_MODE=oidc`, `B11_OIDC_ISSUER=https://idp.example.com/`, `B11_OIDC_AUDIENCE=<api audience>` (optional `B11_OIDC_JWKS_URL`) |
| frontend (`.env.local` / build args) | `NEXT_PUBLIC_OIDC_AUTHORITY`, `NEXT_PUBLIC_OIDC_CLIENT_ID`, optional `NEXT_PUBLIC_OIDC_AUDIENCE` |

Every `/api/v1` call then needs a valid bearer token; jobs and analyses are private to the
token's subject (another user's resource answers 404).

### Production on AWS

`B11_JOB_BACKEND=aws` switches execution to **API → SQS → worker → one Fargate runner task per
Newman run**, with jobs in DynamoDB and job material/reports in KMS-encrypted S3. The runner
task has no AWS credentials (presigned URLs only) and runs in a subnet whose network ACL blocks
private and link-local ranges. Infrastructure, deployment steps and known limitations:
[infra/terraform/README.md](infra/terraform/README.md). Container images: `docker/api`
(API and worker), `docker/frontend`, `docker/newman-runner`.

In production (`B11_ENVIRONMENT=production`) the API and worker refuse to start unless
OIDC, explicit https CORS origins and every AWS setting are configured.

Local check of the whole AWS path (moto instead of AWS, real runner containers):
```bash
docker build -t baseline11/newman-runner:6.2.2 docker/newman-runner
docker compose -f docker/compose.aws-local.yml up -d
cd backend && B11_RUN_AWS_LOCAL_TESTS=1 ./.venv/Scripts/python.exe -m pytest -m aws_local
```

Job metrics (`Baseline11/Execution`: `JobsCreated`, `JobsCompleted` by outcome/error code,
`RunDurationSeconds`, `QueueWaitSeconds`) are written as CloudWatch Embedded Metric Format log
lines; `/health` is liveness and `/health/ready` checks DynamoDB, SQS and S3 reachability.

---

## Commands

| Task            | Command (from the relevant dir)                         |
|-----------------|---------------------------------------------------------|
| Backend tests   | `backend> pytest -q`                                    |
| Backend lint    | `backend> ruff check app`                               |
| Backend types   | `backend> mypy app`                                     |
| Frontend build  | `frontend> npm run build`                               |
| Frontend tests  | `frontend> npm test` (Vitest + Testing Library)         |
| JMeter provision| `scripts/provision_jmeter.sh` / `.ps1` (checksum-verified) |
| JMeter smoke    | `python scripts/smoke_test.py --jmeter <path>/bin/jmeter` |

---

## Capturing Newman reports

```bash
npm install -g newman
# Run A (baseline)
newman run collection.json -e env.json --reporters json --reporter-json-export baseline.json
# Run B (comparison) — fresh login / new session data
newman run collection.json -e env.json --reporters json --reporter-json-export comparison.json
```
Upload `baseline.json` and `comparison.json`. The runtime responses under `run.executions[]` are authoritative — collection example responses under `item.response[]` are ignored.

For a safe local demonstration, start `scripts/mock_api.py` on port 8087, then run
`samples/local-login-flow.postman_collection.json` through Newman twice. The
resulting reports are already included as `samples/local-login-baseline.json`
and `samples/local-login-comparison.json`; upload those two files directly to
see the token extractor, request substitution, and dependency graph. The mock
API runs only on localhost and both recorded requests return 200.

Supported response body shapes: `stream = {type:"Buffer", data:[...]}`, raw int arrays, string streams, `body` strings, and empty bodies. Byte arrays decode strictly as UTF-8 (with a recorded warning + safe fallback on invalid bytes).

---

## Seven-request webhook demo

Upload `samples/webhook-baseline.json` and `samples/webhook-comparison.json` for
a successful seven-request journey with three correlation variables and seven
dependency edges. These are real Newman reports from the local demo API.

To execute the generated JMX, keep the demo API running in a terminal:

```powershell
.\backend\.venv\Scripts\python.exe scripts/webhook_demo.py --port 8088
```

To reproduce the entire Newman → upload → correlation → JMeter verification
(with port 8088 free), run:

```powershell
.\backend\.venv\Scripts\python.exe scripts/verify_webhook_flow.py
```

The demo changes GUIDs and webhook IDs between journeys and changes merchant
array order. It checks that the update request receives a numeric webhook ID.
Results are saved in `samples/webhook-validation.json`.

## Opening / running the generated JMX

1. Launch JMeter 5.6.3 GUI and **File → Open** the downloaded `<collection>-correlated-<timestamp>.jmx`, or run headless:
   ```bash
   jmeter -n -t plan.jmx -l results.jtl
   ```
2. Review the **Thread Group** (defaults 1 user / 1 iteration), **HTTP Cookie Manager**, optional **Cache Manager**, and **User Defined Variables** (e.g. `BASE_HOST`, `BASE_PROTOCOL`).
3. Externalised secrets appear as `${SECRET_*}` UDVs with `__SET_ME__` placeholders — set real values there, or re-generate with "embed static secrets" (opt-in) if you accept embedding captured values.
4. Producer samplers carry the extractors; consumers reference `${variable}`. A Debug Sampler or the View Results Tree confirms propagation.

`scripts/smoke_test.py` proves this automatically: it runs a generated plan against `scripts/mock_api.py`, where `/me` returns `200` **only** if the token extracted from `/auth/login` was correctly propagated.

---

## Security & data handling

- Strict file-size (25 MiB default) and JSON complexity bounds (depth/array/scalar/total).
- No script execution, no XML parsing of untrusted bodies without hardening (`defusedxml`), **no outbound requests** during parsing/analysis/generation.
- Secrets (Authorization, API keys, tokens, GUIDs, sensitive cookies) are **masked in the UI** and **redacted from logs**; logs carry request ids, stages, counts, durations and error codes — never bodies or credentials.
- Analysis state is **ephemeral** (30-min TTL, in-memory, cryptographically-random ids) and behind a `SessionStore` interface a Redis implementation can replace. `DELETE /api/v1/analyses/{id}` removes it immediately.
- RFC 9457 problem+json errors with stable machine-readable codes; no tracebacks in production responses. Security headers + configurable CORS (no wildcard in production).

---

## Known limitations & next steps

- HTML/text producer extraction targets `<input name=… value=…>` and is intentionally lower-confidence; complex HTML may need a manual boundary/regex rule.
- Alignment resolves ambiguous mappings conservatively; a manual ambiguous-mapping resolver UI is a natural next step.
- The session store is in-memory for the MVP; wire the Redis implementation for multi-worker deployments.
- JSON export supports JSONPath. JMESPath rules are rejected during generation;
  they are not silently converted to JSONPath.
- Multipart file uploads carry metadata only (no captured file bytes are embedded).

---

## API surface (v1)

`POST /analyses` · `GET /analyses/{id}` · `DELETE /analyses/{id}` ·
`GET /analyses/{id}/executions` · `GET …/executions/{execId}` · `GET …/executions/{execId}/occurrences` ·
`GET /analyses/{id}/candidates` · `POST …/candidates/{cid}/accept` · `…/accept-high` · `…/{cid}/reject` ·
`GET /analyses/{id}/graph` (dependency graph: nodes + producer→consumer edges) ·
`POST /analyses/{id}/rules` · `PATCH …/rules/{ruleId}` · `DELETE …/rules/{ruleId}` · `POST …/rules/{ruleId}/validate` ·
`POST /analyses/{id}/preview` · `POST /analyses/{id}/generate` ·
`GET /analyses/{id}/download/jmx` · `GET /analyses/{id}/download/manifest`

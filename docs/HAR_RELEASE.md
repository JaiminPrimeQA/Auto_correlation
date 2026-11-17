# HAR conversion release — 6 November 2026

The release includes the frontend, backend, tests, local conversion tools, Dockerfiles,
and the existing AWS Terraform deployment configuration. It is a source package;
dependencies and JMeter are installed separately. No deployment was performed.

## What was wrong

The supplied `latest_one.jmx` already had login and merchant correlations, but also
extracted a menu `icon` and substituted it into fixed `/api/dashboard/...` routes.
Matching a response label to a route word is not evidence of a dynamic identifier.
The working tree already rejected this in auto-correlation; the candidate-discovery
path still suggested it. Both single-recording paths now reject this false match.
A regression test reproduced the candidate failure before the fix.

## Results for the supplied recording

- 357 recorded requests: 314 removed, 12 optional and excluded, 31 selected.
- 4 extractors: `merchantGuid`, `token`, `tokenGuid`, `url`.
- 56 downstream substitutions. Dashboard paths remain literal.
- Login password is supplied through the `Code` user variable/JMeter property.
- The cleaned HAR retains 35 entries: 31 selected requests and 4 redirect hops
  needed to preserve response provenance. Session pages remain because APIs may
  depend on their cookies. Images, scripts, styles and tracking calls are removed.
- The cleaned HAR retains recorded request/response data, including credentials;
  it is a private artifact and is intentionally excluded from the shareable ZIP.

## Start locally

Extract the ZIP. In its root, using Python 3.12+ and Node.js 22+:

```powershell
py -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
cd frontend
npm ci
npm run build
```

In a separate terminal, from the extracted `backend` folder:

```powershell
$env:B11_REQUIRE_ACCOUNT = "false"
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In the frontend terminal run `npm start`, then open http://localhost:3000.
Choose the browser-recording/HAR flow, upload a HAR (extensionless files work),
review the selection, convert, generate, and download. The existing JSON/Newman
API flow remains available. HAR conversion itself requires neither Docker nor Java.

## Use the corrected JMX

Open `download_1_verified.jmx` in JMeter 5.6.3. Set `Code` in User Defined Variables
to a valid password. The default username comes from the recording; override it
with the `Username` JMeter property when using another account.

Alternatively, from the project root after provisioning JMeter:

```powershell
.jmeter/apache-jmeter-5.6.3/bin/jmeter.bat -n -t reports/download_1_verified.jmx -JUsername=YOUR_TEST_USER -JCode=YOUR_TEST_PASSWORD -l results.jtl
```

The final recorded navigation reaches `/MFALanding.aspx`. Captcha/MFA and real
account permissions require application-specific handling. A single capture can
show value reuse but cannot prove that every reused value changes between sessions.
A second successful recording improves that evidence. Do not label a real-site
run validated until its own JMeter results pass.

## Reproduce conversion and verification

```powershell
backend/.venv/Scripts/python.exe scripts/convert_har.py "PATH_TO_RECORDING" --output reports/converted.jmx
backend/.venv/Scripts/python.exe scripts/verify_har_local.py "PATH_TO_RECORDING" reports/converted.jmx
```

The verification helper is for this JSON-body correlation recording: it supplies
the `Code` test property, changes response values, and redirects all samplers to
localhost. It checks request order, paths, and fresh values in consuming requests.
It flattens recorded redirects to their final responses; dedicated integration
tests cover real redirect handling separately. It does not authenticate to the
recorded site or establish live-target acceptance.

## Verification performed

| Check | Result |
|---|---|
| Original extensionless file through inspect/upload/correlate/generate/download API | Passed; output equals corrected artifact |
| Full corrected recording in JMeter 5.6.3 against localhost with changed values | 31/31 samplers succeeded; all 4 variables extracted; 0 assertion failures |
| Backend regression suite excluding slow/Docker/AWS tests | 702 passed, 3 skipped, 7 deselected |
| HAR API plus real JMeter tests | 37 passed (includes 4 JMeter tests) |
| Frontend tests | 230 passed across 21 files |
| Production frontend build/type validation | Passed |
| Backend Ruff and mypy | Passed; 88 source files type checked |
| Diff whitespace check | Passed |
| Production deployment / real recorded site | Not performed |

The two test warnings concern upstream Starlette/AnyIO deprecations. Coverage was
not measured. Local API tests needed execution outside the tool sandbox because
Windows asyncio's localhost socket pair blocked inside it.

## Deployment handoff

Use `docker/api/Dockerfile`, `docker/frontend/Dockerfile`, and
`infra/terraform/README.md` for the existing production deployment path. Docker
images and AWS infrastructure were not built/applied during this verification.
Configure the production OIDC provider, HTTPS origin, AWS resources and billing
settings required by `backend/app/core/production_guard.py`. Persist the accounts
database/history directory on writable storage; the API container runs as UID 10001.
Keep one API instance until analysis storage is shared. Set proxy request limits
to support the configured `B11_MAX_HAR_BYTES` (200 MiB per recording by default).
Use environment-specific secrets; the ZIP contains no local `.env`, database,
user recording, generated customer plan, virtualenv or node_modules.

Guidance used: [ECC verification loop](https://github.com/affaan-m/ecc/blob/main/skills/verification-loop/SKILL.md)
and [Karpathy guidelines](https://github.com/multica-ai/andrej-karpathy-skills/blob/main/CLAUDE.md).

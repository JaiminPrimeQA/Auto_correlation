# Baseline11 Auto-Correlate — How to Run and Test

A short, practical guide for anyone who wants to start the app on their own machine and check
that it works. No prior knowledge of the codebase is needed.

---

## 1. What the tool does (in one minute)

When you record an API flow and replay it in JMeter, some values change every time (login
tokens, session IDs, order IDs, GUIDs). JMeter must **capture** each value from one response
and **re-use** it in later requests. This is called *correlation*.

Baseline11 does this for you:

1. It gets **two runs** of the same Postman flow — either you upload two Newman JSON reports,
   or you upload a Postman collection and the tool runs it twice for you (inside Docker).
2. It compares both runs, finds which values were produced by one response and re-used by a
   later request, and proposes correlation rules.
3. It generates a **JMeter 5.6.3 test plan (`.jmx`)** with the extractors already in place, and
   can execute it with JMeter to prove it works.

---

## 2. What you need installed

| Tool | Version | Needed for |
|------|---------|-----------|
| Python | 3.12 or newer (Windows: use the `py` launcher) | Backend API |
| Node.js | 22 or newer (npm included) | Frontend web app |
| Docker Desktop | running | Only for "Run a Postman collection" |
| Java | 17 or newer | Only for "Validate with JMeter" |
| Git | any | Getting the code |

---

## 3. One-time setup

Open PowerShell and run:

```powershell
git clone https://github.com/JaiminPrimeQA/Auto_correlation.git
cd Auto_correlation

# Backend
cd backend
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
cd ..

# Frontend
cd frontend
npm install
cd ..

# Newman image for "Run a Postman collection" (Docker Desktop must be running)
docker build -t baseline11/newman:6.2.2 docker/newman
```

**JMeter (optional, for "Validate with JMeter").** The app looks for JMeter in
`.jmeter\apache-jmeter-5.6.3` inside the project. To download it there (from Git Bash):

```bash
bash scripts/provision_jmeter.sh .jmeter
```

If you already have JMeter 5.6.3 somewhere else, set `B11_JMETER_HOME` to that folder instead.

---

## 4. Start the app

You need **two terminals**, and both must stay open.

**Terminal 1 — Backend (API on port 8000)**

```powershell
cd Auto_correlation\backend
$env:B11_NEWMAN_RUNNER = "docker"      # enables "Run a Postman collection"; omit if no Docker
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — Frontend (web app on port 3000)**

```powershell
cd Auto_correlation\frontend
npm run dev
```

Open **http://localhost:3000** in your browser.

Quick check that the backend is alive: open http://127.0.0.1:8000/health — you should see
`{"status":"ok", ...}`.

> Sign-in is turned off for local use. You will go straight to the start page.

---

## 5. Manual test scenarios

Do these in order. Each one lists what to do and what you should see.

### Test A — Upload two Newman reports (no Docker needed)

1. On the start page choose **Upload Newman reports**.
2. Select both files from the `samples` folder:
   `webhook-baseline.json` and `webhook-comparison.json`.
3. Click **Analyze**.

**Expected**

- The **Run health** tab opens and shows that both runs are healthy.
- The **Candidates** tab lists correlation candidates (e.g. merchant GUID / merchant ID,
  webhook ID).
- Click **Auto-correlate all reused values**. The button then shows it has completed and
  cannot be clicked twice.
- The **Dependency graph** tab shows arrows from the request that *produces* a value to the
  requests that *use* it.
- The **Classification** tab explains the values that are *not* correlations (credentials,
  cookies, noise) and why.

### Test B — Generate the JMeter plan

1. Open the **Generate** tab.
2. Click **Preview Draft** to see the plan, then generate and download it.

**Expected:** a `.jmx` file downloads. The badge says *Generated JMX — structurally valid,
not yet executed*. Open it in JMeter: producer requests carry extractors, and later requests
use `${variableName}`.

### Test C — Validate with JMeter (needs Java + JMeter, see section 3)

The webhook sample needs its demo API running. Start it in a **third terminal**:

```powershell
cd Auto_correlation
.\backend\.venv\Scripts\python.exe scripts/webhook_demo.py --port 8088
```

Then, in the **Generate** tab, click **Validate with JMeter**.

**Expected:** a report shows every sampler, its success/failure, and which variables were
extracted. If everything passes the badge changes to **✓ Validated JMX**.

### Test D — Run a Postman collection twice (needs Docker + internet)

Use the ready-made **booking flow** in `samples/`. It calls the public practice API
Restful-Booker (`https://restful-booker.herokuapp.com`) and books, updates and deletes a
hotel booking.

| File | What it is |
|------|------------|
| `samples/booking-flow.postman_collection.json` | 7 requests: Login → Create booking → Get → Update (PUT) → Partial update (PATCH) → Search → Delete |
| `samples/booking-flow.postman_environment.json` | `baseUrl`, `username` = `admin`, `password` = `password123` (public demo login, marked secret), guest first/last name |

1. Go back to the start page and choose **Run a Postman collection**.
2. Pick the collection file and the environment file above.
3. **Variables step:** nothing is missing. `password` shows as a hidden (secret) value, and
   `token` / `bookingid` show as *set by a script* — they are created during the run.
4. **Scope and review:** keep the whole collection; the only target domain is
   `restful-booker.herokuapp.com`. Click **Run collection twice**.

**Expected, step by step**

| Where | You should see |
|-------|----------------|
| Progress page | validating → running baseline → running comparison → analysing → ready (about 30–60 s) |
| Run health | Both runs 7/7 successful, alignment 100 %, readiness **Ready** |
| Candidates | `token` (from Login, used in the `Cookie` of PUT/PATCH/DELETE) and `bookingid` (from Create booking, used in the URL of Get/PUT/PATCH/Delete) |
| Auto-correlate | Creates `token`, `bookingid` and three echoed values (`firstname`, `lastname`, `checkin`) |
| Generate | *Generated JMX*; PUT/PATCH/Delete carry a **Set client cookies (token)** step; the login body contains `"password": "${__P(password,)}"` — the real password is never written into the plan |
| Validate with JMeter | A **password** field appears: type `password123`, then validate. **✓ Validated JMX** — 7/7 samplers pass (Delete answers 201; that is normal for this API). Without the password, validation fails on purpose |

Why this is a good test: the login token and the booking id are **different on every run**,
so a plan that replays the recorded values fails, and only a correctly correlated plan passes.

**Alternative:** `frontend/e2e/fixtures/checkout-demo.postman_collection.json` with
`checkout-demo.postman_environment.json` (calls `postman-echo.com`; type any text for the
secret `api_key`).

### Test E — Things that must be blocked (safety checks)

| Try this | Expected result |
|----------|-----------------|
| Run `frontend/e2e/fixtures/blocked-destination.postman_collection.json` and enter `10.0.0.5` for `internal_host` | The run is refused because the target is a private address. Private/internal addresses are never called. |
| In Test D, leave a required variable empty | You cannot start the run until it is filled in. |
| Click **Run collection twice** several times quickly | Only **one** job starts. |
| After Test D, search the downloaded JMX for `password123` | It does not appear. Secret body fields (password, client_secret, api_key) and secret headers become `${__P(name,)}` properties. |
| Upload two runs where every request failed (e.g. all 401) | Run health says *not ready*; the tool does not claim a successful correlation. |

---

## 6. Automated tests

Run these before pushing changes. All should pass.

```powershell
# Backend: unit + integration tests, and lint
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .

# Frontend: type check and component tests
cd ..\frontend
npm run typecheck
npm test
```

Optional, slower suites:

| Suite | Command | Needs |
|-------|---------|-------|
| Real Newman containers | `$env:B11_RUN_DOCKER_TESTS="1"; .\.venv\Scripts\python.exe -m pytest -m docker` (in `backend`) | Docker |
| Browser end-to-end | `npx playwright install` once, then `npm run e2e` (in `frontend`, app running as in section 4) | Docker, JMeter, internet |

On every push, GitHub Actions (`.github/workflows/ci.yml`) runs the backend, frontend, Newman
runner, Docker images, Terraform checks and a JMeter smoke test.

---

## 7. Troubleshooting

| Problem | Fix |
|---------|-----|
| "Run a Postman collection" answers *503* / execution disabled | Start the backend with `B11_NEWMAN_RUNNER=docker` and make sure Docker Desktop is running. |
| Job fails immediately with a Docker/image error | Build the image: `docker build -t baseline11/newman:6.2.2 docker/newman`. |
| "JMeter is not available on this host" | Run `bash scripts/provision_jmeter.sh .jmeter` or set `B11_JMETER_HOME`. |
| Port 3000 or 8000 already in use | Another copy is running. Close it, or use another port (`uvicorn ... --port 8010`, and `.\node_modules\.bin\next dev -p 3010` with `$env:BACKEND_URL="http://127.0.0.1:8010"`). |
| `python` is not recognised | On Windows use `py` or the venv path `.\backend\.venv\Scripts\python.exe`. |
| My earlier analysis disappeared | Analyses are kept in memory for 30 minutes and are lost when the backend restarts. Upload again. |
| Collection that calls `localhost` / `127.0.0.1` fails | By design. The collection runs inside an isolated container and private addresses are blocked. Use a publicly reachable test API, or upload Newman reports you recorded yourself (Test A). |

---

## 8. Current limitations

- Production AWS deployment (Terraform, ECS/Fargate workers) is written and checked, but it
  has **never been applied** to a real AWS account.
- Sign-in (OIDC) was tested only with a mock identity provider, not with a real one
  (Cognito, Auth0, Entra ID).
- Analyses are kept in memory only (lost on restart, one backend process).
- HTML/text responses are correlated with lower confidence than JSON. Complex pages may need a
  manual rule in the **Add rule** tab.
- Redirects are not followed when a collection runs locally.

For the full design and acceptance evidence see
`docs/specs/2026-09-24-postman-collection-execution-design.md` and
`docs/acceptance/2026-09-25-postman-collection-execution.md`. The illustrated tester guide is
`reports/Baseline11_Auto_Correlation_Tester_Guide.docx`.

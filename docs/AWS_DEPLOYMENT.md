# Deploying Baseline11 on AWS Fargate

This is the deployment plan for the hosted product: what runs where, what the
code now does to stay up through restarts and deploys, and the steps to go
from an empty AWS account to production.

> **Status.** The application changes below are implemented and tested
> (backend: 750+ tests, including PostgreSQL 16 in a container; Terraform
> `validate` passes). The infrastructure has **not** been applied to a real
> AWS account yet — run it through staging (step 7) before production.

---

## 1. Architecture

```
Browser ──HTTPS──▶ ALB ─┬─ /api/*, /health* ─▶ API (Fargate, 2+ tasks, 2 AZs)
                        └─ everything else ──▶ Web UI (Fargate, 2 tasks)

API ──▶ PostgreSQL (RDS, Multi-AZ)   accounts, sessions, plans, free uses, history metadata
    ──▶ S3 app bucket (SSE-KMS)      analyses/ (signed snapshots)  history/ (saved files)
    ──▶ SQS ─▶ Worker (Fargate, 0..N, one job per task) ──RunTask──▶ Newman runner task
    ──▶ DynamoDB                     execution-job state
```

| Piece | Where its state lives | Survives task replacement? |
|-------|-----------------------|----------------------------|
| Analyses in progress (rules, candidates, generated JMX) | S3 `analyses/`, HMAC-signed, 30-min TTL | Yes |
| Accounts, sign-in sessions, plans, free-use counters | RDS PostgreSQL | Yes |
| Saved history files (paid plans) | S3 `history/` | Yes |
| Execution jobs | DynamoDB + S3 `jobs/` + SQS | Yes |

No API task keeps anything on its own disk, so the API runs as several
replicas and deploys with zero downtime.

## 2. What changed in the application (and why)

| Problem found | What the code does now | Where |
|---|---|---|
| Analyses lived only in one API process — a restart or redeploy lost them, and two replicas disagreed | `B11_SESSION_STORE=s3`: each analysis is a signed, compressed snapshot in S3. A middleware saves what a request changed **before** the response is sent; a failed request is never saved. Concurrent writes from two replicas fail with 409 instead of silently overwriting. | `repositories/persistent_analysis_store.py`, `api/persistence.py` |
| Accounts and history used SQLite and local files; the task's root disk is read-only | The account store runs on PostgreSQL (`B11_DATABASE_URL` or `B11_DATABASE_HOST/USER/PASSWORD`), with versioned migrations applied under an advisory lock. History files go to S3 (`B11_HISTORY_BACKEND=s3`). SQLite stays for local development. | `repositories/account_store.py`, `repositories/history_files.py`, `app/migrate.py` |
| CPU-heavy parsing ran inside `async` handlers and blocked the event loop | Those handlers now run in the threadpool, so a large upload no longer freezes other requests. | `api/v1/analyses.py`, `rules.py`, `downloads.py` |
| A lost worker failed the job; a long run could be redelivered to a second worker | The worker heartbeats: it extends the SQS visibility and stamps the job every 30 s, and holds **ECS task scale-in protection** while it runs. SIGTERM lets the current job finish. A redelivered message whose worker is still alive is handed back, not failed. | `services/job_liveness.py`, `worker.py` |
| A job could show "running" forever | The API fails a job whose worker stopped heartbeating (`worker_lost`, after 3 min) or that waited too long for a worker (`queue_timeout`, 15 min), with a clear message. Collections are **never replayed automatically** — they may create real orders or bookings. | `services/job_liveness.py`, `api/v1/execution_jobs.py` |
| OIDC sign-in was not connected to plans, free uses or history | Each OIDC subject gets its own app account on first sign-in (never merged into a password account by email). Password sign-up/sign-in are switched off in OIDC mode; "Sign out" ends the identity-provider session. | `api/deps.py`, `api/v1/accounts.py`, `AccountProvider.tsx` |
| Each API process ran its own maintenance loop (duplicate emails) | Maintenance runs in one replica at a time (PostgreSQL advisory lock). | `services/maintenance.py` |
| Terraform left billing on `demo`, which production refuses | Terraform sets `B11_BILLING_PROVIDER=disabled` for launch. | `infra/terraform/ecs.tf` |
| Production guard did not check storage | Production refuses to start without PostgreSQL, S3 analysis/history storage and a ≥ 32-character signing key. | `core/production_guard.py` |
| `/health/ready` checked only AWS job services | It also checks PostgreSQL and the analysis store; the ALB now routes only to ready tasks. | `main.py`, `alb.tf` |

### Terraform additions (`infra/terraform/persistence.tf` and edits)

- **RDS PostgreSQL 16** — encrypted with the app KMS key, TLS enforced, Multi-AZ by default, 7-day backups, deletion protection, RDS-managed master password in Secrets Manager.
- **App bucket** — private, SSE-KMS, TLS-only; lifecycle expires `analyses/` after 2 days and `history/` a week after the longest plan retention.
- **Secrets** — state signing key (generated), database password (RDS-managed), optional SMTP password; injected as ECS `secrets`, never plain environment.
- **API service** — `api_desired_count = 2`, 2 vCPU / 8 GiB (starting point; benchmark), rolling deploys with `minimumHealthyPercent = 100`, `maximumPercent = 200`, circuit breaker with automatic rollback, readiness health check.
- **Worker** — `ecs:UpdateTaskProtection` permission, 120 s stop timeout, database access.
- **Alarms** — jobs waiting over 10 min, API memory > 85 %, RDS CPU and free storage (in addition to the existing dead-letter, failed-job, 5xx and unhealthy-host alarms).

## 3. What you need before you start

- An AWS account, and deploy access through IAM Identity Center or an IAM role (no long-lived keys).
- A region close to your users and to the APIs your customers test (the default is `eu-west-1`).
- A domain or subdomain, e.g. `app.example.com`, and access to its DNS.
- An **ACM certificate** for that name **in the deployment region**.
- An **OIDC application** (Okta, Auth0, Entra ID, Cognito…) with redirect URI `https://app.example.com/auth/callback`; note the issuer URL, client id and API audience.
- An SMTP provider for account emails (Amazon SES works): host, user, password, from-address.
- Expected peak simultaneous jobs, largest file size you will accept, and a monthly budget.

Tools on the machine you deploy from: Terraform ≥ 1.6, AWS CLI v2, Docker.

## 4. Deployment steps

### Step 1 — Freeze a release
Merge the release branch, tag it (e.g. `v1.0.0`), and build every image from that tag.
Never ship real recordings or `.env` files: `reports/download_*`, `*.har` and `.env`
are git-ignored.

### Step 2 — Terraform state (once per account)
Create a versioned, encrypted S3 bucket for Terraform state (block public access).
Then uncomment the `backend "s3"` block in `infra/terraform/versions.tf`, with a
**different `key` for staging and production**, and run `terraform init`.

### Step 3 — Write the variables file
`infra/terraform/staging.tfvars` (git-ignored):

```hcl
environment         = "staging"
region              = "eu-west-1"
domain_name         = "staging.app.example.com"
acm_certificate_arn = "arn:aws:acm:eu-west-1:123456789012:certificate/..."
oidc_issuer         = "https://your-idp.example.com/"
oidc_audience       = "baseline11-api"
image_tag           = "v1.0.0"

# Staging can be cheaper; production keeps the defaults.
db_multi_az            = false
db_deletion_protection = false
single_nat_gateway     = true

smtp_host         = "email-smtp.eu-west-1.amazonaws.com"
smtp_username     = "AKIA..."
mail_from_address = "no-reply@example.com"
alarm_email       = "ops@example.com"
```

### Step 4 — Create the image repositories first
```bash
cd infra/terraform
terraform apply -var-file=staging.tfvars -target=aws_ecr_repository.repo
```

### Step 5 — Build and push the three images
```bash
REGISTRY=123456789012.dkr.ecr.eu-west-1.amazonaws.com
aws ecr get-login-password | docker login --username AWS --password-stdin $REGISTRY
docker build -f docker/api/Dockerfile -t $REGISTRY/b11-staging/api:v1.0.0 .
docker build -f docker/newman-runner/Dockerfile -t $REGISTRY/b11-staging/newman-runner:v1.0.0 .
docker build -f docker/frontend/Dockerfile \
  --build-arg NEXT_PUBLIC_OIDC_AUTHORITY=https://your-idp.example.com/ \
  --build-arg NEXT_PUBLIC_OIDC_CLIENT_ID=your-client-id \
  --build-arg NEXT_PUBLIC_OIDC_AUDIENCE=baseline11-api \
  -t $REGISTRY/b11-staging/frontend:v1.0.0 .
docker push ...   # all three
```
Use the repository names from `terraform output ecr_repositories`. Tags are immutable:
a new release always gets a new tag.

### Step 6 — Apply the rest
```bash
terraform plan  -var-file=staging.tfvars -out=staging.plan
terraform apply staging.plan
```
API tasks apply database migrations on start-up (under a lock). To run them as a
separate step before new tasks take traffic, start a one-off task from the API task
definition with the command `python -m app.migrate`.

If you set `smtp_host`, put the SMTP password in the created secret, then roll the
services so they pick it up:
```bash
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw smtp_password_secret_arn)" --secret-string '...'
aws ecs update-service --cluster b11-staging --service api --force-new-deployment
```

Point the DNS name at `terraform output load_balancer_dns_name` (alias/CNAME).

### Step 7 — Staging acceptance (all must pass)

| Test | Expected |
|------|----------|
| Sign in through OIDC → upload → auto-correlate → accept/reject candidates → generate → download JMX | Works end to end; accepted candidates leave the list |
| Upload your largest supported HAR and Newman files | Finishes in the agreed time; API memory alarm stays quiet |
| Several users run jobs at once | UI stays responsive; nobody sees another user's analysis or job |
| `aws ecs update-service --service api --force-new-deployment` **during** an analysis | Analysis, rules and results still there after the deploy |
| Stop a worker task mid-job (`aws ecs stop-task`) | Job ends `worker_lost` within ~3 min with a clear message; never "running" forever |
| Deploy a new image while a job runs | The protected worker finishes the job |
| Refresh / reopen the browser during a job | Same job and result come back |
| Restore RDS to a point in time into a new instance | Accounts and history are there |

### Step 8 — Production
Create `prod.tfvars` (defaults: Multi-AZ, deletion protection, one NAT per AZ with
`single_nat_gateway = false`), push the **same image tags that passed staging**, review
the plan, apply, connect DNS, and repeat the end-to-end check.

## 5. Day-2 operations

- **Release:** new tag → push images → `terraform apply -var image_tag=v1.0.1`. ECS starts new
  API tasks before stopping old ones and rolls back automatically if they fail health checks.
- **Rotate the state signing key:** update the secret and redeploy. Analyses in progress (≤ 30 min)
  become unreadable and users start them again — do it in a quiet hour.
- **Scale:** raise `api_desired_count` (API) or `worker_max_tasks` (concurrent jobs). Size API
  memory from the `api-memory` alarm and real upload sizes.
- **Watch:** the SNS alarm topic (dead letters, failed jobs, jobs waiting, 5xx, unhealthy hosts,
  API memory, RDS CPU/storage).

## 6. Known limits and decisions for launch

- **Payments:** only `demo` (never in production) and `disabled` exist. Launch with purchases
  off; a real payment provider is a separate piece of work.
- **Hosted JMeter validation** stays disabled in production. Users download the JMX and validate
  it in their own JMeter. Running it in the cloud needs its own isolated runner (like Newman's).
- **Private targets:** the runner subnet blocks private address ranges on purpose, so collections
  that call `localhost` or internal company APIs cannot run in the hosted service.
- **Analysis of finished jobs** still runs in the API (in the threadpool). If very large job
  reports load the API, move it to its own queue and worker.
- **Limits that look like interruptions:** Newman runs stop after 5 minutes
  (`B11_JOB_RUN_TIMEOUT_SECONDS`) and analyses expire after 30 minutes
  (`B11_SESSION_TTL_SECONDS`). Raise them deliberately if your users need more.
- **Cost:** besides Fargate, budget for the ALB, NAT gateway(s), RDS (Multi-AZ doubles it),
  S3, CloudWatch Logs and data transfer. A quote needs expected concurrency, file sizes and region.

## 7. Local checks for this configuration

```bash
# PostgreSQL-backed account store tests (needs Docker):
docker run -d --rm --name b11-test-pg -e POSTGRES_PASSWORD=b11test -e POSTGRES_DB=b11 -p 55432:5432 postgres:16-alpine
B11_TEST_DATABASE_URL=postgresql://postgres:b11test@localhost:55432/b11 python -m pytest tests/integration/test_account_store_databases.py

# Run the API locally with production-style storage (file-backed analyses, PostgreSQL accounts):
B11_SESSION_STORE=file B11_STATE_SIGNING_KEY=dev-signing-key-0123456789 \
B11_DATABASE_URL=postgresql://postgres:b11test@localhost:55432/b11 uvicorn app.main:app --reload
```

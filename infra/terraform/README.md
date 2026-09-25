# Baseline11 on AWS (Terraform)

Deploys the Postman-collection execution product as **API service + queue + isolated
ECS/Fargate workers** (spec §5.2). Nothing here has been applied to a real account
by the authors; `terraform validate` and a Trivy misconfiguration scan pass.

```
Internet ─HTTPS─▶ ALB ─┬─ /api/*, /health* ─▶ API (Fargate, 1 task) ──▶ DynamoDB, S3 (KMS), SQS
                       └─ everything else ──▶ Web UI (Fargate, 2 tasks)
SQS ─▶ Worker (Fargate, 0..N, one job per task) ──RunTask──▶ Newman runner task per run
                                                              (runner subnets, no task role)
```

## What it creates

| File | Resources |
|------|-----------|
| `network.tf` | VPC, public/private/runner subnets in 2 AZs, NAT, S3 gateway endpoint, **runner NACL** (denies 10/8, 172.16/12, 192.168/16, 100.64/10, 169.254/16, 127/8), security groups |
| `data.tf` | KMS key, private SSE-KMS bucket (TLS-only, 1-day expiry of `jobs/`), DynamoDB table (TTL, PITR, KMS), SQS queue + DLQ (KMS), ECR repos |
| `iam.tf` | Execution role; API role (table, bucket `jobs/*`, KMS, SendMessage); worker role (same data + queue consume, `RunTask` of the runner family in this cluster only, `PassRole` of the execution role only). **The runner has no task role.** |
| `ecs.tf` | Cluster, log groups, task definitions and services, worker autoscaling on queue depth |
| `alb.tf` | HTTPS listener (TLS 1.2/1.3), HTTP→HTTPS redirect, path routing |
| `monitoring.tf` | SNS alarm topic (own KMS key), alarms: dead-lettered jobs, failed jobs, API 5xx, API unhealthy |

## Security model (why it is built this way)

- Collections contain arbitrary JavaScript and call arbitrary hosts. Each Newman run is a fresh
  Fargate task with a read-only root filesystem, all Linux capabilities dropped, and **no AWS
  credentials** — it reads its input and uploads its report only through two presigned S3 URLs.
- The runner subnet NACL blocks private, CGNAT, loopback and link-local ranges, so a script (or a
  DNS answer rebound to a private address) cannot reach anything inside the VPC. NACLs do not
  filter the ECS credential endpoint — which is why the runner has no task role at all.
- Supplied variable values exist only inside KMS-encrypted S3 objects and the worker's memory;
  never in task overrides, environment variables, logs, metrics, queue messages or job status.
- The API refuses to start in production unless OIDC, explicit https CORS origins and every AWS
  setting are configured (`backend/app/core/production_guard.py`).

## Deploy

Prerequisites: an ACM certificate for your domain in `region`, an OIDC provider with an
application for the UI (Authorization Code + PKCE, redirect `https://<domain>/auth/callback`)
and an API audience, Docker, AWS credentials for the target account.

1. Configure a remote state backend in `versions.tf`.
2. Create the repositories first, then push images:
   ```bash
   terraform init
   terraform apply -target=aws_ecr_repository.repo \
     -var domain_name=b11.example.com -var acm_certificate_arn=arn:aws:acm:... \
     -var oidc_issuer=https://idp.example.com/ -var oidc_audience=baseline11-api -var image_tag=v1
   # from the repo root, for each output of `terraform output ecr_repositories`:
   docker build -f docker/api/Dockerfile -t <api-repo>:v1 .
   docker build -f docker/frontend/Dockerfile -t <frontend-repo>:v1 \
     --build-arg NEXT_PUBLIC_OIDC_AUTHORITY=https://idp.example.com \
     --build-arg NEXT_PUBLIC_OIDC_CLIENT_ID=<spa-client-id> \
     --build-arg NEXT_PUBLIC_OIDC_AUDIENCE=baseline11-api .
   docker build -t <newman-runner-repo>:v1 docker/newman-runner
   docker push ...   # after `aws ecr get-login-password | docker login ...`
   ```
3. `terraform apply` with the same variables (a `terraform.tfvars` file is easier).
4. Point `domain_name` at `load_balancer_dns_name`.

## Known limitations

- **One API task.** Analyses (and their rules/JMX state) live in the API process, as in the
  rest of the product. Scaling the API out requires moving analyses to a shared store first.
  A redeploy of the API drops open analyses (execution jobs themselves survive in DynamoDB).
- **DNS pinning.** Fargate cannot pin hosts like the local Docker runner (`--add-host`); DNS
  rebinding is contained by the runner NACL instead.
- **Dead-lettered jobs.** A job whose message fails delivery three times (repeated worker crashes)
  stays in its last state until it expires; the `jobs-dead-lettered` alarm fires.
- Newman does not follow redirects (`--ignore-redirects`).

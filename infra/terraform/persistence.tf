# Durable application state, so API tasks are stateless and can be replaced,
# redeployed and run as several replicas:
#   - PostgreSQL (RDS): accounts, sign-in sessions, plans, free-use counters,
#     history metadata. Master password generated and rotated by RDS in
#     Secrets Manager; tasks receive it as a secret, never as plain env.
#   - App bucket (S3, SSE-KMS): analyses/ (signed snapshots of analyses in
#     progress) and history/ (saved collection, JMX and manifest files).
#   - State signing key (Secrets Manager): authenticates analysis snapshots.

# --- PostgreSQL ---

resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "PostgreSQL: from the API and worker tasks only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_tasks" {
  for_each = {
    api    = aws_security_group.api.id
    worker = aws_security_group.worker.id
  }
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "tasks_to_db" {
  for_each = {
    api    = aws_security_group.api.id
    worker = aws_security_group.worker.id
  }
  security_group_id            = each.value
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_parameter_group" "main" {
  name   = "${local.name}-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier                          = "${local.name}-db"
  engine                              = "postgres"
  engine_version                      = var.db_engine_version
  instance_class                      = var.db_instance_class
  allocated_storage                   = var.db_allocated_storage
  max_allocated_storage               = var.db_allocated_storage * 4
  storage_type                        = "gp3"
  storage_encrypted                   = true
  kms_key_id                          = aws_kms_key.main.arn
  db_name                             = "baseline11"
  username                            = "baseline11"
  manage_master_user_password         = true
  master_user_secret_kms_key_id       = aws_kms_key.main.arn
  db_subnet_group_name                = aws_db_subnet_group.main.name
  vpc_security_group_ids              = [aws_security_group.db.id]
  parameter_group_name                = aws_db_parameter_group.main.name
  multi_az                            = var.db_multi_az
  publicly_accessible                 = false
  backup_retention_period             = var.db_backup_retention_days
  backup_window                       = "02:00-03:00"
  maintenance_window                  = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot               = true
  deletion_protection                 = var.db_deletion_protection
  skip_final_snapshot                 = !var.db_deletion_protection
  final_snapshot_identifier           = var.db_deletion_protection ? "${local.name}-db-final" : null
  auto_minor_version_upgrade          = true
  performance_insights_enabled        = true
  performance_insights_kms_key_id     = aws_kms_key.main.arn
  iam_database_authentication_enabled = false
  enabled_cloudwatch_logs_exports     = ["postgresql"]
}

# --- App bucket: analyses in progress and saved history ---

resource "aws_s3_bucket" "app" {
  bucket_prefix = "${local.name}-app-"
  force_destroy = false
}

resource "aws_s3_bucket_ownership_controls" "app" {
  bucket = aws_s3_bucket.app.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "app" {
  bucket                  = aws_s3_bucket.app.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "app" {
  bucket = aws_s3_bucket.app.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

# Analyses expire after B11_SESSION_TTL_SECONDS (checked on read); this is the
# clean-up. History is purged by the app at the end of each plan's retention;
# the lifecycle rule is the safety net beyond the longest retention (90 days).
resource "aws_s3_bucket_lifecycle_configuration" "app" {
  bucket = aws_s3_bucket.app.id
  rule {
    id     = "expire-analyses"
    status = "Enabled"
    filter {
      prefix = "analyses/"
    }
    expiration {
      days = 2
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
  rule {
    id     = "expire-history"
    status = "Enabled"
    filter {
      prefix = "history/"
    }
    expiration {
      days = var.history_max_retention_days + 7
    }
  }
}

data "aws_iam_policy_document" "app_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.app.arn, "${aws_s3_bucket.app.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "app" {
  bucket     = aws_s3_bucket.app.id
  policy     = data.aws_iam_policy_document.app_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.app]
}

# --- Secrets ---

resource "random_password" "state_signing_key" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "state_signing_key" {
  name_prefix = "${local.name}/state-signing-key-"
  description = "HMAC key authenticating stored analysis snapshots (B11_STATE_SIGNING_KEY)"
  kms_key_id  = aws_kms_key.main.arn
}

resource "aws_secretsmanager_secret_version" "state_signing_key" {
  secret_id     = aws_secretsmanager_secret.state_signing_key.id
  secret_string = random_password.state_signing_key.result
}

# Optional SMTP password, created empty-handed: put the value in with
#   aws secretsmanager put-secret-value --secret-id <arn> --secret-string '...'
resource "aws_secretsmanager_secret" "smtp_password" {
  count       = var.smtp_host == "" ? 0 : 1
  name_prefix = "${local.name}/smtp-password-"
  description = "SMTP password for account emails (B11_SMTP_PASSWORD)"
  kms_key_id  = aws_kms_key.main.arn
}

locals {
  db_secret_arn = aws_db_instance.main.master_user_secret[0].secret_arn

  # Task-role access to the app bucket (API: analyses and history).
  app_secret_arns = concat(
    [local.db_secret_arn, aws_secretsmanager_secret.state_signing_key.arn],
    aws_secretsmanager_secret.smtp_password[*].arn,
  )
}

# The execution role reads the secrets when it starts a task.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.app_secret_arns
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

data "aws_iam_policy_document" "api_app_data" {
  statement {
    sid       = "AppObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.app.arn}/analyses/*", "${aws_s3_bucket.app.arn}/history/*"]
  }
  statement {
    sid       = "ListAppObjects"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.app.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["analyses/*", "history/*"]
    }
  }
}

resource "aws_iam_role_policy" "api_app_data" {
  name   = "app-data"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_app_data.json
}

# The worker holds ECS scale-in protection while it runs a job.
data "aws_iam_policy_document" "worker_protection" {
  statement {
    actions   = ["ecs:UpdateTaskProtection", "ecs:GetTaskProtection"]
    resources = ["arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*"]
  }
}

resource "aws_iam_role_policy" "worker_protection" {
  name   = "task-protection"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker_protection.json
}

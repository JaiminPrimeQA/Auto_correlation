# Job state and material: KMS key, S3 bucket, DynamoDB table, SQS queues, ECR.

resource "aws_kms_key" "main" {
  description             = "${local.name}: job material, reports, job table, queue"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.main.key_id
}

# --- S3: per-job material and reports (private, encrypted, short-lived) ---

resource "aws_s3_bucket" "material" {
  bucket_prefix = "${local.name}-material-"
  force_destroy = false
}

resource "aws_s3_bucket_ownership_controls" "material" {
  bucket = aws_s3_bucket.material.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "material" {
  bucket                  = aws_s3_bucket.material.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Default SSE-KMS also covers the runner's presigned-URL uploads.
resource "aws_s3_bucket_server_side_encryption_configuration" "material" {
  bucket = aws_s3_bucket.material.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

# Workers delete a job's objects when it finishes; this removes anything a
# crashed task left behind (spec §8: delete job files on completion/expiry).
resource "aws_s3_bucket_lifecycle_configuration" "material" {
  bucket = aws_s3_bucket.material.id
  rule {
    id     = "expire-job-objects"
    status = "Enabled"
    filter {
      prefix = "jobs/"
    }
    expiration {
      days = 1
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "material_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.material.arn, "${aws_s3_bucket.material.arn}/*"]
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

resource "aws_s3_bucket_policy" "material" {
  bucket     = aws_s3_bucket.material.id
  policy     = data.aws_iam_policy_document.material_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.material]
}

# --- DynamoDB: jobs, idempotency records, per-user slot counters ---

resource "aws_dynamodb_table" "jobs" {
  name         = "${local.name}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.main.arn
  }
}

# --- SQS: job queue with a dead-letter queue ---

resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "${local.name}-jobs-dlq"
  message_retention_seconds = 14 * 24 * 3600
  kms_master_key_id         = aws_kms_key.main.arn
}

resource "aws_sqs_queue" "jobs" {
  name = "${local.name}-jobs"
  # Must cover two runs (5 min each + start-up) and handing over the reports;
  # matches B11_JOB_VISIBILITY_TIMEOUT_SECONDS.
  visibility_timeout_seconds = 20 * 60
  message_retention_seconds  = 24 * 3600
  receive_wait_time_seconds  = 20
  kms_master_key_id          = aws_kms_key.main.arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 3
  })
}

# --- ECR ---

resource "aws_ecr_repository" "repo" {
  for_each             = toset(["api", "frontend", "newman-runner"])
  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }
}

resource "aws_ecr_lifecycle_policy" "repo" {
  for_each   = aws_ecr_repository.repo
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 20 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

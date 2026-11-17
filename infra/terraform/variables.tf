variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "eu-west-1"
}

variable "environment" {
  description = "Deployment name used in resource names and tags (e.g. prod, staging)."
  type        = string
  default     = "prod"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.environment))
    error_message = "environment must be 2-16 lowercase letters, digits or dashes."
  }
}

variable "vpc_cidr" {
  description = "CIDR of the dedicated VPC. Must be inside 10.0.0.0/8 (the runner NACL relies on that)."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && startswith(var.vpc_cidr, "10.")
    error_message = "vpc_cidr must be a valid CIDR inside 10.0.0.0/8."
  }
}

variable "single_nat_gateway" {
  description = "One NAT gateway for all AZs (cheaper) instead of one per AZ (resilient)."
  type        = bool
  default     = true
}

variable "domain_name" {
  description = "Public host name the application is served on (used for CORS and OIDC redirects)."
  type        = string
}

variable "acm_certificate_arn" {
  description = "ACM certificate for domain_name in this region (HTTPS listener)."
  type        = string
}

variable "oidc_issuer" {
  description = "OIDC issuer URL; the API verifies bearer tokens against its JWKS."
  type        = string

  validation {
    condition     = startswith(var.oidc_issuer, "https://")
    error_message = "oidc_issuer must be an https URL."
  }
}

variable "oidc_audience" {
  description = "Expected 'aud' claim of access tokens for the API."
  type        = string
}

variable "image_tag" {
  description = "Tag of the api, frontend and newman-runner images pushed to the ECR repositories."
  type        = string
}

variable "worker_max_tasks" {
  description = "Upper bound on simultaneously running worker tasks (each runs one job, two Newman runs)."
  type        = number
  default     = 4
}

variable "max_jobs_per_user" {
  description = "Concurrent active execution jobs per user (spec §9 default: 2)."
  type        = number
  default     = 2
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for all services."
  type        = number
  default     = 30
}

variable "alarm_email" {
  description = "Optional e-mail subscribed to the alarm SNS topic (confirm the subscription e-mail)."
  type        = string
  default     = ""
}

# --- API sizing and availability ---

variable "api_desired_count" {
  description = "API tasks. Keep >= 2 so a deploy or an AZ failure leaves one serving."
  type        = number
  default     = 2

  validation {
    condition     = var.api_desired_count >= 1
    error_message = "api_desired_count must be at least 1."
  }
}

variable "api_cpu" {
  description = "API task CPU units (2048 = 2 vCPU). Benchmark with your largest uploads."
  type        = number
  default     = 2048
}

variable "api_memory" {
  description = "API task memory in MiB. Parsing large JSON expands it several times in memory."
  type        = number
  default     = 8192
}

# --- PostgreSQL ---

variable "db_engine_version" {
  description = "RDS PostgreSQL engine version (major 16)."
  type        = string
  default     = "16"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.small"
}

variable "db_allocated_storage" {
  description = "Initial storage in GiB (autoscaling up to 4x)."
  type        = number
  default     = 20
}

variable "db_multi_az" {
  description = "Standby in a second AZ. true for production; false is cheaper for staging."
  type        = bool
  default     = true
}

variable "db_backup_retention_days" {
  description = "Automated backup retention (point-in-time restore window)."
  type        = number
  default     = 7
}

variable "db_deletion_protection" {
  description = "Block deleting the database (and take a final snapshot). false only for throwaway staging."
  type        = bool
  default     = true
}

variable "history_max_retention_days" {
  description = "Longest plan retention (yearly plan: 90). The bucket lifecycle removes history a week after."
  type        = number
  default     = 90
}

# --- Email ---

variable "smtp_host" {
  description = "SMTP host for account emails. Empty: emails are only logged."
  type        = string
  default     = ""
}

variable "smtp_username" {
  description = "SMTP user name (the password goes into the created Secrets Manager secret)."
  type        = string
  default     = ""
}

variable "mail_from_address" {
  description = "From address of account emails."
  type        = string
  default     = ""
}

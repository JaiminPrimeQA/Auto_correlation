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

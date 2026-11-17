terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60, < 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6, < 4.0"
    }
  }

  # Remote state: keep staging and production apart (one key or workspace
  # each). See docs/AWS_DEPLOYMENT.md, step 2. Terraform >= 1.10 can lock with
  # S3 itself (use_lockfile); older versions need a DynamoDB lock table.
  # backend "s3" {
  #   bucket       = "my-terraform-state"
  #   key          = "baseline11/staging/terraform.tfstate"
  #   region       = "eu-west-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Application = "baseline11"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

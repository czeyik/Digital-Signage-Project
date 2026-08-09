terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # This backend is intentionally configured at init time with a distinct key:
  # recovery-smoke/<operation-id>.tfstate. Never initialize this root with the
  # production/terraform.tfstate key.
  backend "s3" {}
}

provider "aws" {
  # Do not inherit arbitrary shell credentials. The provider must use the
  # same fixed production SSO profile that the isolated backend and wrapper
  # verify before it can inspect or create recovery-only resources.
  profile             = local.recovery_state_profile
  region              = local.production_region
  allowed_account_ids = [local.production_account_id]

  default_tags {
    tags = {
      Project         = local.project_tag_value
      Environment     = "recovery"
      ManagedBy       = "terraform"
      Purpose         = "isolated-restore-smoke"
      OperationId     = var.operation_id
      Temporary       = "true"
      CleanupRequired = "true"
    }
  }
}

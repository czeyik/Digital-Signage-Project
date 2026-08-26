terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.0"
    }
  }

  backend "s3" {}
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = ["173454940059"]

  default_tags {
    tags = {
      Project     = "duducar-signage"
      Environment = "production"
      ManagedBy   = "terraform"
    }
  }
}

provider "awscc" {
  region = var.aws_region
}

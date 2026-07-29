variable "aws_region" {
  type        = string
  description = "Production AWS region. The pilot defaults to Malaysia."
  default     = "ap-southeast-5"
}

variable "project_name" {
  type    = string
  default = "duducar-signage-production"
}

variable "domain_name" {
  type    = string
  default = "duducaradmin.com"
}

variable "dashboard_hostname" {
  type    = string
  default = "marketing.duducaradmin.com"
}

variable "api_hostname" {
  type    = string
  default = "api.marketing.duducaradmin.com"
}

variable "container_image" {
  type        = string
  description = "Reviewed ARM64 backend ECR image for the isolated media worker. Pin by digest and keep the EC2 host release on the same version."
  default     = ""
}

variable "enable_services" {
  type        = bool
  description = "Deprecated outer compatibility gate for the retired ECS service runtime. It has no effect unless enable_legacy_ecs_runtime is also true; reviewed current-production examples keep both false."
  default     = false
}

variable "enable_legacy_ecs_runtime" {
  type        = bool
  description = "Historical state guard for retired ECS services and EventBridge schedules. Default true protects pre-migration state; current production explicitly sets false."
  default     = true
}

variable "enable_legacy_alb" {
  type        = bool
  description = "Historical state guard for the retired ALB, listeners, target group, and alarms. Default true protects pre-migration state; current production explicitly sets false."
  default     = true
}

variable "enable_legacy_rds" {
  type        = bool
  description = "Historical state guard for retired RDS and RDS-backed task definitions. Default true protects pre-migration state; current production explicitly sets false."
  default     = true
}

variable "legacy_rds_deletion_protection" {
  type        = bool
  description = "Historical RDS deletion gate. Default true is fail-safe for pre-migration state; false in current production records the completed decommission."
  default     = true
}

variable "legacy_rds_final_snapshot_identifier" {
  type        = string
  description = "Recorded final legacy RDS snapshot identifier. Never reuse it as authorization to delete or recreate a database."
  default     = ""

  validation {
    condition = (
      var.legacy_rds_final_snapshot_identifier == "" ||
      (
        length(var.legacy_rds_final_snapshot_identifier) <= 255 &&
        can(regex("^[A-Za-z][A-Za-z0-9-]*$", var.legacy_rds_final_snapshot_identifier)) &&
        !endswith(var.legacy_rds_final_snapshot_identifier, "-") &&
        !strcontains(var.legacy_rds_final_snapshot_identifier, "--")
      )
    )
    error_message = "legacy_rds_final_snapshot_identifier must be empty or a valid RDS snapshot identifier."
  }
}

variable "confirm_legacy_rds_final_snapshot" {
  type        = bool
  description = "Historical destructive confirmation required by the legacy RDS removal gate. In current production it records an already completed action."
  default     = false
}

variable "ecs_web_desired_count" {
  type        = number
  description = "Historical desired count for the retired ECS web service. Current EC2 production keeps this at zero."
  default     = 1

  validation {
    condition     = contains([0, 1], var.ecs_web_desired_count)
    error_message = "ecs_web_desired_count must be zero or one for the pilot."
  }
}

variable "enable_ecs_schedules" {
  type        = bool
  description = "Historical switch for retired EventBridge application schedules. Current schedules are local systemd timers, so production keeps this false."
  default     = true
}

variable "application_origin" {
  type        = string
  description = "Route 53 application origin. Current production is ec2; the alb option remains only for historical state compatibility."
  default     = "alb"

  validation {
    condition     = contains(["alb", "ec2"], var.application_origin)
    error_message = "application_origin must be alb or ec2."
  }
}

variable "enable_continuous_media_worker" {
  type        = bool
  description = "Historical polling-worker switch. Current production always keeps false and dispatches one isolated Fargate task per media asset."
  default     = false
}

variable "enable_container_insights" {
  type        = bool
  description = "Paid ECS Container Insights switch. Current USD 30 production keeps this false; use standard metrics and host diagnostics."
  default     = false
}

variable "required_app_version" {
  type    = string
  default = "0.1.0"
}

variable "play_integrity_project_number" {
  type        = string
  description = "Non-secret Google Cloud numeric project number."
  default     = ""
}

variable "smtp_host" {
  type        = string
  description = "Non-secret company SMTP hostname."
  default     = ""
}

variable "smtp_port" {
  type    = number
  default = 587
}

variable "default_from_email" {
  type    = string
  default = "no-reply@duducar.co"
}

variable "operations_email" {
  type        = string
  description = "Address receiving operational and budget notifications."
}

variable "monthly_budget_usd" {
  type        = number
  description = "Existing account-wide/shared-workload guard in USD. This is intentionally separate from the authoritative project-tagged USD 30 budget."
  default     = 115
}

variable "media_processing_lease_seconds" {
  type        = number
  description = "How long one isolated media task owns an asset before reconciliation may retry it."
  default     = 1800

  validation {
    condition     = var.media_processing_lease_seconds >= 300
    error_message = "media_processing_lease_seconds must be at least 300."
  }
}

variable "media_dispatch_retry_seconds" {
  type        = number
  description = "Minimum delay before retrying a failed media-task dispatch."
  default     = 600

  validation {
    condition     = var.media_dispatch_retry_seconds >= 60
    error_message = "media_dispatch_retry_seconds must be at least 60."
  }
}

variable "media_max_dispatch_attempts" {
  type        = number
  description = "Maximum isolated ECS dispatch attempts for one quarantined asset."
  default     = 5

  validation {
    condition     = var.media_max_dispatch_attempts >= 1
    error_message = "media_max_dispatch_attempts must be positive."
  }
}

variable "media_reconcile_max_assets" {
  type        = number
  description = "Maximum quarantined media assets inspected by one reconciliation run."
  default     = 25

  validation {
    condition     = var.media_reconcile_max_assets >= 1
    error_message = "media_reconcile_max_assets must be positive."
  }
}

variable "enable_ec2_target" {
  type        = bool
  description = "Manage the live EC2 Caddy/Django/local-PostgreSQL production host. The false default prevents accidental creation in an uninitialized workspace."
  default     = false
}

variable "ec2_target_instance_type" {
  type        = string
  description = "ARM64 instance type for the live USD 30 production host."
  default     = "t4g.small"

  validation {
    condition     = startswith(var.ec2_target_instance_type, "t4g.")
    error_message = "The production host must remain on an ARM64 t4g instance type."
  }
}

variable "ec2_target_termination_protection" {
  type        = bool
  description = "Protect the live EC2 host from API termination while it contains the production PostgreSQL database."
  default     = true
}

variable "enable_media_cloudfront" {
  type        = bool
  description = "Manage the live private signed-URL CloudFront delivery path for validated S3 media."
  default     = false
}

variable "enable_ec2_acme_bridge" {
  type        = bool
  description = "Retired migration-only ALB HTTP-01 bridge. Current production must keep this false."
  default     = true
}

variable "cloudfront_public_key_pem" {
  type        = string
  description = "PEM public key used by CloudFront trusted key groups. The matching private key must never enter Terraform."
  sensitive   = true
  default     = ""
}

variable "migration_budget_usd" {
  type        = number
  description = "Authoritative monthly Project=duducar-signage production budget in USD."
  default     = 30

  validation {
    condition     = var.migration_budget_usd > 0
    error_message = "migration_budget_usd must be positive."
  }
}

variable "migration_budget_forecast_thresholds" {
  type        = set(number)
  description = "Forecast percentages that send project-tagged USD 30 production budget notifications."
  default     = [80]
}

variable "migration_budget_actual_thresholds" {
  type        = set(number)
  description = "Actual percentages that send project-tagged USD 30 production budget notifications."
  default     = [60, 80, 90, 100]
}

variable "budget_project_tag_value" {
  type        = string
  description = "Activated Project cost-allocation tag value used by the migration budget."
  default     = "duducar-signage"
}

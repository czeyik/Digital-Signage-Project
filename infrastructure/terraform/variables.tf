variable "aws_region" {
  type        = string
  description = "Fixed production AWS region for the Malaysia pilot."
  default     = "ap-southeast-5"

  validation {
    condition     = var.aws_region == "ap-southeast-5"
    error_message = "Production infrastructure is fixed to ap-southeast-5."
  }
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

  validation {
    condition = (
      var.container_image == "" ||
      can(regex(
        "^[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}$",
        var.container_image,
      ))
    )
    error_message = "container_image must be empty before bootstrap or a SHA-256 digest-pinned image reference."
  }
}

variable "postgres_image" {
  type        = string
  description = "Exact digest-pinned PostgreSQL image used by the EC2 production stack."

  validation {
    condition = can(regex(
      "^[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}$",
      var.postgres_image,
    ))
    error_message = "postgres_image must be a SHA-256 digest-pinned image reference."
  }
}

variable "caddy_image" {
  type        = string
  description = "Exact digest-pinned Caddy image used by the EC2 production stack and runtime validation."

  validation {
    condition = can(regex(
      "^[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}$",
      var.caddy_image,
    ))
    error_message = "caddy_image must be a SHA-256 digest-pinned image reference."
  }
}

variable "enable_services" {
  type        = bool
  description = "Deprecated outer compatibility gate for the retired ECS service runtime. It has no effect unless enable_legacy_ecs_runtime is also true; reviewed current-production examples keep both false."
  default     = false
}

variable "enable_legacy_ecs_runtime" {
  type        = bool
  description = "Retired ECS services and EventBridge schedules. Current production must keep this false."
  default     = false

  validation {
    condition     = !var.enable_legacy_ecs_runtime
    error_message = "The retired ECS runtime cannot be re-enabled in current production."
  }
}

variable "enable_legacy_alb" {
  type        = bool
  description = "Retired ALB, listeners, target group, and alarms. Current production must keep this false."
  default     = false

  validation {
    condition     = !var.enable_legacy_alb
    error_message = "The retired ALB cannot be re-enabled in current production."
  }
}

variable "enable_legacy_rds" {
  type        = bool
  description = "Retired RDS and RDS-backed task definitions. Current production must keep this false."
  default     = false

  validation {
    condition     = !var.enable_legacy_rds
    error_message = "The retired RDS database cannot be re-enabled in current production."
  }
}

variable "legacy_rds_deletion_protection" {
  type        = bool
  description = "Historical RDS deletion state. Current production records the completed decommission as false."
  default     = false
}

variable "legacy_rds_final_snapshot_identifier" {
  type        = string
  description = "Recorded final legacy RDS snapshot identifier. Never reuse it as authorization to delete or recreate a database."

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
}

variable "ecs_web_desired_count" {
  type        = number
  description = "Historical desired count for the retired ECS web service. Current EC2 production keeps this at zero."
  default     = 0

  validation {
    condition     = var.ecs_web_desired_count == 0
    error_message = "The retired ECS web service must remain at zero."
  }
}

variable "enable_ecs_schedules" {
  type        = bool
  description = "Historical switch for retired EventBridge application schedules. Current schedules are local systemd timers, so production keeps this false."
  default     = false

  validation {
    condition     = !var.enable_ecs_schedules
    error_message = "Retired EventBridge application schedules cannot be re-enabled."
  }
}

variable "application_origin" {
  type        = string
  description = "Route 53 application origin. Current production is ec2; the alb option remains only for historical state compatibility."
  default     = "ec2"

  validation {
    condition     = var.application_origin == "ec2"
    error_message = "Current production DNS must remain on the EC2 origin."
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
  type        = string
  description = "Exact semantic Android version required by the backend. It must match the signed APK and the release-config SSM operation."
  default     = "0.1.0"

  validation {
    condition = can(regex(
      "^[0-9]+\\.[0-9]+\\.[0-9]+([-+][0-9A-Za-z.-]+)?$",
      var.required_app_version,
    ))
    error_message = "required_app_version must be an explicit semantic version such as 1.0.0 or 1.0.0-rc.1."
  }
}

variable "app_update_version_code" {
  type        = number
  description = "Version code advertised to enrolled device-owner players for the staged signed APK; zero disables OTA delivery."
  default     = 0

  validation {
    condition     = var.app_update_version_code >= 0 && var.app_update_version_code <= 2147483647 && floor(var.app_update_version_code) == var.app_update_version_code
    error_message = "app_update_version_code must be zero or a positive 32-bit integer."
  }
}

variable "app_update_version_name" {
  type        = string
  description = "Semantic version name of the staged signed APK; empty only when OTA is disabled."
  default     = ""

  validation {
    condition = (
      var.app_update_version_name == "" || can(regex(
        "^[0-9]+\\.[0-9]+\\.[0-9]+([-+][0-9A-Za-z.-]+)?$",
        var.app_update_version_name,
      ))
    )
    error_message = "app_update_version_name must be empty or an explicit semantic version."
  }
}

variable "app_update_storage_name" {
  type        = string
  description = "Private media-bucket key for the staged signed APK; empty only when OTA is disabled."
  default     = ""

  validation {
    condition = (
      var.app_update_storage_name == "" || can(regex(
        "^updates/[A-Za-z0-9._/-]+\\.apk$",
        var.app_update_storage_name,
      ))
    )
    error_message = "app_update_storage_name must be empty or an updates/*.apk key."
  }
}

variable "app_update_sha256" {
  type        = string
  description = "Lowercase SHA-256 checksum of the staged signed APK; empty only when OTA is disabled."
  default     = ""

  validation {
    condition = (
      var.app_update_sha256 == "" || can(regex(
        "^[0-9a-f]{64}$",
        var.app_update_sha256,
      ))
    )
    error_message = "app_update_sha256 must be empty or a lowercase 64-hex SHA-256 digest."
  }
}

variable "app_update_size_bytes" {
  type        = number
  description = "Exact byte size of the staged signed APK; zero only when OTA is disabled."
  default     = 0

  validation {
    condition     = var.app_update_size_bytes >= 0 && var.app_update_size_bytes <= 209715200 && floor(var.app_update_size_bytes) == var.app_update_size_bytes
    error_message = "app_update_size_bytes must be zero or an integer no larger than 200 MiB."
  }
}

variable "app_update_rollout_percent" {
  type        = number
  description = "Deterministic percentage of enrolled devices eligible for the staged APK; zero disables OTA delivery."
  default     = 0

  validation {
    condition     = var.app_update_rollout_percent >= 0 && var.app_update_rollout_percent <= 100 && floor(var.app_update_rollout_percent) == var.app_update_rollout_percent
    error_message = "app_update_rollout_percent must be an integer from zero through 100."
  }
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
  description = "Manage the live EC2 Caddy/Django/local-PostgreSQL production host."
  default     = true
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
  default     = true
}

variable "enable_ec2_acme_bridge" {
  type        = bool
  description = "Retired migration-only ALB HTTP-01 bridge. Current production must keep this false."
  default     = false

  validation {
    condition     = !var.enable_ec2_acme_bridge
    error_message = "The retired ALB ACME bridge cannot be re-enabled."
  }
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

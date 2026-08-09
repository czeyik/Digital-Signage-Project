variable "operation_id" {
  type        = string
  description = "Unique, recorded 32-character lowercase hexadecimal restore-smoke operation ID."

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.operation_id))
    error_message = "operation_id must be exactly 32 lowercase hexadecimal characters."
  }
}

variable "source_snapshot_id" {
  type        = string
  description = "Exact completed, encrypted DLM EBS snapshot selected during read-only preflight."

  validation {
    condition     = can(regex("^snap-[0-9a-f]+$", var.source_snapshot_id))
    error_message = "source_snapshot_id must be an EBS snapshot ID."
  }
}

variable "source_data_volume_id" {
  type        = string
  description = "Live source data-volume ID recorded for the selected snapshot; used as a fail-closed provenance check."

  validation {
    condition     = can(regex("^vol-[0-9a-f]+$", var.source_data_volume_id))
    error_message = "source_data_volume_id must be an EBS volume ID."
  }
}

variable "source_archive_key" {
  type        = string
  description = "Exact versioned logical PostgreSQL archive key under database-backups/."

  validation {
    condition     = can(regex("^database-backups/[A-Za-z0-9][A-Za-z0-9._/-]*\\.dump$", var.source_archive_key))
    error_message = "source_archive_key must name a database-backups/*.dump object."
  }
}

variable "source_archive_version_id" {
  type        = string
  description = "Exact S3 version ID of source_archive_key."

  validation {
    condition     = can(regex("^[A-Za-z0-9._~+/=-]+$", var.source_archive_version_id))
    error_message = "source_archive_version_id must contain only the expected opaque S3 version-ID characters."
  }
}

variable "source_sidecar_key" {
  type        = string
  description = "Exact SHA-256 sidecar key for source_archive_key."

  validation {
    condition     = can(regex("^database-backups/[A-Za-z0-9][A-Za-z0-9._/-]*\\.dump\\.sha256$", var.source_sidecar_key))
    error_message = "source_sidecar_key must name a database-backups/*.dump.sha256 object."
  }
}

variable "source_sidecar_version_id" {
  type        = string
  description = "Exact S3 version ID of source_sidecar_key."

  validation {
    condition     = can(regex("^[A-Za-z0-9._~+/=-]+$", var.source_sidecar_version_id))
    error_message = "source_sidecar_version_id must contain only the expected opaque S3 version-ID characters."
  }
}

variable "source_media_key" {
  type        = string
  description = "Exact normalized media key under validated/ that must map to the restored MediaAsset record."

  validation {
    condition     = can(regex("^validated/[A-Za-z0-9]([A-Za-z0-9._/-]*[A-Za-z0-9])?$", var.source_media_key))
    error_message = "source_media_key must name a normalized validated/* object."
  }
}

variable "source_media_version_id" {
  type        = string
  description = "Exact S3 version ID of source_media_key."

  validation {
    condition     = can(regex("^[A-Za-z0-9._~+/=-]+$", var.source_media_version_id))
    error_message = "source_media_version_id must contain only the expected opaque S3 version-ID characters."
  }
}

variable "source_media_sha256" {
  type        = string
  description = "Expected normalized-media SHA-256 recorded during the read-only preflight."

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.source_media_sha256))
    error_message = "source_media_sha256 must be a lowercase 64-character SHA-256 digest."
  }
}

variable "source_media_size_bytes" {
  type        = number
  description = "Expected normalized-media byte size recorded during the read-only preflight."

  validation {
    condition     = var.source_media_size_bytes >= 1 && var.source_media_size_bytes <= 52428800
    error_message = "source_media_size_bytes must be between 1 byte and the 50 MiB pilot media limit."
  }
}

variable "recovery_vpc_id" {
  type        = string
  description = "Existing production VPC ID. The temporary host never receives production ingress rules."

  validation {
    condition     = can(regex("^vpc-[0-9a-f]+$", var.recovery_vpc_id))
    error_message = "recovery_vpc_id must be a VPC ID."
  }
}

variable "recovery_subnet_id" {
  type        = string
  description = "Existing public subnet in the selected recovery AZ. It is used only with a zero-ingress security group and an ephemeral public IPv4 address for outbound HTTPS."

  validation {
    condition     = can(regex("^subnet-[0-9a-f]+$", var.recovery_subnet_id))
    error_message = "recovery_subnet_id must be a subnet ID."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR of recovery_vpc_id; used only to permit DNS queries to the VPC resolver."
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 2))
    error_message = "vpc_cidr must be a valid IPv4 CIDR."
  }
}

variable "recovery_ami_id" {
  type        = string
  description = "Reviewed ARM64 Amazon Linux AMI for the disposable recovery root host."

  validation {
    condition     = can(regex("^ami-[0-9a-f]+$", var.recovery_ami_id))
    error_message = "recovery_ami_id must be an AMI ID."
  }
}

variable "recovery_instance_type" {
  type        = string
  description = "Small ARM64 instance used only for the bounded restore smoke."
  default     = "t4g.small"

  validation {
    condition     = startswith(var.recovery_instance_type, "t4g.")
    error_message = "The recovery host must use an ARM64 t4g instance type."
  }
}

variable "recovery_root_volume_size_gib" {
  type        = number
  description = "Encrypted disposable root-volume size. It must leave room for Docker images outside the cloned data volume."
  default     = 16

  validation {
    condition     = var.recovery_root_volume_size_gib >= 16 && var.recovery_root_volume_size_gib <= 32
    error_message = "recovery_root_volume_size_gib must be between 16 and 32 GiB."
  }
}

variable "data_volume_kms_key_arn" {
  type        = string
  description = "KMS key ARN that encrypts the source EBS snapshot and the disposable root/data clone."

  validation {
    condition     = can(regex("^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/[0-9a-f-]+$", var.data_volume_kms_key_arn))
    error_message = "data_volume_kms_key_arn must be a KMS key ARN."
  }
}

variable "application_secret_arn" {
  type        = string
  description = "Existing production application secret ARN. The recovery role may read only this secret into root-only tmpfs files."

  validation {
    condition     = can(regex("^arn:[^:]+:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+$", var.application_secret_arn))
    error_message = "application_secret_arn must be a Secrets Manager secret ARN."
  }
}

variable "application_kms_key_arn" {
  type        = string
  description = "KMS key ARN for the application secret."

  validation {
    condition     = can(regex("^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/[0-9a-f-]+$", var.application_kms_key_arn))
    error_message = "application_kms_key_arn must be a KMS key ARN."
  }
}

variable "storage_kms_key_arn" {
  type        = string
  description = "KMS key ARN for the selected private S3 backups and media objects."

  validation {
    condition     = can(regex("^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/[0-9a-f-]+$", var.storage_kms_key_arn))
    error_message = "storage_kms_key_arn must be a KMS key ARN."
  }
}

variable "backup_bucket_name" {
  type        = string
  description = "Existing versioned private backup bucket; the recovery role can read only the selected archive and sidecar objects."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.backup_bucket_name))
    error_message = "backup_bucket_name must be a valid S3 bucket name."
  }
}

variable "media_bucket_name" {
  type        = string
  description = "Existing private media bucket; the recovery role can read only the selected normalized-media object."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.media_bucket_name))
    error_message = "media_bucket_name must be a valid S3 bucket name."
  }
}

variable "backend_image" {
  type        = string
  description = "Exact immutable backend image digest for the smoke; do not use a tag."

  validation {
    condition     = can(regex("^[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}$", var.backend_image))
    error_message = "backend_image must be pinned by a sha256 digest."
  }
}

variable "postgres_image" {
  type        = string
  description = "Exact immutable PostgreSQL image digest compatible with the snapshot."

  validation {
    condition     = can(regex("^[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}$", var.postgres_image))
    error_message = "postgres_image must be pinned by a sha256 digest."
  }
}

variable "caddy_image" {
  type        = string
  description = "Exact immutable Caddy image digest for recovery-only internal TLS."

  validation {
    condition     = can(regex("^[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}$", var.caddy_image))
    error_message = "caddy_image must be pinned by a sha256 digest."
  }
}

variable "required_app_version" {
  type        = string
  description = "Reviewed production required Android app version retained for application configuration parity."
  default     = "1.0.0"

  validation {
    condition     = can(regex("^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$", var.required_app_version))
    error_message = "required_app_version must be a short shell-safe version identifier."
  }
}

variable "play_integrity_project_number" {
  type        = string
  description = "Non-secret production Play Integrity numeric project number required by the production settings check."

  validation {
    condition     = can(regex("^[0-9]+$", var.play_integrity_project_number))
    error_message = "play_integrity_project_number must be numeric."
  }
}

variable "cloudfront_domain" {
  type        = string
  description = "Existing private CloudFront domain used only for settings parity; the recovery smoke does not preview media."

  validation {
    condition     = can(regex("^[A-Za-z0-9.-]+$", var.cloudfront_domain))
    error_message = "cloudfront_domain must be a hostname without a scheme or path."
  }
}

variable "cloudfront_public_key_id" {
  type        = string
  description = "Existing CloudFront public key ID associated with the secret signing key."

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]+$", var.cloudfront_public_key_id))
    error_message = "cloudfront_public_key_id must be a non-empty CloudFront key ID."
  }
}

variable "recovery_caddy_port" {
  type        = number
  description = "Loopback-only host and container port used for recovery Caddy internal TLS."
  default     = 8443

  validation {
    condition     = var.recovery_caddy_port >= 1024 && var.recovery_caddy_port <= 65535
    error_message = "recovery_caddy_port must be an unprivileged TCP port."
  }
}

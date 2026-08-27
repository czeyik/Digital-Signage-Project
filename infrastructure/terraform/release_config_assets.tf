# The EC2 user data is bootstrap-only. This separate, opt-in document updates
# the complete non-secret release selection. It never restarts services and keeps a per-operation backup
# on the host for an exact, guarded rollback.
locals {
  ec2_release_config_manager_sha256 = filesha256(
    "${path.module}/ec2/runtime/manage-release-config"
  )
}

resource "aws_ssm_document" "ec2_release_config" {
  count           = var.enable_ec2_target ? 1 : 0
  name            = "${local.name}-set-release-config"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Validate, install, or roll back the complete reviewed DUDU release selection"
    parameters = {
      Mode = {
        type          = "String"
        description   = "Validate is read-only; install and rollback replace the exact backend, PostgreSQL, Caddy, app-version, and Caddy-config selection without restarting services."
        default       = "validate"
        allowedValues = ["validate", "install", "rollback"]
      }
      ExpectedCommit = {
        type           = "String"
        description    = "Full reviewed Git commit recorded with the host-side backup."
        allowedPattern = "^[0-9a-f]{40}$"
      }
      OperationId = {
        type           = "String"
        description    = "Unique lowercase 32-hex release operation identifier; reuse it only to retry or roll back this exact configuration change."
        allowedPattern = "^[0-9a-f]{32}$"
      }
      BackendImage = {
        type           = "String"
        description    = "The exact Terraform-reviewed digest-pinned backend image."
        allowedPattern = "^[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}$"
        allowedValues  = [var.container_image]
      }
      RequiredAppVersion = {
        type           = "String"
        description    = "The exact Terraform-reviewed semantic Android version required by the server."
        allowedPattern = "^[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
        allowedValues  = [var.required_app_version]
      }
      PostgresImage = {
        type          = "String"
        description   = "Exact Terraform-reviewed digest-pinned PostgreSQL image."
        allowedValues = [var.postgres_image]
      }
      CaddyImage = {
        type          = "String"
        description   = "Exact Terraform-reviewed digest-pinned Caddy image."
        allowedValues = [var.caddy_image]
      }
      AppUpdateVersionCode = {
        type           = "String"
        description    = "Exact Terraform-reviewed staged APK version code; zero disables OTA delivery."
        default        = tostring(var.app_update_version_code)
        allowedPattern = "^(0|[1-9][0-9]*)$"
        allowedValues  = [tostring(var.app_update_version_code)]
      }
      AppUpdateVersionName = {
        type           = "String"
        description    = "Exact Terraform-reviewed staged APK version name."
        default        = var.app_update_version_name
        allowedPattern = "^$|^[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
        allowedValues  = [var.app_update_version_name]
      }
      AppUpdateStorageName = {
        type           = "String"
        description    = "Exact private media-bucket key for the staged APK."
        default        = var.app_update_storage_name
        allowedPattern = "^$|^updates/[A-Za-z0-9._/-]+\\.apk$"
        allowedValues  = [var.app_update_storage_name]
      }
      AppUpdateSha256 = {
        type           = "String"
        description    = "Exact lowercase SHA-256 digest of the staged APK."
        default        = var.app_update_sha256
        allowedPattern = "^$|^[0-9a-f]{64}$"
        allowedValues  = [var.app_update_sha256]
      }
      AppUpdateSizeBytes = {
        type           = "String"
        description    = "Exact byte size of the staged APK; zero disables OTA delivery."
        default        = tostring(var.app_update_size_bytes)
        allowedPattern = "^(0|[1-9][0-9]*)$"
        allowedValues  = [tostring(var.app_update_size_bytes)]
      }
      AppUpdateRolloutPercent = {
        type           = "String"
        description    = "Deterministic rollout percentage for the staged APK."
        default        = tostring(var.app_update_rollout_percent)
        allowedPattern = "^(0|[1-9][0-9]?)$|^100$"
        allowedValues  = [tostring(var.app_update_rollout_percent)]
      }
    }
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "validateInstallOrRollbackReleaseConfig"
        inputs = {
          timeoutSeconds = "300"
          runCommand = [
            "#!/bin/bash",
            "set -Eeuo pipefail",
            "umask 0077",
            "mode='{{ Mode }}'",
            "commit='{{ ExpectedCommit }}'",
            "operation_id='{{ OperationId }}'",
            "backend_image='{{ BackendImage }}'",
            "required_app_version='{{ RequiredAppVersion }}'",
            "postgres_image='{{ PostgresImage }}'",
            "caddy_image='{{ CaddyImage }}'",
            "app_update_version_code='{{ AppUpdateVersionCode }}'",
            "app_update_version_name='{{ AppUpdateVersionName }}'",
            "app_update_storage_name='{{ AppUpdateStorageName }}'",
            "app_update_sha256='{{ AppUpdateSha256 }}'",
            "app_update_size_bytes='{{ AppUpdateSizeBytes }}'",
            "app_update_rollout_percent='{{ AppUpdateRolloutPercent }}'",
            "expected_backend_image='${var.container_image}'",
            "expected_required_app_version='${var.required_app_version}'",
            "expected_postgres_image='${var.postgres_image}'",
            "expected_caddy_image='${var.caddy_image}'",
            "stage_dir=$(mktemp -d /var/tmp/duducar-release-config.XXXXXX)",
            "trap 'rm -rf -- \"$stage_dir\"' EXIT",
            "printf '%s' '${filebase64("${path.module}/ec2/runtime/manage-release-config")}' | base64 -d > \"$stage_dir/manage-release-config\"",
            "chmod 0500 \"$stage_dir/manage-release-config\"",
            "printf '%s  %s\\n' '${local.ec2_release_config_manager_sha256}' \"$stage_dir/manage-release-config\" | sha256sum -c -",
            "bash -n \"$stage_dir/manage-release-config\"",
            "\"$stage_dir/manage-release-config\" \"$mode\" \"$commit\" \"$operation_id\" \"$backend_image\" \"$required_app_version\" '${split("@sha256:", var.container_image)[0]}' \"$postgres_image\" \"$caddy_image\" \"$expected_backend_image\" \"$expected_required_app_version\" \"$expected_postgres_image\" \"$expected_caddy_image\" \"$app_update_version_code\" \"$app_update_version_name\" \"$app_update_storage_name\" \"$app_update_sha256\" \"$app_update_size_bytes\" \"$app_update_rollout_percent\" '${var.app_update_version_code}' '${var.app_update_version_name}' '${var.app_update_storage_name}' '${var.app_update_sha256}' '${var.app_update_size_bytes}' '${var.app_update_rollout_percent}'",
          ]
        }
      }
    ]
  })

  tags = {
    Name = "${local.name}-set-release-config"
  }
}

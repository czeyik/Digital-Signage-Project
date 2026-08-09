# The EC2 user data is bootstrap-only. This separate, opt-in document updates
# only the two non-secret release selections that must change together for an
# Android rollout. It never restarts services and keeps a per-operation backup
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
    description   = "Validate, install, or roll back the reviewed DUDU release image and app version"
    parameters = {
      Mode = {
        type          = "String"
        description   = "Validate is read-only; install and rollback replace only BACKEND_IMAGE and REQUIRED_APP_VERSION and never restart services."
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
            "expected_backend_image='${var.container_image}'",
            "expected_required_app_version='${var.required_app_version}'",
            "stage_dir=$(mktemp -d /var/tmp/duducar-release-config.XXXXXX)",
            "trap 'rm -rf -- \"$stage_dir\"' EXIT",
            "printf '%s' '${filebase64("${path.module}/ec2/runtime/manage-release-config")}' | base64 -d > \"$stage_dir/manage-release-config\"",
            "chmod 0500 \"$stage_dir/manage-release-config\"",
            "printf '%s  %s\\n' '${local.ec2_release_config_manager_sha256}' \"$stage_dir/manage-release-config\" | sha256sum -c -",
            "bash -n \"$stage_dir/manage-release-config\"",
            "\"$stage_dir/manage-release-config\" \"$mode\" \"$commit\" \"$operation_id\" \"$backend_image\" \"$required_app_version\" '${aws_ecr_repository.backend.repository_url}' \"$expected_backend_image\" \"$expected_required_app_version\"",
          ]
        }
      }
    ]
  })

  tags = {
    Name = "${local.name}-set-release-config"
  }
}

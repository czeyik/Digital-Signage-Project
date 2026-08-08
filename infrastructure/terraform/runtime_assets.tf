# EC2 user data is intentionally bootstrap-only. This command document provides
# an audited, opt-in path for updating the two non-secret runtime files that can
# change after provisioning without replacing the production host. Creating or
# updating the document does not execute it and does not restart production.
locals {
  ec2_runtime_caddy_sha256 = filesha256(
    "${path.module}/ec2/runtime/Caddyfile.post-cutover"
  )
  ec2_runtime_renderer_sha256 = filesha256(
    "${path.module}/ec2/runtime/render-runtime-env"
  )
  ec2_runtime_manager_sha256 = filesha256(
    "${path.module}/ec2/runtime/manage-runtime-assets"
  )
}

resource "aws_ssm_document" "ec2_runtime_assets" {
  count           = var.enable_ec2_target ? 1 : 0
  name            = "${local.name}-install-runtime-assets"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Validate or install reviewed DUDU Car EC2 runtime assets"
    parameters = {
      Mode = {
        type          = "String"
        description   = "Validate is read-only; install and rollback replace two runtime files but never restart services."
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
        description    = "Unique lowercase 32-hex release operation identifier; reuse it only to retry or roll back this exact installation."
        allowedPattern = "^[0-9a-f]{32}$"
      }
      CaddyImage = {
        type           = "String"
        description    = "Use current for the running pinned Caddy image, or supply an already-pulled digest-pinned candidate image. Rollback uses the image recorded in its backup."
        default        = "current"
        allowedPattern = "^(current|[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64})$"
      }
    }
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "validateOrInstallRuntimeAssets"
        inputs = {
          timeoutSeconds = "300"
          runCommand = [
            "#!/bin/bash",
            "set -Eeuo pipefail",
            "umask 0077",
            "mode='{{ Mode }}'",
            "commit='{{ ExpectedCommit }}'",
            "operation_id='{{ OperationId }}'",
            "caddy_image='{{ CaddyImage }}'",
            "stage_dir=$(mktemp -d /var/tmp/duducar-runtime-assets.XXXXXX)",
            "trap 'rm -rf -- \"$stage_dir\"' EXIT",
            "printf '%s' '${filebase64("${path.module}/ec2/runtime/Caddyfile.post-cutover")}' | base64 -d > \"$stage_dir/Caddyfile.post-cutover\"",
            "printf '%s' '${filebase64("${path.module}/ec2/runtime/render-runtime-env")}' | base64 -d > \"$stage_dir/render-runtime-env\"",
            "printf '%s' '${filebase64("${path.module}/ec2/runtime/manage-runtime-assets")}' | base64 -d > \"$stage_dir/manage-runtime-assets\"",
            "chmod 0600 \"$stage_dir/Caddyfile.post-cutover\" \"$stage_dir/render-runtime-env\"",
            "chmod 0500 \"$stage_dir/manage-runtime-assets\"",
            "printf '%s  %s\\n' '${local.ec2_runtime_caddy_sha256}' \"$stage_dir/Caddyfile.post-cutover\" '${local.ec2_runtime_renderer_sha256}' \"$stage_dir/render-runtime-env\" '${local.ec2_runtime_manager_sha256}' \"$stage_dir/manage-runtime-assets\" | sha256sum -c -",
            "bash -n \"$stage_dir/manage-runtime-assets\"",
            "if [ \"$mode\" != rollback ] && [ \"$caddy_image\" = current ]; then caddy_image=$(docker inspect duducar-caddy | jq -er '.[0].Config.Image'); fi",
            "\"$stage_dir/manage-runtime-assets\" \"$mode\" \"$commit\" \"$operation_id\" \"$stage_dir/Caddyfile.post-cutover\" \"$stage_dir/render-runtime-env\" '${local.ec2_runtime_caddy_sha256}' '${local.ec2_runtime_renderer_sha256}' \"$caddy_image\"",
          ]
        }
      }
    ]
  })

  tags = {
    Name = "${local.name}-install-runtime-assets"
  }
}

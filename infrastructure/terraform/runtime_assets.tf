# User data is bootstrap-only. This document is the audited, opt-in path for
# installing every runtime file on an existing production host. It validates
# and atomically installs the bundle, but never starts or restarts services.
locals {
  ec2_runtime_assets = {
    "Caddyfile.post-cutover" = "${path.module}/ec2/runtime/Caddyfile.post-cutover"
    "pg_hba.conf"            = "${path.module}/ec2/runtime/pg_hba.conf"
    "postgres-init-roles.sh" = "${path.module}/ec2/runtime/postgres-init-roles.sh"
    "postgres-runtime-grants.sql" = (
      "${path.module}/ec2/runtime/postgres-runtime-grants.sql"
    )
    "render-runtime-env"        = "${path.module}/ec2/runtime/render-runtime-env"
    "duducar-stack"             = "${path.module}/ec2/runtime/duducar-stack"
    "duducar-command"           = "${path.module}/ec2/runtime/duducar-command"
    "duducar-alert"             = "${path.module}/ec2/runtime/duducar-alert"
    "duducar-host-health"       = "${path.module}/ec2/runtime/duducar-host-health"
    "duducar-backup-verify"     = "${path.module}/ec2/runtime/duducar-backup-verify"
    "duducar-credential-broker" = "${path.module}/ec2/runtime/duducar-credential-broker"
    "duducar.service"           = "${path.module}/ec2/runtime/duducar.service"
    "duducar-credential-broker.service" = (
      "${path.module}/ec2/runtime/duducar-credential-broker.service"
    )
    "duducar-command@.service"      = "${path.module}/ec2/runtime/duducar-command@.service"
    "duducar-alert@.service"        = "${path.module}/ec2/runtime/duducar-alert@.service"
    "duducar-health.timer"          = "${path.module}/ec2/runtime/duducar-health.timer"
    "duducar-playlists.timer"       = "${path.module}/ec2/runtime/duducar-playlists.timer"
    "duducar-media-reconcile.timer" = "${path.module}/ec2/runtime/duducar-media-reconcile.timer"
    "duducar-retention.timer"       = "${path.module}/ec2/runtime/duducar-retention.timer"
    "duducar-backup.timer"          = "${path.module}/ec2/runtime/duducar-backup.timer"
  }
  ec2_runtime_asset_sha256 = {
    for name, source in local.ec2_runtime_assets : name => filesha256(source)
  }
  ec2_runtime_manifest = join("\n", [
    for name, digest in local.ec2_runtime_asset_sha256 : "${digest}  ${name}"
  ])
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
    description   = "Validate, install, or roll back the complete reviewed DUDU host runtime bundle without activation"
    parameters = {
      Mode = {
        type          = "String"
        description   = "Validate may cache the exact pinned Caddy image but changes no configs or services; install and rollback replace the complete runtime bundle without activation."
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
        description    = "Unique lowercase 32-hex operation identifier; reuse it only for this exact bundle."
        allowedPattern = "^[0-9a-f]{32}$"
      }
      CaddyImage = {
        type          = "String"
        description   = "Exact Terraform-reviewed digest-pinned Caddy image used for offline config validation."
        allowedValues = [var.caddy_image]
      }
    }
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "validateInstallOrRollbackRuntimeBundle"
        inputs = {
          timeoutSeconds = "600"
          runCommand = concat(
            [
              "#!/bin/bash",
              "set -Eeuo pipefail",
              "umask 0077",
              "mode='{{ Mode }}'",
              "commit='{{ ExpectedCommit }}'",
              "operation_id='{{ OperationId }}'",
              "caddy_image='{{ CaddyImage }}'",
              "stage_dir=$(mktemp -d /var/tmp/duducar-runtime-assets.XXXXXX)",
              "trap 'rm -rf -- \"$stage_dir\"' EXIT",
            ],
            [for name, source in local.ec2_runtime_assets :
              "printf '%s' '${base64gzip(file(source))}' | base64 -d | gzip -dc > \"$stage_dir/${name}\""
            ],
            [
              "cat > \"$stage_dir/MANIFEST\" <<'DUDUCAR_RUNTIME_MANIFEST'\n${local.ec2_runtime_manifest}\nDUDUCAR_RUNTIME_MANIFEST",
              "printf '%s' '${base64gzip(file("${path.module}/ec2/runtime/manage-runtime-assets"))}' | base64 -d | gzip -dc > \"$stage_dir/manage-runtime-assets\"",
              "chmod 0500 \"$stage_dir/manage-runtime-assets\"",
              "printf '%s  %s\\n' '${local.ec2_runtime_manager_sha256}' \"$stage_dir/manage-runtime-assets\" | sha256sum -c -",
              "bash -n \"$stage_dir/manage-runtime-assets\"",
              "\"$stage_dir/manage-runtime-assets\" \"$mode\" \"$commit\" \"$operation_id\" \"$stage_dir\" \"$caddy_image\"",
            ]
          )
        }
      }
    ]
  })

  tags = {
    Name = "${local.name}-install-runtime-assets"
  }
}

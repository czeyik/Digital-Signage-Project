locals {
  ec2_release_activation_sha256 = filesha256(
    "${path.module}/ec2/runtime/activate-release"
  )
}

resource "aws_ssm_document" "ec2_release_activation" {
  count           = var.enable_ec2_target ? 1 : 0
  name            = "${local.name}-activate-release"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Validate or explicitly activate one fully installed, Terraform-reviewed DUDU release"
    parameters = {
      Mode = {
        type          = "String"
        default       = "validate"
        allowedValues = ["validate", "arm-failed-existing", "activate"]
      }
      ExpectedCommit = {
        type           = "String"
        allowedPattern = "^[0-9a-f]{40}$"
      }
      OperationId = {
        type           = "String"
        allowedPattern = "^[0-9a-f]{32}$"
      }
      Confirmation = {
        type           = "String"
        default        = ""
        description    = "Leave empty for validation. existing and initial-empty require ACTIVATE; failed-existing requires ARM then RECOVER with its new and prior operation IDs."
        allowedPattern = "^(|ACTIVATE [0-9a-f]{32}|ARM [0-9a-f]{32} FROM [0-9a-f]{32}|RECOVER [0-9a-f]{32} FROM [0-9a-f]{32})$"
      }
      ActivationKind = {
        type          = "String"
        default       = "existing"
        description   = "Existing requires fresh remote logical-backup, snapshot, and host-health checks. failed-existing is a distinct recovery path for a fully stopped prior fail-closed attempt; it refreshes a stale backup with only the credential broker, PostgreSQL, and a private one-shot runner, leaves Caddy/web/timers/workers off, rechecks non-public host gates before restore and public HTTPS after it. initial-empty refuses any PostgreSQL data."
        allowedValues = ["existing", "failed-existing", "initial-empty"]
      }
      RecoveryFromOperationId = {
        type           = "String"
        default        = ""
        description    = "For failed-existing only: the 32-hex operation ID from retained prior-failure evidence."
        allowedPattern = "^(|[0-9a-f]{32})$"
      }
      FailedActivationCommandId = {
        type           = "String"
        default        = ""
        description    = "For failed-existing only: the SSM command UUID from retained prior-failure evidence; it is an audit correlation value."
        allowedPattern = "^(|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
      }
      BackendImage = {
        type          = "String"
        allowedValues = [var.container_image]
      }
      PostgresImage = {
        type          = "String"
        allowedValues = [var.postgres_image]
      }
      CaddyImage = {
        type          = "String"
        allowedValues = [var.caddy_image]
      }
      RequiredAppVersion = {
        type          = "String"
        allowedValues = [var.required_app_version]
      }
    }
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "validateOrActivateRelease"
        inputs = {
          timeoutSeconds = "3600"
          runCommand = [
            "#!/bin/bash",
            "set -Eeuo pipefail",
            "umask 0077",
            "stage_dir=$(mktemp -d /var/tmp/duducar-release-activation.XXXXXX)",
            "trap 'rm -rf -- \"$stage_dir\"' EXIT",
            "cat > \"$stage_dir/MANIFEST\" <<'DUDUCAR_RUNTIME_MANIFEST'\n${local.ec2_runtime_manifest}\nDUDUCAR_RUNTIME_MANIFEST",
            "printf '%s' '${base64gzip(file("${path.module}/ec2/runtime/manage-runtime-assets"))}' | base64 -d | gzip -dc > \"$stage_dir/manage-runtime-assets\"",
            "printf '%s' '${base64gzip(file("${path.module}/ec2/runtime/activate-release"))}' | base64 -d | gzip -dc > \"$stage_dir/activate-release\"",
            "chmod 0500 \"$stage_dir/manage-runtime-assets\" \"$stage_dir/activate-release\"",
            "printf '%s  %s\\n' '${local.ec2_runtime_manager_sha256}' \"$stage_dir/manage-runtime-assets\" '${local.ec2_release_activation_sha256}' \"$stage_dir/activate-release\" | sha256sum -c -",
            "bash -n \"$stage_dir/manage-runtime-assets\" \"$stage_dir/activate-release\"",
            "\"$stage_dir/activate-release\" '{{ Mode }}' '{{ ExpectedCommit }}' '{{ OperationId }}' '{{ Confirmation }}' '{{ ActivationKind }}' '{{ BackendImage }}' '{{ PostgresImage }}' '{{ CaddyImage }}' '{{ RequiredAppVersion }}' '{{ RecoveryFromOperationId }}' '{{ FailedActivationCommandId }}' \"$stage_dir\" \"$stage_dir/manage-runtime-assets\"",
          ]
        }
      }
    ]
  })

  tags = {
    Name = "${local.name}-activate-release"
  }
}

#!/bin/bash
set -euo pipefail

# Fast, dependency-free regression checks for the recovery helpers. They do
# not replace a real restore smoke, but make CI fail if a future edit removes a
# containment condition that cannot be exercised safely on a CI runner.
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

require_literal() {
  local literal=$1
  local file=$2
  if ! grep -Fq -- "$literal" "$file"; then
    echo "Missing required recovery guardrail in $file: $literal" >&2
    exit 1
  fi
}

reject_literal() {
  local literal=$1
  local file=$2
  if grep -Fq -- "$literal" "$file"; then
    echo "Forbidden production value in $file: $literal" >&2
    exit 1
  fi
}

bash -n \
  "$root_dir/recovery-terraform" \
  "$root_dir/test-recovery-media-query.sh" \
  "$root_dir/test-recovery-terraform-wrapper.sh" \
  "$root_dir/test-recovery-mount-journal-replay.sh" \
  "$root_dir/test-user-data-size.sh" \
  "$root_dir/runtime/duducar-recovery-mount" \
  "$root_dir/runtime/duducar-recovery-restore" \
  "$root_dir/runtime/duducar-recovery-stack" \
  "$root_dir/runtime/render-recovery-runtime-env"
"$root_dir/test-user-data-size.sh"
"$root_dir/test-recovery-terraform-wrapper.sh"
"$root_dir/test-recovery-mount-journal-replay.sh"
"$root_dir/test-recovery-media-query.sh"

# State must be isolated even if an operator's terminal has inherited Terraform
# state/workspace settings from a production command.
require_literal 'unset TF_DATA_DIR TF_WORKSPACE TF_CLI_ARGS' "$root_dir/recovery-terraform"
require_literal 'export TF_WORKSPACE=default' "$root_dir/recovery-terraform"
require_literal 'verify_backend' "$root_dir/recovery-terraform"
require_literal 'verify_default_workspace' "$root_dir/recovery-terraform"
require_literal 'recovery-smoke/${operation_id}.tfstate' "$root_dir/recovery-terraform"
require_literal 'profile             = local.recovery_state_profile' "$root_dir/versions.tf"
require_literal '-state|-state=*' "$root_dir/recovery-terraform"
require_literal '-lock=false' "$root_dir/recovery-terraform"
require_literal 'run_safe_apply' "$root_dir/recovery-terraform"
require_literal 'run_safe_destroy' "$root_dir/recovery-terraform"
require_literal 'Recovery operation does not accept a positional saved-plan path' "$root_dir/recovery-terraform"
require_literal 'verify_cleanup_account' "$root_dir/recovery-terraform"
require_literal 'verify_no_active_ec2_resources' "$root_dir/recovery-terraform"
reject_literal 'resourcegroupstaggingapi get-resources' "$root_dir/recovery-terraform"
require_literal 'get-role --role-name' "$root_dir/recovery-terraform"
require_literal 'get-instance-profile --instance-profile-name' "$root_dir/recovery-terraform"
require_literal 'recovery_asset_bundle' "$root_dir/main.tf"
require_literal 'recovery_assets_raw = local.recovery_asset_bundle' "$root_dir/main.tf"
require_literal 'install_bundle_asset' "$root_dir/recovery-bootstrap.sh.tftpl"
require_literal "<<'__DUDUCAR_RECOVERY_ASSET_BUNDLE_EOF_V1__'" "$root_dir/recovery-bootstrap.sh.tftpl"
reject_literal 'recovery_assets_b64' "$root_dir/recovery-bootstrap.sh.tftpl"
# AL2023 already provides AWS CLI v2 as awscli-2. A nonexistent package name
# must not make cloud-init terminate before it installs the recovery helpers.
reject_literal 'awscli2' "$root_dir/recovery-bootstrap.sh.tftpl"
require_literal 'command -v aws >/dev/null' "$root_dir/recovery-bootstrap.sh.tftpl"
require_literal 'aws --version >/dev/null' "$root_dir/recovery-bootstrap.sh.tftpl"

# A writable clone needs a prior read-only XFS inspection and a receipt tied to
# the exact mounted device. Every runtime entry point revalidates it.
require_literal 'mount -o ro,nouuid,norecovery,nodev,nosuid' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'xfs_repair -n "$device"' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'require_inspection_receipt' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'verify_mounted_clone' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'replay-journal)' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'REPLAY-JOURNAL $RECOVERY_OPERATION_ID' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'journal_replay_required' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'journal_replayed_clean' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'assert_docker_quarantined' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'assert_clone_unmounted_everywhere' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'rw,nouuid,nodev,nosuid,noexec,noatime' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'post_replay_xfs_log' "$root_dir/runtime/duducar-recovery-mount"
reject_literal 'xfs_repair -L' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'duducar-recovery-mount verify-mounted' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'duducar-recovery-mount verify-mounted' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'duducar-recovery-mount verify-mounted' "$root_dir/runtime/render-recovery-runtime-env"

# Docker must prove the local recovery data-root before a daemon starts; Caddy
# is loopback-only and application containers use a no-egress internal bridge.
require_literal 'ensure_recovery_docker_config' "$root_dir/runtime/duducar-recovery-stack"
require_literal '"data-root"' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'docker network create --internal' "$root_dir/runtime/duducar-recovery-stack"
require_literal '--publish "127.0.0.1:${RECOVERY_CADDY_PORT}:${RECOVERY_CADDY_PORT}"' "$root_dir/runtime/duducar-recovery-stack"
require_literal '--restart no' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'start_existing_container_if_stopped' "$root_dir/runtime/duducar-recovery-stack"

# The official pinned Caddy image marks its executable with
# cap_net_bind_service=ep. Retain exactly that capability so it can execute
# under no-new-privileges after dropping everything else; broader grants would
# weaken the isolated recovery stack without a listener requirement.
caddy_container_definition=$(awk '
  /^create_caddy\(\) \{/ { in_caddy = 1 }
  in_caddy { print }
  in_caddy && /^}/ { exit }
' "$root_dir/runtime/duducar-recovery-stack")
web_container_definition=$(awk '
  /^create_web\(\) \{/ { in_web = 1 }
  in_web { print }
  in_web && /^}/ { exit }
' "$root_dir/runtime/duducar-recovery-stack")
if ! printf '%s\n' "$caddy_container_definition" | grep -Fq -- '--network "$network"'; then
  echo "Recovery Caddy must stay on the isolated recovery bridge." >&2
  exit 1
fi
if ! printf '%s\n' "$caddy_container_definition" | grep -Fq -- '--publish "127.0.0.1:${RECOVERY_CADDY_PORT}:${RECOVERY_CADDY_PORT}"'; then
  echo "Recovery Caddy must publish only its loopback listener." >&2
  exit 1
fi
if ! printf '%s\n' "$caddy_container_definition" | grep -Eq '^[[:space:]]*--cap-drop ALL \\$'; then
  echo "Recovery Caddy must drop all Linux capabilities before its narrow exception." >&2
  exit 1
fi
if ! printf '%s\n' "$caddy_container_definition" | grep -Eq '^[[:space:]]*--cap-add NET_BIND_SERVICE \\$'; then
  echo "Recovery Caddy must retain NET_BIND_SERVICE for the pinned image executable." >&2
  exit 1
fi
if [ "$(printf '%s\n' "$caddy_container_definition" | grep -Fc -- '--cap-add')" -ne 1 ]; then
  echo "Recovery Caddy must retain exactly one Linux capability." >&2
  exit 1
fi
if ! printf '%s\n' "$caddy_container_definition" | grep -Eq '^[[:space:]]*--security-opt no-new-privileges:true \\$'; then
  echo "Recovery Caddy must retain no-new-privileges." >&2
  exit 1
fi
if printf '%s\n' "$caddy_container_definition" | grep -Fq -- '--privileged'; then
  echo "Recovery Caddy must never use privileged mode." >&2
  exit 1
fi
if printf '%s\n' "$caddy_container_definition" | grep -Fq -- '--network host'; then
  echo "Recovery Caddy must never use host networking." >&2
  exit 1
fi
if printf '%s\n' "$web_container_definition" | grep -Fq -- '--cap-add'; then
  echo "Recovery Django must not inherit Caddy's capability exception." >&2
  exit 1
fi
if ! printf '%s\n' "$web_container_definition" | grep -Eq '^[[:space:]]*--cap-drop ALL \\$'; then
  echo "Recovery Django must keep its full capability drop." >&2
  exit 1
fi
if ! printf '%s\n' "$web_container_definition" | grep -Eq '^[[:space:]]*--security-opt no-new-privileges:true \\$'; then
  echo "Recovery Django must keep no-new-privileges." >&2
  exit 1
fi

# The media proof must bind an exact S3 version to both preflight evidence and
# the record recovered from the clone; no production hostname may be used.
require_literal '--version-id "$SOURCE_MEDIA_VERSION_ID"' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'SOURCE_MEDIA_SHA256' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'SOURCE_MEDIA_SIZE_BYTES' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'python manage.py shell --no-imports -c' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'ReadOnlyExactArchiveVersion' "$root_dir/main.tf"
require_literal 'ReadOnlyExactArchiveSidecarVersion' "$root_dir/main.tf"
require_literal 'ReadOnlyExactNormalizedMediaVersion' "$root_dir/main.tf"
require_literal 's3:VersionId' "$root_dir/main.tf"
require_literal 'kms:EncryptionContext:aws:s3:arn' "$root_dir/main.tf"
reject_literal 'marketing.duducaradmin.com' "$root_dir/runtime/Caddyfile.recovery"
reject_literal 'api.marketing.duducaradmin.com' "$root_dir/runtime/Caddyfile.recovery"

echo "Recovery-smoke static guardrails passed."

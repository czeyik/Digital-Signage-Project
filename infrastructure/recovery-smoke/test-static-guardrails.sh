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
  "$root_dir/test-recovery-logical-restore.sh" \
  "$root_dir/test-recovery-terraform-wrapper.sh" \
  "$root_dir/test-recovery-mount-journal-replay.sh" \
  "$root_dir/test-recovery-loopback-proxy.sh" \
  "$root_dir/test-user-data-size.sh" \
  "$root_dir/runtime/duducar-recovery-mount" \
  "$root_dir/runtime/duducar-recovery-restore" \
  "$root_dir/runtime/duducar-recovery-stack" \
  "$root_dir/runtime/render-recovery-runtime-env"
"$root_dir/test-user-data-size.sh"
"$root_dir/test-recovery-terraform-wrapper.sh"
"$root_dir/test-recovery-mount-journal-replay.sh"
"$root_dir/test-recovery-media-query.sh"
"$root_dir/test-recovery-logical-restore.sh"
"$root_dir/test-recovery-loopback-proxy.sh"

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
require_literal 'dnf install -y docker jq openssl systemd xfsprogs' "$root_dir/recovery-bootstrap.sh.tftpl"
require_literal 'test -x /usr/lib/systemd/systemd-socket-proxyd' "$root_dir/recovery-bootstrap.sh.tftpl"
require_literal 'command -v ss >/dev/null' "$root_dir/recovery-bootstrap.sh.tftpl"
require_literal 'install_bundle_asset 9 /etc/systemd/system/duducar-recovery-loopback-proxy.socket 0644' "$root_dir/recovery-bootstrap.sh.tftpl"
require_literal 'install_bundle_asset 10 /etc/systemd/system/duducar-recovery-loopback-proxy.service 0644' "$root_dir/recovery-bootstrap.sh.tftpl"
require_literal 'systemd-analyze verify' "$root_dir/recovery-bootstrap.sh.tftpl"

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
require_literal 'Refusing recovery volume unmount because the recovery stack did not stop cleanly.' "$root_dir/runtime/duducar-recovery-mount"
reject_literal '/usr/local/sbin/duducar-recovery-stack stop || true' "$root_dir/runtime/duducar-recovery-mount"
reject_literal 'xfs_repair -L' "$root_dir/runtime/duducar-recovery-mount"
require_literal 'duducar-recovery-mount verify-mounted' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'duducar-recovery-mount verify-mounted' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'duducar-recovery-mount verify-mounted' "$root_dir/runtime/render-recovery-runtime-env"

# Docker must prove the local recovery data-root before a daemon starts. All
# containers remain on the no-egress internal bridge; a root-owned systemd
# socket proxy, rather than Docker port publishing, exposes Caddy on loopback.
require_literal 'ensure_recovery_docker_config' "$root_dir/runtime/duducar-recovery-stack"
require_literal '"data-root"' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'docker network create --internal' "$root_dir/runtime/duducar-recovery-stack"
require_literal '--restart no' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'start_existing_container_if_stopped' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'assert_caddy_isolation' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'loopback_proxy_listener_present' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'loopback_proxy_listener_present_any' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'stop_loopback_proxy' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'write_loopback_proxy_receipt' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'require_loopback_proxy_receipt' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'assert_recovery_unit_file "$loopback_proxy_socket_unit" socket || return 1' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'assert_recovery_unit_file "$loopback_proxy_service_unit" service || return 1' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'clear_loopback_proxy_receipt || return 1' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'ensure_loopback_proxy_units || return 1' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'assert_caddy_isolation || return 1' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'stop_loopback_proxy || return 1' "$root_dir/runtime/duducar-recovery-stack"
require_literal 'Cannot inspect Caddy ports.' "$root_dir/runtime/duducar-recovery-stack"
reject_literal 'stop_loopback_proxy || true' "$root_dir/runtime/duducar-recovery-stack"
reject_literal 'docker port duducar-recovery-caddy || true' "$root_dir/runtime/duducar-recovery-stack"

loopback_socket_unit="$root_dir/runtime/duducar-recovery-loopback-proxy.socket"
loopback_service_unit="$root_dir/runtime/duducar-recovery-loopback-proxy.service"
require_literal 'ListenStream=127.0.0.1:__RECOVERY_CADDY_PORT__' "$loopback_socket_unit"
require_literal 'Service=duducar-recovery-loopback-proxy.service' "$loopback_socket_unit"
require_literal 'ExecStart=/usr/lib/systemd/systemd-socket-proxyd --connections-max=16 172.31.0.10:__RECOVERY_CADDY_PORT__' "$loopback_service_unit"
require_literal 'DynamicUser=yes' "$loopback_service_unit"
require_literal 'NoNewPrivileges=yes' "$loopback_service_unit"
require_literal 'CapabilityBoundingSet=' "$loopback_service_unit"
require_literal 'RestrictAddressFamilies=AF_INET' "$loopback_service_unit"
require_literal 'IPAddressDeny=any' "$loopback_service_unit"
require_literal 'IPAddressAllow=172.31.0.10/32' "$loopback_service_unit"
if [ "$(grep -c '^ListenStream=' "$loopback_socket_unit")" -ne 1 ]; then
  echo "Recovery loopback proxy must define exactly one listener." >&2
  exit 1
fi
if grep -Eq '(^|[^0-9])0\.0\.0\.0|ListenStream=.*(::|\[::\])|^\[Install\]' "$loopback_socket_unit" "$loopback_service_unit"; then
  echo "Recovery loopback proxy must not bind broadly or install itself for boot." >&2
  exit 1
fi

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
owner_command_definition=$(awk '
  /^run_owner_command\(\) \{/ { in_owner = 1 }
  in_owner { print }
  in_owner && /^}/ { exit }
' "$root_dir/runtime/duducar-recovery-restore")
media_proof_definition=$(awk '
  /^verify_media\(\) \{/ { in_media = 1 }
  in_media { print }
  in_media && /^}/ { exit }
' "$root_dir/runtime/duducar-recovery-restore")
logical_restore_definition=$(awk '
  /^restore_logical\(\) \{/ { in_restore = 1 }
  in_restore { print }
  in_restore && /^}/ { exit }
' "$root_dir/runtime/duducar-recovery-restore")
if ! printf '%s\n' "$caddy_container_definition" | grep -Fq -- '--network "$network"'; then
  echo "Recovery Caddy must stay on the isolated recovery bridge." >&2
  exit 1
fi
if ! printf '%s\n' "$caddy_container_definition" | grep -Fq -- '--ip "$caddy_ip"'; then
  echo "Recovery Caddy must use its reviewed static internal address." >&2
  exit 1
fi
if [ "$(printf '%s\n' "$caddy_container_definition" | grep -Fc -- '--network')" -ne 1 ]; then
  echo "Recovery Caddy must not join a second Docker network." >&2
  exit 1
fi
if printf '%s\n' "$caddy_container_definition" | grep -Fq -- '--publish'; then
  echo "Recovery Caddy must not use Docker port publishing from an internal bridge." >&2
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

# Root inside a container is reserved for the two read-only pg_restore archive
# readers. Do not widen it to dashboard/migration, media, or Caddy helpers.
restore_helper="$root_dir/runtime/duducar-recovery-restore"
if [ "$(grep -Fc -- '--user 0:0' "$restore_helper")" -ne 2 ]; then
  echo "Recovery restore must contain exactly two root archive-reader identities." >&2
  exit 1
fi
if grep -Fq -- '--user root' "$restore_helper" "$root_dir/runtime/duducar-recovery-stack" || \
   printf '%s\n%s\n%s\n%s\n' "$owner_command_definition" "$media_proof_definition" "$web_container_definition" "$caddy_container_definition" | grep -Fq -- '--user 0:0'; then
  echo "Recovery root identity must not widen beyond the dedicated archive readers." >&2
  exit 1
fi

# The media proof must bind an exact S3 version to both preflight evidence and
# the record recovered from the clone; no production hostname may be used.
require_literal '--version-id "$SOURCE_MEDIA_VERSION_ID"' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'SOURCE_MEDIA_SHA256' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'SOURCE_MEDIA_SIZE_BYTES' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'python manage.py shell --no-imports -c' "$root_dir/runtime/duducar-recovery-restore"
# Exact logical backups stay root-only on the host. The dedicated behavioural
# check verifies the only two root-in-container readers remain capless and
# read-only, while normal Django helpers retain their image-configured user.
require_literal 'install -d -o root -g root -m 0700 "$archive_dir"' "$root_dir/runtime/duducar-recovery-restore"
require_literal 'chmod 0600 "$archive_path" "$sidecar_path"' "$root_dir/runtime/duducar-recovery-restore"
require_literal '--network none' "$root_dir/runtime/duducar-recovery-restore"
require_literal '--env AWS_EC2_METADATA_DISABLED=true' "$root_dir/runtime/duducar-recovery-restore"
if ! printf '%s\n' "$logical_restore_definition" | grep -Fq -- 'restore_owner_dir=$(mktemp -d "$runtime_dir/logical-restore.XXXXXX")'; then
  echo "Logical restore must create a dedicated tmpfs credential directory." >&2
  exit 1
fi
if ! printf '%s\n' "$logical_restore_definition" | grep -Eq '^[[:space:]]*trap .+ EXIT$' || \
   ! printf '%s\n' "$logical_restore_definition" | grep -Fq -- 'rm -rf -- "$restore_owner_dir"'; then
  echo "Logical restore must clean its temporary credential directory with an EXIT trap." >&2
  exit 1
fi
if ! printf '%s\n' "$logical_restore_definition" | grep -Fq -- 'install -o root -g root -m 0400' || \
   ! printf '%s\n' "$logical_restore_definition" | grep -Fq -- '"$restore_owner_dir/database-owner-password"'; then
  echo "Logical restore must create a root-only temporary owner-password copy." >&2
  exit 1
fi
if ! printf '%s\n' "$logical_restore_definition" | grep -Fq -- '--volume "$restore_owner_dir:/run/duducar-recovery/logical-restore:ro"'; then
  echo "Logical restore must mount only its temporary owner-password copy." >&2
  exit 1
fi
if printf '%s\n' "$logical_restore_definition" | grep -Fq -- '--volume "$runtime_dir/database-owner:'; then
  echo "Logical root restore must not mount the original appuser credential." >&2
  exit 1
fi
require_literal 'test-recovery-logical-restore.sh' "$root_dir/test-static-guardrails.sh"
require_literal 'ReadOnlyExactArchiveVersion' "$root_dir/main.tf"
require_literal 'ReadOnlyExactArchiveSidecarVersion' "$root_dir/main.tf"
require_literal 'ReadOnlyExactNormalizedMediaVersion' "$root_dir/main.tf"
require_literal 's3:VersionId' "$root_dir/main.tf"
require_literal 'kms:EncryptionContext:aws:s3:arn' "$root_dir/main.tf"
reject_literal 'marketing.duducaradmin.com' "$root_dir/runtime/Caddyfile.recovery"
reject_literal 'api.marketing.duducaradmin.com' "$root_dir/runtime/Caddyfile.recovery"

echo "Recovery-smoke static guardrails passed."

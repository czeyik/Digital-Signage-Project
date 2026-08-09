#!/bin/bash
set -euo pipefail
umask 0077

# EC2 accepts at most 16 KiB of raw user data (before Terraform base64-encodes
# it). Render the bootstrap with conservative non-secret sample inputs and
# verify both its shell syntax and its compressed payload budget. The bundle
# extraction check keeps the compact delivery format reviewable and fail-closed.
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
separator='__DUDUCAR_RECOVERY_ASSET_SEPARATOR_V1__'
work_dir=$(mktemp -d /tmp/duducar-recovery-user-data.XXXXXX)

cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

sources=(
  "$root_dir/../terraform/ec2/runtime/pg_hba.conf"
  "$root_dir/../terraform/ec2/runtime/postgres-init-roles.sh"
  "$root_dir/../terraform/ec2/runtime/postgres-runtime-grants.sql"
  "$root_dir/runtime/Caddyfile.recovery"
  "$root_dir/runtime/duducar-recovery-mount"
  "$root_dir/runtime/render-recovery-runtime-env"
  "$root_dir/runtime/duducar-recovery-stack"
  "$root_dir/runtime/duducar-recovery-restore"
)

if rg -Fq "$separator" "${sources[@]}"; then
  echo "Recovery runtime source contains the reserved bundle separator." >&2
  exit 1
fi

asset_bundle="$work_dir/assets"
cp "${sources[0]}" "$asset_bundle"
for source in "${sources[@]:1}"; do
  printf '\n%s\n' "$separator" >> "$asset_bundle"
  awk '1' "$source" >> "$asset_bundle"
done

asset_b64=$(gzip -n -c "$asset_bundle" | base64 -w0)
decoded_bundle="$work_dir/decoded-assets"
printf '%s' "$asset_b64" | base64 -d | gzip -d > "$decoded_bundle"
asset_count=$(awk -v separator="$separator" 'BEGIN { RS = "\n" separator "\n" } END { print NR }' "$decoded_bundle")
if [ "$asset_count" != "${#sources[@]}" ]; then
  echo "Unexpected recovery asset record count: $asset_count" >&2
  exit 1
fi
for index in "${!sources[@]}"; do
  extracted="$work_dir/asset-$index"
  record=$((index + 1))
  awk -v separator="$separator" -v record="$record" '
    BEGIN { RS = "\n" separator "\n" }
    NR == record { printf "%s", $0; found = 1; exit }
    END { if (!found) exit 1 }
  ' "$decoded_bundle" > "$extracted"
  if ! cmp -s "${sources[$index]}" "$extracted"; then
    echo "Recovery asset bundle did not reproduce ${sources[$index]}." >&2
    exit 1
  fi
done

rendered="$work_dir/recovery-bootstrap.sh"
cp "$root_dir/recovery-bootstrap.sh.tftpl" "$rendered"

b64() {
  printf '%s' "$1" | base64 -w0
}

replace_placeholder() {
  local name=$1
  local value=$2
  local placeholder
  placeholder="\${$name}"
  sed -i "s|${placeholder}|${value}|g" "$rendered"
}

for placeholder in \
  aws_region_b64 operation_id_b64 data_volume_id_b64 backend_image_b64 \
  postgres_image_b64 caddy_image_b64 application_secret_arn_b64 \
  media_bucket_name_b64 backup_bucket_name_b64 recovery_hostname_b64 \
  django_allowed_hosts_b64 django_csrf_trusted_origins_b64 \
  required_app_version_b64 play_integrity_project_number_b64 \
  cloudfront_domain_b64 cloudfront_public_key_id_b64 recovery_caddy_port_b64 \
  source_snapshot_id_b64 source_data_volume_id_b64 source_archive_key_b64 \
  source_archive_version_id_b64 source_sidecar_key_b64 \
  source_sidecar_version_id_b64 source_media_key_b64 source_media_version_id_b64 \
  source_media_sha256_b64 source_media_size_bytes_b64; do
  replace_placeholder "$placeholder" "$(b64 "${placeholder}-sample-0123456789abcdef0123456789abcdef0123456789abcdef")"
done
replace_placeholder recovery_assets_b64 "$asset_b64"

if rg -q '\$\{[A-Za-z0-9_]+\}' "$rendered"; then
  echo "Recovery bootstrap still has an unrendered Terraform placeholder." >&2
  exit 1
fi
bash -n "$rendered"

raw_user_data_bytes=$(gzip -n -c "$rendered" | wc -c | tr -d '[:space:]')
max_raw_user_data_bytes=15000
if [ "$raw_user_data_bytes" -gt "$max_raw_user_data_bytes" ]; then
  echo "Recovery EC2 raw user data is ${raw_user_data_bytes} bytes; budget is ${max_raw_user_data_bytes} bytes (EC2 limit 16384)." >&2
  exit 1
fi

echo "Recovery bootstrap user-data check passed: ${raw_user_data_bytes} bytes (budget ${max_raw_user_data_bytes}, EC2 limit 16384)."

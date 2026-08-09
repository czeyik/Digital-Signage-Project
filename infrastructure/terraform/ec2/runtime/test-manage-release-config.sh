#!/bin/bash
# Behaviour test for manage-release-config. Run as root so the production
# ownership checks are exercised against an isolated temporary filesystem tree.
set -Eeuo pipefail
umask 0077

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this test as root (for example: sudo bash $0)." >&2
  exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
manager="$script_dir/manage-release-config"
commit=0123456789abcdef0123456789abcdef01234567
operation_id=0123456789abcdef0123456789abcdef
replacement_operation_id=11111111111111111111111111111111
repository=173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-backend
old_image="$repository@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
new_image="$repository@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
other_image="$repository@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
test_root=$(mktemp -d /tmp/duducar-release-config-test.XXXXXX)

cleanup() {
  status=$?
  rm -f -- \
    "$test_root/etc/duducar/release.env" \
    "$test_root/expected-original.env" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$operation_id/release.env" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$operation_id/SHA256SUMS" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$operation_id/REQUEST.sha256" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$operation_id/UNRELATED.sha256" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$operation_id/MODE" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$replacement_operation_id/release.env" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$replacement_operation_id/SHA256SUMS" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$replacement_operation_id/REQUEST.sha256" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$replacement_operation_id/UNRELATED.sha256" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$replacement_operation_id/MODE" \
    "$test_root/var/lib/duducar/release-config-backups/.release-config.lock"
  rmdir -- \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$operation_id" \
    "$test_root/var/lib/duducar/release-config-backups/$commit-$replacement_operation_id" \
    "$test_root/var/lib/duducar/release-config-backups" \
    "$test_root/var/lib/duducar" \
    "$test_root/var/lib" \
    "$test_root/var" \
    "$test_root/etc/duducar" \
    "$test_root/etc" \
    "$test_root" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT

install -d -o root -g root -m 0755 "$test_root/etc/duducar"
install -d -o root -g root -m 0755 "$test_root/var/lib"
cat > "$test_root/etc/duducar/release.env" <<EOF
# Test-only non-secret production release configuration.
BACKEND_IMAGE=$old_image
POSTGRES_IMAGE=postgres@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
CADDY_IMAGE=caddy@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
CADDY_CONFIG=/etc/duducar/Caddyfile.post-cutover
EOF
chown root:root "$test_root/etc/duducar/release.env"
chmod 0640 "$test_root/etc/duducar/release.env"
cp -p "$test_root/etc/duducar/release.env" "$test_root/expected-original.env"

run_manager() {
  requested_image=$4
  requested_version=$5
  DUDUCAR_RELEASE_CONFIG_TEST_ROOT="$test_root" \
    "$manager" "$@" "$requested_image" "$requested_version"
}

# Validation is read-only and does not create a backup directory.
run_manager validate "$commit" "$operation_id" "$new_image" 1.0.0 "$repository"
cmp -s "$test_root/etc/duducar/release.env" "$test_root/expected-original.env"
test ! -e "$test_root/var/lib/duducar/release-config-backups/$commit-$operation_id"
test ! -e "$test_root/var/lib/duducar/release-config-backups"
if DUDUCAR_RELEASE_CONFIG_TEST_ROOT="$test_root" \
  "$manager" validate "$commit" "$operation_id" "$new_image" 1.0.0 \
  "$repository" "$other_image" 1.0.0; then
  echo "Manager accepted a value different from the Terraform-reviewed image." >&2
  exit 1
fi
if DUDUCAR_RELEASE_CONFIG_TEST_ROOT="$test_root" \
  "$manager" validate "$commit" "$operation_id" "$new_image" 1.0.0 \
  "$repository" "$new_image" 1.0.1; then
  echo "Manager accepted a version different from the Terraform-reviewed value." >&2
  exit 1
fi
printf 'REQUIRED_APP_VERSION=0.9.0\nREQUIRED_APP_VERSION=0.9.1\n' \
  >> "$test_root/etc/duducar/release.env"
if run_manager validate "$commit" "$operation_id" "$new_image" 1.0.0 "$repository"; then
  echo "Manager accepted duplicate REQUIRED_APP_VERSION assignments." >&2
  exit 1
fi
cp -p "$test_root/expected-original.env" "$test_root/etc/duducar/release.env"

run_manager install "$commit" "$operation_id" "$new_image" 1.0.0 "$repository"
test "$(awk -F= '/^BACKEND_IMAGE=/{print $2}' "$test_root/etc/duducar/release.env")" = "$new_image"
test "$(awk -F= '/^REQUIRED_APP_VERSION=/{print $2}' "$test_root/etc/duducar/release.env")" = 1.0.0
test "$(grep -c '^BACKEND_IMAGE=' "$test_root/etc/duducar/release.env")" = 1
test "$(grep -c '^REQUIRED_APP_VERSION=' "$test_root/etc/duducar/release.env")" = 1
grep -qx 'POSTGRES_IMAGE=postgres@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd' \
  "$test_root/etc/duducar/release.env"
grep -qx 'CADDY_CONFIG=/etc/duducar/Caddyfile.post-cutover' \
  "$test_root/etc/duducar/release.env"
test "$(stat -c '%U:%G %a' "$test_root/etc/duducar/release.env")" = 'root:root 640'
test -f "$test_root/var/lib/duducar/release-config-backups/$commit-$operation_id/release.env"
test "$(stat -c '%U:%G %a' "$test_root/var/lib/duducar/release-config-backups")" = 'root:root 700'
test "$(stat -c '%U:%G %a' "$test_root/var/lib/duducar/release-config-backups/.release-config.lock")" = 'root:root 600'

# Repeating the same operation is idempotent, but a different requested image
# cannot reuse its backup and therefore cannot overwrite the intended target.
installed_sha=$(sha256sum "$test_root/etc/duducar/release.env" | awk '{print $1}')
run_manager install "$commit" "$operation_id" "$new_image" 1.0.0 "$repository"
test "$(sha256sum "$test_root/etc/duducar/release.env" | awk '{print $1}')" = "$installed_sha"
if run_manager install "$commit" "$operation_id" "$other_image" 1.0.0 "$repository"; then
  echo "Manager accepted a different image for an existing operation." >&2
  exit 1
fi
test "$(sha256sum "$test_root/etc/duducar/release.env" | awk '{print $1}')" = "$installed_sha"

run_manager rollback "$commit" "$operation_id" "$new_image" 1.0.0 "$repository"
cmp -s "$test_root/etc/duducar/release.env" "$test_root/expected-original.env"
test "$(stat -c '%U:%G %a' "$test_root/etc/duducar/release.env")" = 'root:root 640'

# A stale rollback cannot discard a later release configuration change.
run_manager install "$commit" "$operation_id" "$new_image" 1.0.0 "$repository"
sed -i "s|^BACKEND_IMAGE=.*$|BACKEND_IMAGE=$other_image|" \
  "$test_root/etc/duducar/release.env"
if run_manager rollback "$commit" "$operation_id" "$new_image" 1.0.0 "$repository"; then
  echo "Manager accepted a stale rollback." >&2
  exit 1
fi
test "$(awk -F= '/^BACKEND_IMAGE=/{print $2}' "$test_root/etc/duducar/release.env")" = "$other_image"

# An existing REQUIRED_APP_VERSION is replaced in place, rather than duplicated.
sed -i 's/^REQUIRED_APP_VERSION=.*/REQUIRED_APP_VERSION=0.9.0/' \
  "$test_root/etc/duducar/release.env"
run_manager install "$commit" "$replacement_operation_id" "$new_image" 1.0.1 "$repository"
test "$(grep -c '^REQUIRED_APP_VERSION=' "$test_root/etc/duducar/release.env")" = 1
test "$(awk -F= '/^REQUIRED_APP_VERSION=/{print $2}' "$test_root/etc/duducar/release.env")" = 1.0.1

echo "manage-release-config tests passed."

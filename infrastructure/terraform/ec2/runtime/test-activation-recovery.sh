#!/bin/bash
set -Eeuo pipefail
umask 0077

runtime_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
activation=$runtime_dir/activate-release
test_root=$(mktemp -d /tmp/duducar-activation-test.XXXXXX)
fake_bin=$(mktemp -d /tmp/duducar-activation-tools.XXXXXX)
operation_id=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
prior_operation_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
command_id=11111111-1111-1111-1111-111111111111
commit=0123456789abcdef0123456789abcdef01234567
backend_image=example.test/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
postgres_image=postgres@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
caddy_image=caddy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc

cleanup() {
  status=$?
  rm -rf -- "$test_root" "$fake_bin"
  exit "$status"
}
trap cleanup EXIT

install -d -m 0755 \
  "$test_root/etc/duducar" \
  "$test_root/srv/duducar/postgres" \
  "$test_root/usr/local/sbin" \
  "$fake_bin"
cat > "$test_root/etc/duducar/host.env" <<'EOF'
AWS_REGION=ap-southeast-5
EOF
cat > "$test_root/etc/duducar/release.env" <<EOF
BACKEND_IMAGE=$backend_image
POSTGRES_IMAGE=$postgres_image
CADDY_IMAGE=$caddy_image
REQUIRED_APP_VERSION=1.0.0
CADDY_CONFIG=/etc/duducar/Caddyfile.post-cutover
EOF
chmod 0600 "$test_root/etc/duducar/host.env" "$test_root/etc/duducar/release.env"

cat > "$fake_bin/id" <<'EOF'
#!/bin/sh
if [ "${1:-}" = -u ]; then
  printf '0\n'
else
  exec /usr/bin/id "$@"
fi
EOF
cat > "$fake_bin/stat" <<'EOF'
#!/bin/sh
case "${2:-}" in
  %U:%G) printf 'root:root\n' ;;
  %a) printf '600\n' ;;
  %U:%G:%a) printf 'root:root:400\n' ;;
  *) exec /usr/bin/stat "$@" ;;
esac
EOF
cat > "$fake_bin/install" <<'EOF'
#!/bin/bash
set -Eeuo pipefail
if [ "${1:-}" = -d ]; then
  destination=${@: -1}
  mkdir -p "$destination"
  chmod 0700 "$destination"
  exit 0
fi
exec /usr/bin/install "$@"
EOF
cat > "$fake_bin/chown" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$fake_bin/systemctl" <<'EOF'
#!/bin/bash
set -Eeuo pipefail
printf 'systemctl %s\n' "$*" >> "$DUDUCAR_TEST_LOG"
case "${1:-}" in
  is-active|is-enabled)
    if [ "${2:-}" = --quiet ]; then
      exit 0
    fi
    if [ "${1:-}" = is-active ]; then
      printf 'inactive\n'
    else
      printf 'enabled\n'
    fi
    ;;
  *) ;;
esac
EOF
cat > "$fake_bin/docker" <<'EOF'
#!/bin/sh
set -eu
printf 'docker %s\n' "$*" >> "$DUDUCAR_TEST_LOG"
case "${1:-}" in
  ps) ;;
  *) ;;
esac
EOF
cat > "$fake_bin/ss" <<'EOF'
#!/bin/sh
exit 0
EOF

cat > "$test_root/manager" <<'EOF'
#!/bin/sh
printf 'manager %s\n' "$*" >> "$DUDUCAR_TEST_LOG"
EOF
cat > "$test_root/usr/local/sbin/render-duducar-runtime-env" <<'EOF'
#!/bin/sh
printf 'render\n' >> "$DUDUCAR_TEST_LOG"
EOF
cat > "$test_root/usr/local/sbin/duducar-stack" <<'EOF'
#!/bin/sh
set -eu
printf 'stack %s\n' "$*" >> "$DUDUCAR_TEST_LOG"
EOF
cat > "$test_root/usr/local/sbin/duducar-command" <<'EOF'
#!/bin/bash
set -Eeuo pipefail
printf 'command %s\n' "$*" >> "$DUDUCAR_TEST_LOG"
if [ "${1:-}" = backup-refresh ]; then
  if [ "${DUDUCAR_TEST_REFRESH_FAIL:-0}" = 1 ]; then
    exit 1
  fi
  printf '%s\n' "$2" > "$DUDUCAR_TEST_RECEIPT"
fi
if [ "${DUDUCAR_TEST_ACTIVATION_FAIL:-0}" = 1 ] && [ "${1:-}" = migrate ]; then
  exit 1
fi
EOF
cat > "$test_root/usr/local/sbin/duducar-backup-verify" <<'EOF'
#!/bin/bash
set -Eeuo pipefail
printf 'verify %s expected=%s\n' "$1" "${DUDUCAR_BACKUP_EXPECTED_OPERATION_ID:-}" >> "$DUDUCAR_TEST_LOG"
if [ "$1" = check ] &&
  [ -f "$DUDUCAR_TEST_RECEIPT" ] &&
  [ "$(cat "$DUDUCAR_TEST_RECEIPT")" = "${DUDUCAR_BACKUP_EXPECTED_OPERATION_ID:-}" ]; then
  exit 0
fi
if [ "$1" = check ]; then
  exit 1
fi
exit 0
EOF
cat > "$test_root/usr/local/sbin/duducar-host-health" <<'EOF'
#!/bin/sh
printf 'health %s\n' "$*" >> "$DUDUCAR_TEST_LOG"
EOF
cat > "$test_root/usr/local/sbin/duducar-alert" <<'EOF'
#!/bin/sh
printf 'alert %s\n' "$*" >> "$DUDUCAR_TEST_LOG"
EOF
chmod 0755 "$fake_bin"/* "$test_root/manager" "$test_root/usr/local/sbin"/*

export DUDUCAR_ACTIVATION_TEST_ROOT=$test_root
export DUDUCAR_TEST_LOG=$test_root/operations.log
export DUDUCAR_TEST_RECEIPT=$test_root/receipt
export PATH=$fake_bin:$PATH

run_activation() {
  bash "$activation" "$@"
}

common_args=(
  "$commit" "$operation_id" "" failed-existing "$backend_image" "$postgres_image"
  "$caddy_image" 1.0.0 "$prior_operation_id" "$command_id" "$test_root/manifest"
  "$test_root/manager"
)
arm_args=(
  "$commit" "$operation_id" "ARM $operation_id FROM $prior_operation_id" failed-existing
  "$backend_image" "$postgres_image" "$caddy_image" 1.0.0 "$prior_operation_id"
  "$command_id" "$test_root/manifest" "$test_root/manager"
)
activate_args=(
  "$commit" "$operation_id" "RECOVER $operation_id FROM $prior_operation_id" failed-existing
  "$backend_image" "$postgres_image" "$caddy_image" 1.0.0 "$prior_operation_id"
  "$command_id" "$test_root/manifest" "$test_root/manager"
)
mkdir -p "$test_root/manifest"

# A stale/missing receipt takes the private refresh path. A runner failure must
# leave no running component and must not consume an arm that does not exist.
if DUDUCAR_TEST_REFRESH_FAIL=1 run_activation validate "${common_args[@]}"; then
  echo "Validation accepted a failed stopped-state backup refresh." >&2
  exit 1
fi
grep -Fq 'stack stop' "$DUDUCAR_TEST_LOG"
grep -Fq 'systemctl stop duducar-credential-broker.service' "$DUDUCAR_TEST_LOG"
test ! -e "$test_root/var/lib/duducar-recovery/$operation_id"

# The same operation is retryable after cleanup and produces an operation-bound
# receipt. The next validation reuses that receipt without starting anything.
run_activation validate "${common_args[@]}"
test "$(cat "$DUDUCAR_TEST_RECEIPT")" = "$operation_id"
refresh_count=$(grep -c "command backup-refresh $operation_id" "$DUDUCAR_TEST_LOG")
test "$refresh_count" -eq 2
run_activation validate "${common_args[@]}"
test "$(grep -c "command backup-refresh $operation_id" "$DUDUCAR_TEST_LOG")" -eq 2

run_activation arm-failed-existing "${arm_args[@]}"
test -f "$test_root/var/lib/duducar-recovery/$operation_id"

# Once the authorization is consumed and deployment begins, a later failure
# stays fail-closed and leaves a claim so the operation cannot be replayed.
if DUDUCAR_TEST_ACTIVATION_FAIL=1 run_activation activate \
  "${activate_args[@]}"; then
  echo "Activation accepted a simulated deployment failure." >&2
  exit 1
fi
test ! -e "$test_root/var/lib/duducar-recovery/$operation_id"
test -d "$test_root/var/lib/duducar-recovery/.claim-$operation_id"
grep -Fq 'systemctl stop duducar-health.timer duducar-playlists.timer duducar-media-reconcile.timer duducar-retention.timer duducar-backup.timer duducar.service duducar-credential-broker.service' "$DUDUCAR_TEST_LOG"

echo "Failed-existing stale refresh, cleanup, retry, reuse, and fail-closed activation checks passed."

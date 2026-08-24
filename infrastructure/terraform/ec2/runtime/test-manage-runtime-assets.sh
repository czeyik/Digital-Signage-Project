#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this runtime bundle contract test as root." >&2
  exit 2
fi

runtime_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
manager=$runtime_dir/manage-runtime-assets
test_root=$(mktemp -d /tmp/duducar-runtime-assets-test.XXXXXX)
stage=$(mktemp -d /tmp/duducar-runtime-assets-stage.XXXXXX)
fake_bin=$(mktemp -d /tmp/duducar-runtime-assets-tools.XXXXXX)
cleanup() {
  rm -rf -- "$test_root" "$stage" "$fake_bin"
}
trap cleanup EXIT

assets=(
  Caddyfile.post-cutover pg_hba.conf postgres-init-roles.sh
  postgres-runtime-grants.sql render-runtime-env duducar-stack duducar-command
  duducar-alert duducar-host-health duducar-backup-verify
  duducar-credential-broker duducar.service duducar-credential-broker.service
  duducar-command@.service duducar-alert@.service duducar-health.timer
  duducar-playlists.timer duducar-media-reconcile.timer duducar-retention.timer
  duducar-backup.timer
)

source_for() {
  printf '%s/%s\n' "$runtime_dir" "$1"
}

target_for() {
  case "$1" in
    Caddyfile.post-cutover|pg_hba.conf)
      printf '%s/etc/duducar/%s\n' "$test_root" "$1" ;;
    postgres-init-roles.sh|postgres-runtime-grants.sql)
      printf '%s/etc/duducar/postgres-init/%s\n' "$test_root" "$1" ;;
    render-runtime-env)
      printf '%s/usr/local/sbin/render-duducar-runtime-env\n' "$test_root" ;;
    duducar-stack|duducar-command|duducar-alert|duducar-host-health|duducar-backup-verify|duducar-credential-broker)
      printf '%s/usr/local/sbin/%s\n' "$test_root" "$1" ;;
    *) printf '%s/etc/systemd/system/%s\n' "$test_root" "$1" ;;
  esac
}

for asset in "${assets[@]}"; do
  install -o root -g root -m 0600 "$(source_for "$asset")" "$stage/$asset"
done
(
  cd "$stage"
  sha256sum "${assets[@]}" > MANIFEST
)

for asset in "${assets[@]}"; do
  # Exercise rollback of both replaced files and newly introduced files.
  [ "$asset" = duducar-credential-broker ] && continue
  target=$(target_for "$asset")
  install -d -o root -g root -m 0755 "$(dirname "$target")"
  printf 'old-%s\n' "$asset" > "$target"
  chown root:root "$target"
  chmod 0640 "$target"
done
# Simulate an existing host whose broker unit references the new executable,
# while that executable has not yet been installed.
install -o root -g root -m 0640 \
  "$(source_for duducar-credential-broker.service)" \
  "$(target_for duducar-credential-broker.service)"
old_stack_sha=$(sha256sum "$(target_for duducar-stack)" | awk '{print $1}')

cat > "$fake_bin/docker" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$fake_bin/systemd-analyze" <<'EOF'
#!/bin/sh
for unit in "$@"; do
  test -f "$unit"
done
if grep -q '^ExecStart=/usr/local/sbin/duducar-credential-broker$' \
  "$DUDUCAR_RUNTIME_ASSET_TEST_ROOT/etc/systemd/system/duducar-credential-broker.service"; then
  test -x "$DUDUCAR_RUNTIME_ASSET_TEST_ROOT/usr/local/sbin/duducar-credential-broker"
fi
printf '%s\n' "$*" >> "$DUDUCAR_TEST_SYSTEMD_ANALYZE_LOG"
EOF
cat > "$fake_bin/systemctl" <<'EOF'
#!/bin/sh
test "$1" = daemon-reload
printf '%s\n' "$*" >> "$DUDUCAR_TEST_SYSTEMCTL_LOG"
EOF
chmod 0755 "$fake_bin"/*

commit=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
operation_id=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
caddy_image=caddy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
export DUDUCAR_RUNTIME_ASSET_TEST_ROOT=$test_root
export DUDUCAR_TEST_SYSTEMCTL_LOG=$test_root/systemctl.log
export DUDUCAR_TEST_SYSTEMD_ANALYZE_LOG=$test_root/systemd-analyze.log
export PATH=$fake_bin:$PATH

"$manager" validate "$commit" "$operation_id" "$stage" "$caddy_image"
test ! -e "$test_root/var/lib/duducar/runtime-backups"
test ! -e "$DUDUCAR_TEST_SYSTEMD_ANALYZE_LOG"
"$manager" install "$commit" "$operation_id" "$stage" "$caddy_image"
cmp -s "$stage/duducar-stack" "$(target_for duducar-stack)"
cmp -s "$stage/duducar-credential-broker" "$(target_for duducar-credential-broker)"
test "$(cat "$test_root/systemctl.log")" = daemon-reload
test "$(wc -l < "$DUDUCAR_TEST_SYSTEMD_ANALYZE_LOG")" -eq 1

installed_sha=$(sha256sum "$(target_for duducar-stack)" | awk '{print $1}')
"$manager" install "$commit" "$operation_id" "$stage" "$caddy_image"
test "$(sha256sum "$(target_for duducar-stack)" | awk '{print $1}')" = "$installed_sha"

"$manager" rollback "$commit" "$operation_id" "$stage" "$caddy_image"
test "$(sha256sum "$(target_for duducar-stack)" | awk '{print $1}')" = "$old_stack_sha"
test ! -e "$(target_for duducar-credential-broker)"
test "$(grep -c '^daemon-reload$' "$test_root/systemctl.log")" -eq 2

echo "Complete runtime bundle validate, install, retry, and rollback checks passed."

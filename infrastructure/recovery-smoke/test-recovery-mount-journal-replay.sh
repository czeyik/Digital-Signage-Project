#!/bin/bash
set -euo pipefail
umask 0077

# Exercise the crash-consistent XFS journal-replay path without a real block
# device, mount, systemd, or AWS call. The copied helper is pointed at a
# disposable fixture, while fake tools model only the invariants the helper
# must check before it can authorize a writable recovery mount.
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
test_dir=$(mktemp -d /tmp/duducar-recovery-journal-replay.XXXXXX)
fake_bin="$test_dir/bin"
helper="$test_dir/duducar-recovery-mount"
host_config="$test_dir/host.env"
device="$test_dir/recovery-clone"
receipt_dir="$test_dir/receipts"
mount_state="$test_dir/mount-state"
mount_log="$test_dir/mount.log"
xfs_count="$test_dir/xfs-repair-count"
operation_id=0123456789abcdef0123456789abcdef

cleanup() {
  rm -rf -- "$test_dir"
}
trap cleanup EXIT

fail() {
  echo "$*" >&2
  exit 1
}

assert_file() {
  local path=$1
  [ -f "$path" ] || fail "Expected file is missing: $path"
}

assert_absent() {
  local path=$1
  [ ! -e "$path" ] || fail "Unexpected file remains: $path"
}

assert_contains() {
  local literal=$1
  local file=$2
  grep -Fq -- "$literal" "$file" || fail "Missing expected value '$literal' in $file"
}

mkdir -p "$fake_bin" "$test_dir/mnt"
: > "$device"
cp "$root_dir/runtime/duducar-recovery-mount" "$helper"

# The production helper deliberately requires a real block device and root
# ownership. Those constraints are modeled by the fake tools here so this test
# works under an unprivileged CI account without weakening the shipped helper.
sed -i \
  -e "s|^host_config=.*$|host_config=$host_config|" \
  -e "s|^recovery_mountpoint=.*$|recovery_mountpoint=$test_dir/srv/duducar|" \
  -e "s|^receipt_dir=.*$|receipt_dir=$receipt_dir|" \
  -e "s|^device=.*$|device=$device|" \
  -e "s|/mnt/duducar-recovery-inspection.XXXXXX|$test_dir/mnt/duducar-recovery-inspection.XXXXXX|" \
  -e "s|/mnt/duducar-recovery-journal-replay.XXXXXX|$test_dir/mnt/duducar-recovery-journal-replay.XXXXXX|" \
  -e 's/if \[ -b "\$device" \]; then/if [ -e "$device" ]; then/' \
  "$helper"
bash -n "$helper"

printf '%s\n' \
  "RECOVERY_OPERATION_ID=$operation_id" \
  'RECOVERY_DATA_VOLUME_ID=vol-recovery-clone' \
  'SOURCE_SNAPSHOT_ID=snap-source' \
  'SOURCE_DATA_VOLUME_ID=vol-source' > "$host_config"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'case "${4:-}" in' \
  '  TYPE) printf "%s\\n" xfs ;;' \
  '  UUID) printf "%s\\n" 11111111-2222-3333-4444-555555555555 ;;' \
  '  *) echo "Unexpected fake blkid invocation: $*" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/blkid"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'if [ "${1:-}" = -d ]; then' \
  '  target="${!#}"' \
  '  mkdir -p -- "$target"' \
  '  exit 0' \
  'fi' \
  'exec /usr/bin/install "$@"' > "$fake_bin/install"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  '# The test fixture cannot chown files to root; fake stat below verifies the' \
  '# ownership/mode contract seen by the helper.' \
  'exit 0' > "$fake_bin/chown"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'if [ "${1:-}" = -c ]; then' \
  '  case "${2:-}" in' \
  '    %U:%G) printf "%s\\n" root:root; exit 0 ;;' \
  '    %a) printf "%s\\n" 600; exit 0 ;;' \
  '  esac' \
  'fi' \
  'exec /usr/bin/stat "$@"' > "$fake_bin/stat"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'if [ "${1:-}" != -o ] || [ "$#" -ne 4 ]; then' \
  '  echo "Unexpected fake mount invocation: $*" >&2' \
  '  exit 1' \
  'fi' \
  'options=$2' \
  'device=$3' \
  'target=$4' \
  'printf "%s\\t%s\\t%s\\n" "$target" "$device" "$options" >> "$FAKE_MOUNT_LOG"' \
  'if [[ "$options" == rw,* ]] && [ "${FAKE_REPLAY_MOUNT:-ok}" = fail ]; then' \
  '  echo "fixture replay mount failure" >&2' \
  '  exit 1' \
  'fi' \
  'printf "%s\\t%s\\t%s\\n" "$target" "$device" "$options" > "$FAKE_MOUNT_STATE"' \
  'if [[ "$options" == ro,* ]]; then' \
  '  mkdir -p -- "$target/postgres" "$target/postgres-secrets" "$target/postgres-tls" "$target/backups"' \
  '  printf fixture > "$target/postgres-secrets/admin-password"' \
  '  printf fixture > "$target/postgres-secrets/owner-password"' \
  '  printf fixture > "$target/postgres-tls/server.key"' \
  'fi' > "$fake_bin/mount"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'target=${1:?missing target}' \
  'if [ -s "$FAKE_MOUNT_STATE" ] && grep -Fq -- "$target" "$FAKE_MOUNT_STATE"; then' \
  '  : > "$FAKE_MOUNT_STATE"' \
  'fi' > "$fake_bin/umount"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'target=' \
  'source=' \
  'output=' \
  'for ((index = 1; index <= $#; index++)); do' \
  '  argument="${!index}"' \
  '  case "$argument" in' \
  '    --target) next=$((index + 1)); target="${!next}" ;;' \
  '    --source) next=$((index + 1)); source="${!next}" ;;' \
  '    --output) next=$((index + 1)); output="${!next}" ;;' \
  '  esac' \
  'done' \
  'if [ ! -s "$FAKE_MOUNT_STATE" ]; then' \
  '  exit 1' \
  'fi' \
  'mounted_target=$(cut -f1 "$FAKE_MOUNT_STATE")' \
  'mounted_source=$(cut -f2 "$FAKE_MOUNT_STATE")' \
  'mounted_options=$(cut -f3 "$FAKE_MOUNT_STATE")' \
  'if [ -n "$source" ]; then' \
  '  [ "$source" = "$mounted_source" ] || exit 1' \
  '  printf "%s\\n" "$mounted_target"' \
  '  exit 0' \
  'fi' \
  '[ -n "$target" ] && [ "$target" = "$mounted_target" ] || exit 1' \
  'case "$output" in' \
  '  SOURCE) printf "%s\\n" "$mounted_source" ;;' \
  '  FSTYPE) printf "%s\\n" xfs ;;' \
  '  OPTIONS) printf "%s\\n" "$mounted_options" ;;' \
  '  *) printf "%s xfs %s %s\\n" "$mounted_source" "$mounted_options" "$mounted_target" ;;' \
  'esac' > "$fake_bin/findmnt"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'count=0' \
  'if [ -f "$FAKE_XFS_COUNT" ]; then' \
  '  count=$(cat "$FAKE_XFS_COUNT")' \
  'fi' \
  'count=$((count + 1))' \
  'printf "%s\\n" "$count" > "$FAKE_XFS_COUNT"' \
  'if [ "$count" -eq 1 ]; then' \
  '  printf "%s\\n" "ALERT: The filesystem has valuable metadata changes in a log which is being"' \
  '  printf "%s\\n" "ignored because the -n option was used.  Expect spurious inconsistencies"' \
  '  if [ "${FAKE_INITIAL_REPAIR:-dirty}" != missing-marker ]; then' \
  '    printf "%s\\n" "which may be resolved by first mounting the filesystem to replay the log."' \
  '  fi' \
  '  printf "%s\\n" "No modify flag set, skipping filesystem flush and exiting."' \
  '  [ "${FAKE_INITIAL_REPAIR:-dirty}" = unexpected-status ] && exit 2' \
  '  exit 1' \
  'fi' \
  'if [ "${FAKE_POST_REPAIR:-clean}" = fail ]; then' \
  '  printf "%s\\n" "post-replay fixture failure"' \
  '  exit 1' \
  'fi' \
  'printf "%s\\n" "post-replay fixture clean"' > "$fake_bin/xfs_repair"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'case "${1:-}" in' \
  '  is-active) [ "${FAKE_DOCKER_MODE:-masked}" = active ] && exit 0; exit 3 ;;' \
  '  is-enabled)' \
  '    if [ "${FAKE_DOCKER_MODE:-masked}" = masked ]; then' \
  '      printf "%s\\n" masked' \
  '    else' \
  '      printf "%s\\n" disabled' \
  '    fi' \
  '    ;;' \
  '  *) exit 0 ;;' \
  'esac' > "$fake_bin/systemctl"

printf '%s\n' \
  '#!/bin/bash' \
  'printf "%s\\n" aarch64' > "$fake_bin/uname"

printf '%s\n' \
  '#!/bin/bash' \
  'exit 0' > "$fake_bin/sync"

chmod 0755 "$fake_bin"/*

run_helper() {
  PATH="$fake_bin:$PATH" \
    FAKE_MOUNT_LOG="$mount_log" \
    FAKE_MOUNT_STATE="$mount_state" \
    FAKE_XFS_COUNT="$xfs_count" \
    bash "$helper" "$@"
}

expect_failure() {
  local expected_status=$1
  shift
  local output
  local status
  if output=$("$@" 2>&1); then
    fail "Expected command to fail: $*"
  else
    status=$?
  fi
  if [ "$status" -ne "$expected_status" ]; then
    echo "$output" >&2
    fail "Expected status $expected_status, got $status: $*"
  fi
  printf '%s' "$output"
}

FAKE_DOCKER_MODE=masked
export FAKE_DOCKER_MODE
FAKE_POST_REPAIR=clean
export FAKE_POST_REPAIR
FAKE_INITIAL_REPAIR=dirty
export FAKE_INITIAL_REPAIR
FAKE_REPLAY_MOUNT=ok
export FAKE_REPLAY_MOUNT

# A dirty journal is not a successful inspection. It leaves a receipt that
# permits only the separately confirmed clone-only replay, never /srv mount.
output=$(expect_failure 3 run_helper inspect)
[[ "$output" == *'journal replay'* ]] || fail "Dirty-log inspection did not explain the replay requirement."
assert_file "$receipt_dir/pre-replay-inspection-receipt"
assert_file "$receipt_dir/pre-replay-xfs-repair.log"
assert_absent "$receipt_dir/inspection-receipt"
assert_contains 'stage=journal_replay_required' "$receipt_dir/pre-replay-inspection-receipt"
assert_contains $'\tro,nouuid,norecovery,nodev,nosuid,noexec' "$mount_log"
if grep -Fq $'\trw,' "$mount_log"; then
  fail "Dirty-log inspection performed a writable mount."
fi

# The operation-bound confirmation is required before the helper considers a
# replay. A missing confirmation must not invoke the fake mount tool.
mount_calls_before=$(wc -l < "$mount_log")
output=$(expect_failure 2 run_helper replay-journal)
[[ "$output" == *'Usage:'* ]] || fail "Replay without confirmation did not show usage."
mount_calls_after=$(wc -l < "$mount_log")
[ "$mount_calls_after" -eq "$mount_calls_before" ] || fail "Replay without confirmation reached mount."

output=$(expect_failure 2 run_helper replay-journal --confirm "REPLAY-JOURNAL 00000000000000000000000000000000")
[[ "$output" == *'Usage:'* ]] || fail "Replay with another operation ID did not show usage."
mount_calls_after=$(wc -l < "$mount_log")
[ "$mount_calls_after" -eq "$mount_calls_before" ] || fail "Wrong-operation confirmation reached mount."

# Even a correct confirmation fails while Docker is not explicitly quarantined.
FAKE_DOCKER_MODE=active
output=$(expect_failure 1 run_helper replay-journal --confirm "REPLAY-JOURNAL $operation_id")
[[ "$output" == *'is active'* ]] || fail "Replay did not reject an active Docker service."
mount_calls_after=$(wc -l < "$mount_log")
[ "$mount_calls_after" -eq "$mount_calls_before" ] || fail "Active-Docker guard failure reached mount."

FAKE_DOCKER_MODE=unmasked
output=$(expect_failure 1 run_helper replay-journal --confirm "REPLAY-JOURNAL $operation_id")
[[ "$output" == *'not masked'* ]] || fail "Replay did not reject an unmasked Docker service."
mount_calls_after=$(wc -l < "$mount_log")
[ "$mount_calls_after" -eq "$mount_calls_before" ] || fail "Docker-guard failure reached mount."

# A failed first writable mount consumes the pending authorization. It cannot
# be retried without a fresh read-only inspection, even if the operator later
# fixes the mount cause.
FAKE_DOCKER_MODE=masked
FAKE_REPLAY_MOUNT=fail
output=$(expect_failure 1 run_helper replay-journal --confirm "REPLAY-JOURNAL $operation_id")
[[ "$output" == *'fixture replay mount failure'* ]] || fail "Replay mount failure was not surfaced."
assert_absent "$receipt_dir/inspection-receipt"
assert_absent "$receipt_dir/pre-replay-inspection-receipt"
assert_file "$receipt_dir/pre-replay-xfs-repair.log"
FAKE_REPLAY_MOUNT=ok
rm -f "$xfs_count"
output=$(expect_failure 3 run_helper inspect)
[[ "$output" == *'journal replay'* ]] || fail "Fresh inspection did not recreate the pending replay state."

# A clean post-replay check creates the final receipt only after a brief
# rw,nouuid mount at the dedicated temporary path has been unmounted.
FAKE_DOCKER_MODE=masked
run_helper replay-journal --confirm "REPLAY-JOURNAL $operation_id"
assert_file "$receipt_dir/inspection-receipt"
assert_file "$receipt_dir/post-replay-xfs-repair.log"
assert_contains 'stage=journal_replayed_clean' "$receipt_dir/inspection-receipt"
assert_contains $'\trw,nouuid,nodev,nosuid,noexec,noatime' "$mount_log"
if ! grep -Fq '/duducar-recovery-journal-replay.' "$mount_log"; then
  fail "Journal replay did not use its dedicated temporary mount directory."
fi
if grep -Fq "$test_dir/srv/duducar" "$mount_log"; then
  fail "Journal replay mounted the normal recovery data-root."
fi

# A later failing post-replay check must revoke both the old final receipt and
# the new pending authorization, leaving diagnostics only for investigation.
rm -f "$xfs_count"
FAKE_POST_REPAIR=fail
output=$(expect_failure 3 run_helper inspect)
[[ "$output" == *'journal replay'* ]] || fail "Second dirty-log inspection did not require replay."
assert_absent "$receipt_dir/inspection-receipt"
assert_file "$receipt_dir/pre-replay-inspection-receipt"
output=$(expect_failure 1 run_helper replay-journal --confirm "REPLAY-JOURNAL $operation_id")
[[ "$output" == *'post-replay'* ]] || fail "Failed replay did not report the post-replay check."
assert_absent "$receipt_dir/inspection-receipt"
assert_absent "$receipt_dir/pre-replay-inspection-receipt"
assert_file "$receipt_dir/pre-replay-xfs-repair.log"
assert_file "$receipt_dir/post-replay-xfs-repair.log"

# A nonzero XFS check may authorize the controlled replay only when every
# expected dirty-log/no-modify marker is present and xfs_repair returned one.
# A missing marker or an unexpected failure status must leave no replay receipt.
rm -f "$xfs_count"
FAKE_INITIAL_REPAIR=missing-marker
rw_mounts_before=$(grep -Fc $'\trw,' "$mount_log" || true)
output=$(expect_failure 1 run_helper inspect)
[[ "$output" == *'non-dirty-log problem'* ]] || fail "Inspection did not fail closed for an incomplete dirty-log diagnostic."
assert_absent "$receipt_dir/inspection-receipt"
assert_absent "$receipt_dir/pre-replay-inspection-receipt"
assert_file "$receipt_dir/pre-replay-xfs-repair.log"
rw_mounts_after=$(grep -Fc $'\trw,' "$mount_log" || true)
[ "$rw_mounts_after" -eq "$rw_mounts_before" ] || fail "Incomplete dirty-log inspection performed a writable mount."

rm -f "$xfs_count"
FAKE_INITIAL_REPAIR=unexpected-status
output=$(expect_failure 1 run_helper inspect)
[[ "$output" == *'non-dirty-log problem'* ]] || fail "Inspection did not fail closed for an unexpected xfs_repair status."
assert_absent "$receipt_dir/inspection-receipt"
assert_absent "$receipt_dir/pre-replay-inspection-receipt"

echo "Recovery XFS journal-replay containment checks passed."

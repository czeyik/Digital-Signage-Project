#!/bin/bash
set -euo pipefail
umask 0077

# Exercise the logical archive restore runner without AWS, Docker, or a
# mounted clone. The backup remains root-only on the host, while only the two
# capless, read-only pg_restore containers may run as root inside the
# container. Django migration helpers must retain the image's non-root user.
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
test_dir=$(mktemp -d /tmp/duducar-recovery-logical-restore.XXXXXX)
fake_bin="$test_dir/bin"
helper="$test_dir/duducar-recovery-restore"
host_config="$test_dir/host.env"
release_config="$test_dir/release.env"
runtime_dir="$test_dir/runtime"
archive_dir="$test_dir/archives"
stack_log="$test_dir/stack.log"
owner_password_dir="$runtime_dir/database-owner"
owner_password_source="$owner_password_dir/database-owner-password"
archive_name=fixture.dump
sidecar_name=fixture.dump.sha256
archive_key="database-backups/$archive_name"
sidecar_key="database-backups/$sidecar_name"
archive_payload='fixture logical PostgreSQL archive'
archive_sha256=$(printf '%s' "$archive_payload" | sha256sum | awk '{print $1}')
backend_image='registry.example.invalid/duducar-backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'

cleanup() {
  # The fixture models the app user's non-writable 0500 directory. Restore
  # owner write permission only after every assertion so the temporary test
  # directory itself can be removed.
  /usr/bin/chmod 0700 "$owner_password_dir" 2>/dev/null || true
  rm -rf -- "$test_dir"
}
trap cleanup EXIT

fail() {
  echo "$*" >&2
  exit 1
}

assert_arg_count() {
  local label=$1
  local wanted=$2
  local expected_count=$3
  shift 3
  local actual_count=0
  local arg
  for arg in "$@"; do
    if [ "$arg" = "$wanted" ]; then
      actual_count=$((actual_count + 1))
    fi
  done
  [ "$actual_count" -eq "$expected_count" ] || \
    fail "$label expected $expected_count occurrence(s) of $wanted, found $actual_count"
}

assert_arg_value() {
  local label=$1
  local flag=$2
  local expected_value=$3
  shift 3
  local -a args=("$@")
  local index
  local next_index
  local found_flag=false
  for index in "${!args[@]}"; do
    if [ "${args[$index]}" = "$flag" ]; then
      found_flag=true
      next_index=$((index + 1))
      [ "$next_index" -lt "${#args[@]}" ] || fail "$label is missing a value after $flag"
      if [ "${args[$next_index]}" = "$expected_value" ]; then
        return 0
      fi
    fi
  done
  [ "$found_flag" = true ] || fail "$label is missing $flag"
  fail "$label is missing $flag $expected_value"
}

assert_no_arg() {
  local label=$1
  local forbidden=$2
  shift 2
  local arg
  for arg in "$@"; do
    [ "$arg" != "$forbidden" ] || fail "$label must not include $forbidden"
  done
}

assert_no_arg_containing() {
  local label=$1
  local forbidden=$2
  shift 2
  local arg
  for arg in "$@"; do
    [[ "$arg" != *"$forbidden"* ]] || \
      fail "$label must not receive an argument containing $forbidden"
  done
}

assert_arg_containing() {
  local label=$1
  local expected_fragment=$2
  shift 2
  local arg
  for arg in "$@"; do
    [[ "$arg" == *"$expected_fragment"* ]] && return 0
  done
  fail "$label is missing an argument containing $expected_fragment"
}

assert_exact_file_argv() {
  local label=$1
  local file=$2
  shift 2
  local -a expected=("$@")
  local -a actual=()
  local index
  mapfile -d '' -t actual < "$file"
  [ "${#actual[@]}" -eq "${#expected[@]}" ] || \
    fail "$label had ${#actual[@]} arguments, expected ${#expected[@]}"
  for index in "${!expected[@]}"; do
    [ "${actual[$index]}" = "${expected[$index]}" ] || \
      fail "$label argument $index expected ${expected[$index]}, found ${actual[$index]}"
  done
}

state_value() {
  local key=$1
  local file=$2
  awk -F= -v key="$key" '$1 == key { print substr($0, length(key) + 2); exit }' "$file"
}

assert_original_password_unchanged() {
  [ "$(stat -c '%a' "$owner_password_dir")" = 500 ] || \
    fail 'The appuser owner-password directory mode changed.'
  [ "$(stat -c '%a' "$owner_password_source")" = 400 ] || \
    fail 'The appuser owner-password file mode changed.'
  [ "$(sha256sum "$owner_password_source" | awk '{print $1}')" = "$original_password_sha256" ] || \
    fail 'The appuser owner-password file content changed.'
}

mkdir -p "$fake_bin" "$owner_password_dir"
cp "$root_dir/runtime/duducar-recovery-restore" "$helper"

# Point the copied helper at the fixture while preserving the shipped helper's
# absolute-path production safeguards.
sed -i \
  -e "s|^host_config=.*$|host_config=$host_config|" \
  -e "s|^release_config=.*$|release_config=$release_config|" \
  -e "s|^runtime_dir=.*$|runtime_dir=$runtime_dir|" \
  -e "s|^archive_dir=.*$|archive_dir=$archive_dir|" \
  -e "s|/usr/local/sbin/duducar-recovery-mount|$fake_bin/duducar-recovery-mount|g" \
  -e "s|/usr/local/sbin/duducar-recovery-stack|$fake_bin/duducar-recovery-stack|g" \
  "$helper"
bash -n "$helper"

printf '%s\n' \
  'AWS_REGION=ap-southeast-5' \
  'PILOT_BACKUP_SOURCE_BUCKET=fixture-backup-bucket' \
  "SOURCE_ARCHIVE_KEY=$archive_key" \
  'SOURCE_ARCHIVE_VERSION_ID=fixture-archive-version-123' \
  "SOURCE_SIDECAR_KEY=$sidecar_key" \
  'SOURCE_SIDECAR_VERSION_ID=fixture-sidecar-version-123' > "$host_config"
printf '%s\n' "BACKEND_IMAGE=$backend_image" > "$release_config"
printf 'fixture database-owner password\n' > "$owner_password_source"
chmod 0500 "$owner_password_dir"
chmod 0400 "$owner_password_source"
original_password_sha256=$(sha256sum "$owner_password_source" | awk '{print $1}')

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  '[ "${1:-}" = verify-mounted ] || { echo "Unexpected mount helper invocation: $*" >&2; exit 1; }' > "$fake_bin/duducar-recovery-mount"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'case "${1:-}" in' \
  '  database|stop) printf "%s\\n" "$1" >> "$FAKE_STACK_LOG" ;;' \
  '  *) echo "Unexpected stack helper invocation: $*" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/duducar-recovery-stack"

# Model root ownership because the CI user cannot create root-owned fixture
# paths. The test checks the exact install/chown argv and real mode bits below.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'if [ "${1:-}" = -d ]; then' \
  '  [ "$#" -eq 8 ] && [ "${2:-}" = -o ] && [ "${3:-}" = root ] && [ "${4:-}" = -g ] && [ "${5:-}" = root ] && [ "${6:-}" = -m ] && [ "${7:-}" = 0700 ] || { echo "Archive directory must stay root:root 0700" >&2; exit 1; }' \
  '  printf "%s\\0" "$@" > "$FAKE_ARTIFACT_DIR/archive-install-args"' \
  '  mkdir -p -- "${!#}"' \
  '  /usr/bin/chmod 0700 "${!#}"' \
  '  exit 0' \
  'fi' \
  'if [ "$#" -eq 8 ] && [ "${1:-}" = -o ] && [ "${2:-}" = root ] && [ "${3:-}" = -g ] && [ "${4:-}" = root ] && [ "${5:-}" = -m ] && [ "${6:-}" = 0400 ]; then' \
  '  source_path=${7:-}' \
  '  destination=${8:-}' \
  '  [ "$source_path" = "$FAKE_OWNER_PASSWORD_SOURCE" ] || { echo "Unexpected password-copy source: $source_path" >&2; exit 1; }' \
  '  case "$destination" in' \
  '    "$FAKE_RUNTIME_DIR"/logical-restore.*/database-owner-password) ;;' \
  '    *) echo "Unexpected password-copy destination: $destination" >&2; exit 1 ;;' \
  '  esac' \
  '  printf "%s\\0" "$@" > "$FAKE_ARTIFACT_DIR/password-copy-args"' \
  '  exec /usr/bin/install -m 0400 "$source_path" "$destination"' \
  'fi' \
  'echo "Unexpected install invocation: $*" >&2' \
  'exit 1' > "$fake_bin/install"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  '[ "$#" -eq 2 ] && [ "${1:-}" = root:root ] || { echo "Unexpected chown invocation: $*" >&2; exit 1; }' \
  'case "${2:-}" in' \
  '  "$FAKE_RUNTIME_DIR"/logical-restore.*) ;;' \
  '  *) echo "Original password ownership must not change: $*" >&2; exit 1 ;;' \
  'esac' \
  'printf "%s\\0" "$@" > "$FAKE_ARTIFACT_DIR/temporary-owner-dir-chown-args"' > "$fake_bin/chown"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'printf "%q " "$@" >> "$FAKE_ARTIFACT_DIR/chmod.log"' \
  'printf "\\n" >> "$FAKE_ARTIFACT_DIR/chmod.log"' \
  'case "${1:-}" in' \
  '  0600)' \
  '    [ "$#" -eq 3 ] && [ "${2:-}" = "$FAKE_ARCHIVE_PATH" ] && [ "${3:-}" = "$FAKE_SIDECAR_PATH" ] || { echo "Unexpected archive chmod: $*" >&2; exit 1; }' \
  '    exec /usr/bin/chmod "$@"' \
  '    ;;' \
  '  0700)' \
  '    [ "$#" -eq 2 ] || { echo "Unexpected temporary-directory chmod: $*" >&2; exit 1; }' \
  '    case "${2:-}" in "$FAKE_RUNTIME_DIR"/logical-restore.*) exec /usr/bin/chmod "$@" ;; *) echo "Original password mode must not change: $*" >&2; exit 1 ;; esac' \
  '    ;;' \
  '  *) echo "Unexpected chmod invocation: $*" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/chmod"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'printf "%q " "$@" >> "$FAKE_ARTIFACT_DIR/rm.log"' \
  'printf "\\n" >> "$FAKE_ARTIFACT_DIR/rm.log"' \
  'case "${1:-}" in' \
  '  -f)' \
  '    [ "$#" -eq 3 ] && [ "${2:-}" = "$FAKE_ARCHIVE_PATH" ] && [ "${3:-}" = "$FAKE_SIDECAR_PATH" ] || { echo "Unexpected archive cleanup: $*" >&2; exit 1; }' \
  '    exec /usr/bin/rm "$@"' \
  '    ;;' \
  '  -rf)' \
  '    [ "$#" -eq 3 ] && [ "${2:-}" = -- ] || { echo "Unexpected temporary credential cleanup: $*" >&2; exit 1; }' \
  '    case "${3:-}" in "$FAKE_RUNTIME_DIR"/logical-restore.*) exec /usr/bin/rm "$@" ;; *) echo "Original credential cleanup is forbidden: $*" >&2; exit 1 ;; esac' \
  '    ;;' \
  '  *) echo "Unexpected rm invocation: $*" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/rm"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'if [ "${1:-}" = -c ] && [ "${2:-}" = %U:%G ]; then' \
  '  case "${3:-}" in' \
  '    "$FAKE_RUNTIME_DIR"/database-owner) printf "%s\\n" 10001:10001; exit 0 ;;' \
  '    "$FAKE_OWNER_PASSWORD_SOURCE") printf "%s\\n" 10001:10001; exit 0 ;;' \
  '    "$FAKE_RUNTIME_DIR"/logical-restore.*|"$FAKE_RUNTIME_DIR"/logical-restore.*/database-owner-password) printf "%s\\n" root:root; exit 0 ;;' \
  '  esac' \
  'fi' \
  'exec /usr/bin/stat "$@"' > "$fake_bin/stat"

# Materialize exactly the two immutable fixture objects. The real checksum
# utility then verifies the sidecar from the root-only host directory.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  '[ "${1:-}" = s3api ] && [ "${2:-}" = get-object ] || { echo "Unexpected fake AWS invocation: $*" >&2; exit 1; }' \
  'value_after() {' \
  '  local flag=$1 index' \
  '  shift' \
  '  for ((index = 1; index <= $#; index++)); do' \
  '    if [ "${!index}" = "$flag" ]; then' \
  '      index=$((index + 1))' \
  '      printf "%s" "${!index}"' \
  '      return 0' \
  '    fi' \
  '  done' \
  '  return 1' \
  '}' \
  'region=$(value_after --region "$@")' \
  'bucket=$(value_after --bucket "$@")' \
  'key=$(value_after --key "$@")' \
  'version=$(value_after --version-id "$@")' \
  'destination=${!#}' \
  '[ "$region" = ap-southeast-5 ] && [ "$bucket" = fixture-backup-bucket ] || { echo "Archive source was not exact" >&2; exit 1; }' \
  'case "$key:$version" in' \
  '  "$FAKE_ARCHIVE_KEY:$FAKE_ARCHIVE_VERSION") printf "%s" "$FAKE_ARCHIVE_PAYLOAD" > "$destination" ;;' \
  '  "$FAKE_SIDECAR_KEY:$FAKE_SIDECAR_VERSION") printf "%s  %s\\n" "$FAKE_ARCHIVE_SHA256" "$FAKE_ARCHIVE_NAME" > "$destination" ;;' \
  '  *) echo "Archive object selector was not exact: $key:$version" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/aws"

# Capture every Docker run argv. The fake deliberately does not emulate a
# database, but inspects the temporary credential while the logical restore
# container has it mounted.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'command=${1:-}' \
  'shift || true' \
  'case "$command" in' \
  '  run)' \
  '    run_count=0' \
  '    docker_dir="$FAKE_ARTIFACT_DIR/docker"' \
  '    if [ -f "$docker_dir/run-count" ]; then read -r run_count < "$docker_dir/run-count"; fi' \
  '    run_count=$((run_count + 1))' \
  '    printf "%s\\n" "$run_count" > "$docker_dir/run-count"' \
  '    printf "%s\\0" "$@" > "$docker_dir/run-${run_count}.args"' \
  '    if [ "$run_count" -eq 2 ]; then' \
  '      args=("$@")' \
  '      temporary_mount=' \
  '      for index in "${!args[@]}"; do' \
  '        if [ "${args[$index]}" = --volume ]; then' \
  '          next_index=$((index + 1))' \
  '          [ "$next_index" -lt "${#args[@]}" ] || { echo "Volume without destination" >&2; exit 1; }' \
  '          case "${args[$next_index]}" in' \
  '            *:/run/duducar-recovery/logical-restore:ro)' \
  '              [ -z "$temporary_mount" ] || { echo "Multiple temporary credential mounts" >&2; exit 1; }' \
  '              temporary_mount=${args[$next_index]}' \
  '              ;;' \
  '          esac' \
  '        fi' \
  '      done' \
  '      [ -n "$temporary_mount" ] || { echo "Missing temporary credential mount" >&2; exit 1; }' \
  '      temporary_dir=${temporary_mount%:/run/duducar-recovery/logical-restore:ro}' \
  '      temporary_password="$temporary_dir/database-owner-password"' \
  '      [ -d "$temporary_dir" ] && [ -f "$temporary_password" ] || { echo "Temporary credential is missing" >&2; exit 1; }' \
  '      [ "$(find "$temporary_dir" -mindepth 1 -maxdepth 1 -printf . | wc -c)" -eq 1 ] || { echo "Temporary credential directory contains unexpected files" >&2; exit 1; }' \
  '      {' \
  '        printf "temporary_dir=%s\\n" "$temporary_dir"' \
  '        printf "temporary_directory_owner=%s\\n" "$(stat -c %U:%G "$temporary_dir")"' \
  '        printf "temporary_directory_mode=%s\\n" "$(stat -c %a "$temporary_dir")"' \
  '        printf "temporary_password_owner=%s\\n" "$(stat -c %U:%G "$temporary_password")"' \
  '        printf "temporary_password_mode=%s\\n" "$(stat -c %a "$temporary_password")"' \
  '        printf "original_directory_owner=%s\\n" "$(stat -c %U:%G "$(dirname "$FAKE_OWNER_PASSWORD_SOURCE")")"' \
  '        printf "original_password_owner=%s\\n" "$(stat -c %U:%G "$FAKE_OWNER_PASSWORD_SOURCE")"' \
  '        printf "original_directory_mode=%s\\n" "$(stat -c %a "$(dirname "$FAKE_OWNER_PASSWORD_SOURCE")")"' \
  '        printf "original_password_mode=%s\\n" "$(stat -c %a "$FAKE_OWNER_PASSWORD_SOURCE")"' \
  '        printf "original_password_sha256=%s\\n" "$(sha256sum "$FAKE_OWNER_PASSWORD_SOURCE" | cut -d " " -f 1)"' \
  '      } > "$FAKE_ARTIFACT_DIR/temporary-credential-state"' \
  '      if [ "${FAKE_FAIL_LOGICAL_RESTORE:-false}" = true ]; then exit 79; fi' \
  '    fi' \
  '    ;;' \
  '  exec) printf "%s\\0" "$@" >> "$FAKE_ARTIFACT_DIR/docker/exec.args" ;;' \
  '  *) echo "Unexpected fake Docker invocation: $command $*" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/docker"

chmod 0755 "$fake_bin"/*

run_restore() {
  local label=$1
  local fail_logical_restore=$2
  local artifact_dir="$test_dir/$label"
  mkdir -p "$artifact_dir/docker"
  PATH="$fake_bin:$PATH" \
    FAKE_ARCHIVE_KEY="$archive_key" \
    FAKE_ARCHIVE_NAME="$archive_name" \
    FAKE_ARCHIVE_PATH="$archive_dir/$archive_name" \
    FAKE_ARCHIVE_PAYLOAD="$archive_payload" \
    FAKE_ARCHIVE_SHA256="$archive_sha256" \
    FAKE_ARCHIVE_VERSION=fixture-archive-version-123 \
    FAKE_ARTIFACT_DIR="$artifact_dir" \
    FAKE_FAIL_LOGICAL_RESTORE="$fail_logical_restore" \
    FAKE_OWNER_PASSWORD_SOURCE="$owner_password_source" \
    FAKE_RUNTIME_DIR="$runtime_dir" \
    FAKE_SIDECAR_KEY="$sidecar_key" \
    FAKE_SIDECAR_PATH="$archive_dir/$sidecar_name" \
    FAKE_SIDECAR_VERSION=fixture-sidecar-version-123 \
    FAKE_STACK_LOG="$artifact_dir/stack.log" \
    bash "$helper" logical
}

success_dir="$test_dir/success"
output=$(run_restore success false)

[[ "$output" == *'Exact versioned archive and sidecar verified locally.'* ]] || \
  fail 'The logical helper did not verify the exact archive first.'
[[ "$output" == *'Logical archive restored into the disposable clone.'* ]] || \
  fail 'The logical helper did not complete the clone-only restore path.'

[ "$(stat -c '%a' "$archive_dir")" = 700 ] || fail 'The archive directory is not mode 0700.'
[ "$(stat -c '%a' "$archive_dir/$archive_name")" = 600 ] || fail 'The archive is not mode 0600.'
[ "$(stat -c '%a' "$archive_dir/$sidecar_name")" = 600 ] || fail 'The checksum sidecar is not mode 0600.'
assert_exact_file_argv 'archive directory install' "$success_dir/archive-install-args" \
  -d -o root -g root -m 0700 "$archive_dir"

mapfile -t stack_calls < "$success_dir/stack.log"
expected_stack_calls=(database stop database)
[ "${#stack_calls[@]}" -eq "${#expected_stack_calls[@]}" ] || fail 'Unexpected recovery stack call count.'
for index in "${!expected_stack_calls[@]}"; do
  [ "${stack_calls[$index]}" = "${expected_stack_calls[$index]}" ] || \
    fail "Unexpected recovery stack call $index: ${stack_calls[$index]}"
done

shopt -s nullglob
run_files=("$success_dir/docker"/run-*.args)
[ "${#run_files[@]}" -eq 4 ] || fail "Expected four Docker run calls, found ${#run_files[@]}"

mapfile -d '' -t catalogue_argv < "$success_dir/docker/run-1.args"
mapfile -d '' -t logical_argv < "$success_dir/docker/run-2.args"
mapfile -d '' -t migrate_argv < "$success_dir/docker/run-3.args"
mapfile -d '' -t migrate_check_argv < "$success_dir/docker/run-4.args"

success_state="$success_dir/temporary-credential-state"
success_temp_dir=$(state_value temporary_dir "$success_state")
[ -n "$success_temp_dir" ] || fail 'The logical restore did not create a temporary credential directory.'
[ "$(state_value temporary_directory_owner "$success_state")" = root:root ] || fail 'The temporary credential directory is not root-owned.'
[ "$(state_value temporary_directory_mode "$success_state")" = 700 ] || fail 'The temporary credential directory is not mode 0700.'
[ "$(state_value temporary_password_owner "$success_state")" = root:root ] || fail 'The temporary credential copy is not root-owned.'
[ "$(state_value temporary_password_mode "$success_state")" = 400 ] || fail 'The temporary credential copy is not mode 0400.'
[ "$(state_value original_directory_owner "$success_state")" = 10001:10001 ] || fail 'The original owner-password directory ownership changed.'
[ "$(state_value original_directory_mode "$success_state")" = 500 ] || fail 'The original owner-password directory mode changed.'
[ "$(state_value original_password_owner "$success_state")" = 10001:10001 ] || fail 'The original owner-password file ownership changed.'
[ "$(state_value original_password_mode "$success_state")" = 400 ] || fail 'The original owner-password file mode changed.'
[ "$(state_value original_password_sha256 "$success_state")" = "$original_password_sha256" ] || fail 'The original owner-password content changed.'
assert_exact_file_argv 'temporary owner-password directory ownership' "$success_dir/temporary-owner-dir-chown-args" \
  root:root "$success_temp_dir"
assert_exact_file_argv 'temporary owner-password copy' "$success_dir/password-copy-args" \
  -o root -g root -m 0400 "$owner_password_source" "$success_temp_dir/database-owner-password"
[ ! -e "$success_temp_dir" ] || fail 'The temporary credential directory remained after a successful restore.'
grep -Fq -- "-rf -- $success_temp_dir" "$success_dir/rm.log" || fail 'Successful restore did not remove its temporary credential directory.'
if grep -Fq -- "$owner_password_source" "$success_dir/temporary-owner-dir-chown-args" "$success_dir/chmod.log" "$success_dir/rm.log"; then
  fail 'The successful restore changed ownership, mode, or cleanup state of the original password.'
fi
assert_original_password_unchanged

for reader in catalogue logical; do
  if [ "$reader" = catalogue ]; then
    reader_args=("${catalogue_argv[@]}")
  else
    reader_args=("${logical_argv[@]}")
  fi
  assert_arg_count "$reader pg_restore" --user 1 "${reader_args[@]}"
  assert_arg_value "$reader pg_restore" --user 0:0 "${reader_args[@]}"
  assert_arg_count "$reader pg_restore" --network 1 "${reader_args[@]}"
  assert_arg_count "$reader pg_restore" --read-only 1 "${reader_args[@]}"
  assert_arg_value "$reader pg_restore" --cap-drop ALL "${reader_args[@]}"
  assert_arg_value "$reader pg_restore" --security-opt no-new-privileges:true "${reader_args[@]}"
  assert_arg_value "$reader pg_restore" --pids-limit 256 "${reader_args[@]}"
  assert_arg_value "$reader pg_restore" --memory 768m "${reader_args[@]}"
  assert_arg_value "$reader pg_restore" --cpus 1.5 "${reader_args[@]}"
  assert_arg_value "$reader pg_restore" --volume "$archive_dir:/backups:ro" "${reader_args[@]}"
  assert_no_arg "$reader pg_restore" --privileged "${reader_args[@]}"
  assert_no_arg "$reader pg_restore" --cap-add "${reader_args[@]}"
  assert_no_arg "$reader pg_restore" --env-file "${reader_args[@]}"
  assert_no_arg_containing "$reader pg_restore" backend-secrets "${reader_args[@]}"
  assert_no_arg_containing "$reader pg_restore" docker.sock "${reader_args[@]}"
  assert_no_arg_containing "$reader pg_restore" /srv/duducar "${reader_args[@]}"
done

assert_arg_value 'catalogue pg_restore' --network none "${catalogue_argv[@]}"
assert_arg_value 'catalogue pg_restore' --entrypoint pg_restore "${catalogue_argv[@]}"
assert_arg_value 'catalogue pg_restore' --list "/backups/$archive_name" "${catalogue_argv[@]}"
assert_arg_count 'catalogue pg_restore' --volume 1 "${catalogue_argv[@]}"
assert_no_arg_containing 'catalogue pg_restore' "$runtime_dir/database-owner" "${catalogue_argv[@]}"
assert_no_arg_containing 'catalogue pg_restore' :/run/duducar-recovery/logical-restore "${catalogue_argv[@]}"
assert_arg_value 'logical pg_restore' --network duducar-recovery "${logical_argv[@]}"
assert_arg_value 'logical pg_restore' --entrypoint /bin/sh "${logical_argv[@]}"
assert_arg_count 'logical pg_restore' --volume 2 "${logical_argv[@]}"
assert_arg_value 'logical pg_restore' --env AWS_EC2_METADATA_DISABLED=true "${logical_argv[@]}"
assert_arg_value 'logical pg_restore' --volume "$success_temp_dir:/run/duducar-recovery/logical-restore:ro" "${logical_argv[@]}"
assert_no_arg_containing 'logical pg_restore' "$runtime_dir/database-owner" "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' 'exec pg_restore' "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' --exit-on-error "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' --single-transaction "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' --no-owner "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' --no-privileges "${logical_argv[@]}"
assert_arg_count 'Django migrate' --user 0 "${migrate_argv[@]}"
assert_arg_count 'Django migrate check' --user 0 "${migrate_check_argv[@]}"
assert_no_arg_containing 'Django migrate' :/run/duducar-recovery/logical-restore "${migrate_argv[@]}"
assert_no_arg_containing 'Django migrate check' :/run/duducar-recovery/logical-restore "${migrate_check_argv[@]}"

failure_dir="$test_dir/failure"
failure_output=''
if failure_output=$(run_restore failure true 2>&1); then
  fail 'The forced logical pg_restore failure unexpectedly succeeded.'
else
  failure_status=$?
fi
[ "$failure_status" -eq 79 ] || fail "Forced logical pg_restore failure returned $failure_status instead of 79."

failure_state="$failure_dir/temporary-credential-state"
failure_temp_dir=$(state_value temporary_dir "$failure_state")
[ -n "$failure_temp_dir" ] || fail 'The failed logical restore did not create a temporary credential directory.'
[ "$(state_value temporary_directory_owner "$failure_state")" = root:root ] || fail 'The failed restore temporary directory is not root-owned.'
[ "$(state_value temporary_directory_mode "$failure_state")" = 700 ] || fail 'The failed restore temporary directory is not mode 0700.'
[ "$(state_value temporary_password_owner "$failure_state")" = root:root ] || fail 'The failed restore temporary password is not root-owned.'
[ "$(state_value temporary_password_mode "$failure_state")" = 400 ] || fail 'The failed restore temporary password is not mode 0400.'
[ "$(state_value original_directory_owner "$failure_state")" = 10001:10001 ] || fail 'The failed restore changed original directory ownership.'
[ "$(state_value original_directory_mode "$failure_state")" = 500 ] || fail 'The failed restore changed original directory mode.'
[ "$(state_value original_password_owner "$failure_state")" = 10001:10001 ] || fail 'The failed restore changed original password ownership.'
[ "$(state_value original_password_mode "$failure_state")" = 400 ] || fail 'The failed restore changed original password mode.'
[ "$(state_value original_password_sha256 "$failure_state")" = "$original_password_sha256" ] || fail 'The failed restore changed original password content.'
assert_exact_file_argv 'failed temporary owner-password directory ownership' "$failure_dir/temporary-owner-dir-chown-args" \
  root:root "$failure_temp_dir"
assert_exact_file_argv 'failed temporary owner-password copy' "$failure_dir/password-copy-args" \
  -o root -g root -m 0400 "$owner_password_source" "$failure_temp_dir/database-owner-password"
[ ! -e "$failure_temp_dir" ] || fail 'The temporary credential directory remained after a failed restore.'
grep -Fq -- "-rf -- $failure_temp_dir" "$failure_dir/rm.log" || fail 'Failed restore did not remove its temporary credential directory.'
if grep -Fq -- "$owner_password_source" "$failure_dir/temporary-owner-dir-chown-args" "$failure_dir/chmod.log" "$failure_dir/rm.log"; then
  fail 'The failed restore changed ownership, mode, or cleanup state of the original password.'
fi
assert_original_password_unchanged

failure_run_files=("$failure_dir/docker"/run-*.args)
[ "${#failure_run_files[@]}" -eq 2 ] || fail "Expected two Docker run calls before forced failure, found ${#failure_run_files[@]}"
mapfile -d '' -t failure_catalogue_argv < "$failure_dir/docker/run-1.args"
mapfile -d '' -t failure_logical_argv < "$failure_dir/docker/run-2.args"
assert_no_arg 'failed catalogue pg_restore' --env-file "${failure_catalogue_argv[@]}"
assert_no_arg_containing 'failed catalogue pg_restore' "$runtime_dir/database-owner" "${failure_catalogue_argv[@]}"
assert_no_arg_containing 'failed catalogue pg_restore' :/run/duducar-recovery/logical-restore "${failure_catalogue_argv[@]}"
assert_arg_value 'failed logical pg_restore' --volume "$failure_temp_dir:/run/duducar-recovery/logical-restore:ro" "${failure_logical_argv[@]}"
assert_no_arg 'failed logical pg_restore' --env-file "${failure_logical_argv[@]}"
assert_no_arg_containing 'failed logical pg_restore' "$runtime_dir/database-owner" "${failure_logical_argv[@]}"
assert_arg_value 'failed logical pg_restore' --user 0:0 "${failure_logical_argv[@]}"
assert_arg_value 'failed logical pg_restore' --cap-drop ALL "${failure_logical_argv[@]}"
assert_arg_value 'failed logical pg_restore' --security-opt no-new-privileges:true "${failure_logical_argv[@]}"
assert_arg_value 'failed logical pg_restore' --network duducar-recovery "${failure_logical_argv[@]}"
assert_arg_value 'failed logical pg_restore' --env AWS_EC2_METADATA_DISABLED=true "${failure_logical_argv[@]}"

echo 'Recovery logical archive permission and containment check passed.'

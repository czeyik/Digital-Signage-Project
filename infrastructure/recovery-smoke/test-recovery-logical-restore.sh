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
install_args="$test_dir/install-args"
stack_log="$test_dir/stack.log"
docker_dir="$test_dir/docker"
archive_name=fixture.dump
sidecar_name=fixture.dump.sha256
archive_key="database-backups/$archive_name"
sidecar_key="database-backups/$sidecar_name"
archive_payload='fixture logical PostgreSQL archive'
archive_sha256=$(printf '%s' "$archive_payload" | sha256sum | awk '{print $1}')
backend_image='registry.example.invalid/duducar-backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'

cleanup() {
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

mkdir -p "$fake_bin" "$runtime_dir/database-owner" "$docker_dir"
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
printf 'fixture database-owner password\n' > "$runtime_dir/database-owner/database-owner-password"

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
# paths. The test checks the exact install argv and the real mode bits below.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'if [ "${1:-}" = -d ]; then' \
  '  [ "$#" -eq 8 ] && [ "${2:-}" = -o ] && [ "${3:-}" = root ] && [ "${4:-}" = -g ] && [ "${5:-}" = root ] && [ "${6:-}" = -m ] && [ "${7:-}" = 0700 ] || { echo "Archive directory must stay root:root 0700" >&2; exit 1; }' \
  '  printf "%s\\0" "$@" > "$FAKE_INSTALL_ARGS"' \
  '  mkdir -p -- "${!#}"' \
  '  chmod 0700 "${!#}"' \
  '  exit 0' \
  'fi' \
  'exec /usr/bin/install "$@"' > "$fake_bin/install"

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
# database: this check is about the containment and identity of the archive
# reader before a real recovery host is allowed to restore data.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'command=${1:-}' \
  'shift || true' \
  'case "$command" in' \
  '  run)' \
  '    run_count=0' \
  '    if [ -f "$FAKE_DOCKER_DIR/run-count" ]; then read -r run_count < "$FAKE_DOCKER_DIR/run-count"; fi' \
  '    run_count=$((run_count + 1))' \
  '    printf "%s\\n" "$run_count" > "$FAKE_DOCKER_DIR/run-count"' \
  '    printf "%s\\0" "$@" > "$FAKE_DOCKER_DIR/run-${run_count}.args"' \
  '    ;;' \
  '  exec) printf "%s\\0" "$@" >> "$FAKE_DOCKER_DIR/exec.args" ;;' \
  '  *) echo "Unexpected fake Docker invocation: $command $*" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/docker"

chmod 0755 "$fake_bin"/*

output=$(PATH="$fake_bin:$PATH" \
  FAKE_ARCHIVE_KEY="$archive_key" \
  FAKE_ARCHIVE_NAME="$archive_name" \
  FAKE_ARCHIVE_PAYLOAD="$archive_payload" \
  FAKE_ARCHIVE_SHA256="$archive_sha256" \
  FAKE_ARCHIVE_VERSION=fixture-archive-version-123 \
  FAKE_DOCKER_DIR="$docker_dir" \
  FAKE_INSTALL_ARGS="$install_args" \
  FAKE_SIDECAR_KEY="$sidecar_key" \
  FAKE_SIDECAR_VERSION=fixture-sidecar-version-123 \
  FAKE_STACK_LOG="$stack_log" \
  bash "$helper" logical)

[[ "$output" == *'Exact versioned archive and sidecar verified locally.'* ]] || \
  fail 'The logical helper did not verify the exact archive first.'
[[ "$output" == *'Logical archive restored into the disposable clone.'* ]] || \
  fail 'The logical helper did not complete the clone-only restore path.'

[ "$(stat -c '%a' "$archive_dir")" = 700 ] || fail 'The archive directory is not mode 0700.'
[ "$(stat -c '%a' "$archive_dir/$archive_name")" = 600 ] || fail 'The archive is not mode 0600.'
[ "$(stat -c '%a' "$archive_dir/$sidecar_name")" = 600 ] || fail 'The checksum sidecar is not mode 0600.'
assert_exact_file_argv 'archive directory install' "$install_args" \
  -d -o root -g root -m 0700 "$archive_dir"

mapfile -t stack_calls < "$stack_log"
expected_stack_calls=(database stop database)
[ "${#stack_calls[@]}" -eq "${#expected_stack_calls[@]}" ] || fail 'Unexpected recovery stack call count.'
for index in "${!expected_stack_calls[@]}"; do
  [ "${stack_calls[$index]}" = "${expected_stack_calls[$index]}" ] || \
    fail "Unexpected recovery stack call $index: ${stack_calls[$index]}"
done

shopt -s nullglob
run_files=("$docker_dir"/run-*.args)
[ "${#run_files[@]}" -eq 4 ] || fail "Expected four Docker run calls, found ${#run_files[@]}"

mapfile -d '' -t catalogue_argv < "$docker_dir/run-1.args"
mapfile -d '' -t logical_argv < "$docker_dir/run-2.args"
mapfile -d '' -t migrate_argv < "$docker_dir/run-3.args"
mapfile -d '' -t migrate_check_argv < "$docker_dir/run-4.args"

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
assert_arg_value 'logical pg_restore' --network duducar-recovery "${logical_argv[@]}"
assert_arg_value 'logical pg_restore' --entrypoint /bin/sh "${logical_argv[@]}"
assert_arg_count 'logical pg_restore' --volume 2 "${logical_argv[@]}"
assert_arg_value 'logical pg_restore' --env AWS_EC2_METADATA_DISABLED=true "${logical_argv[@]}"
assert_arg_value 'logical pg_restore' --volume "$runtime_dir/database-owner:/run/duducar-recovery/database-owner:ro" "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' 'exec pg_restore' "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' --exit-on-error "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' --single-transaction "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' --no-owner "${logical_argv[@]}"
assert_arg_containing 'logical pg_restore' --no-privileges "${logical_argv[@]}"
assert_arg_count 'Django migrate' --user 0 "${migrate_argv[@]}"
assert_arg_count 'Django migrate check' --user 0 "${migrate_check_argv[@]}"

echo 'Recovery logical archive permission and containment check passed.'

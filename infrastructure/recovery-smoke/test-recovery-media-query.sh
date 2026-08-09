#!/bin/bash
set -euo pipefail
umask 0077

# Exercise the media-proof database query without AWS, Docker, or a mounted
# clone. In particular, keep the Python command attached to `docker run`: a
# shell comment after a continued image line otherwise makes Docker receive
# only the image/default CMD and runs no metadata query in the container.
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
test_dir=$(mktemp -d /tmp/duducar-recovery-media-query.XXXXXX)
fake_bin="$test_dir/bin"
helper="$test_dir/duducar-recovery-restore"
host_config="$test_dir/host.env"
release_config="$test_dir/release.env"
runtime_dir="$test_dir/runtime"
archive_dir="$test_dir/archives"
docker_args="$test_dir/docker-args"
stack_log="$test_dir/stack.log"
aws_log="$test_dir/aws.log"
media_payload='fixture normalized media bytes'
source_media_key='validated/fixture-normalized-media.png'
source_media_version='fixture-media-version-123'
backend_image='registry.example.invalid/duducar-backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
source_media_sha256=$(printf '%s' "$media_payload" | sha256sum | awk '{print $1}')
source_media_size=$(printf '%s' "$media_payload" | wc -c | tr -d '[:space:]')

cleanup() {
  rm -rf -- "$test_dir"
}
trap cleanup EXIT

fail() {
  echo "$*" >&2
  exit 1
}

assert_contains() {
  local literal=$1
  local file=$2
  grep -Fq -- "$literal" "$file" || fail "Missing expected value '$literal' in $file"
}

mkdir -p "$fake_bin" "$runtime_dir"
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
  'SOURCE_ARCHIVE_KEY=database-backups/fixture.dump' \
  'SOURCE_SIDECAR_KEY=database-backups/fixture.dump.sha256' \
  "SOURCE_MEDIA_KEY=$source_media_key" \
  "SOURCE_MEDIA_VERSION_ID=$source_media_version" \
  "SOURCE_MEDIA_SHA256=$source_media_sha256" \
  "SOURCE_MEDIA_SIZE_BYTES=$source_media_size" > "$host_config"

printf '%s\n' \
  "BACKEND_IMAGE=$backend_image" \
  'AWS_STORAGE_BUCKET_NAME=fixture-media-bucket' > "$release_config"
printf 'fixture application environment\n' > "$runtime_dir/application.env"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  '[ "${1:-}" = verify-mounted ] || { echo "Unexpected mount helper invocation: $*" >&2; exit 1; }' > "$fake_bin/duducar-recovery-mount"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  '[ "${1:-}" = database ] || { echo "Unexpected stack helper invocation: $*" >&2; exit 1; }' \
  'printf "%s\\n" "$1" >> "$FAKE_STACK_LOG"' > "$fake_bin/duducar-recovery-stack"

# The real helper installs a root-owned archive directory. Model that one
# behavior without requiring the CI user to own root-owned fixture paths.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'if [ "${1:-}" = -d ]; then' \
  '  target=${!#}' \
  '  mkdir -p -- "$target"' \
  '  chmod 0700 "$target"' \
  '  exit 0' \
  'fi' \
  'exec /usr/bin/install "$@"' > "$fake_bin/install"

# The media command has exactly one S3 get-object call. Verify its immutable
# source selectors before materializing harmless fixture bytes at its target.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  '[ "${1:-}" = s3api ] && [ "${2:-}" = get-object ] || { echo "Unexpected fake AWS invocation: $*" >&2; exit 1; }' \
  'printf "%s\\n" "$*" >> "$FAKE_AWS_LOG"' \
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
  '[ "$region" = "$FAKE_AWS_REGION" ] && [ "$bucket" = "$FAKE_AWS_BUCKET" ] && [ "$key" = "$FAKE_MEDIA_KEY" ] && [ "$version" = "$FAKE_MEDIA_VERSION" ] || { echo "Media download selectors were not exact" >&2; exit 1; }' \
  'destination=${!#}' \
  'printf "%s" "$FAKE_MEDIA_PAYLOAD" > "$destination"' > "$fake_bin/aws"

# Capture the complete Docker argv as NUL-delimited records, then return only
# the one JSON document the real jq metadata proof expects. The fake exits
# before output if the image is not followed by the exact Django shell command.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  '[ "${1:-}" = run ] || { echo "Unexpected fake Docker invocation: $*" >&2; exit 1; }' \
  'shift' \
  'args=("$@")' \
  'printf "%s\\0" "${args[@]}" > "$FAKE_DOCKER_ARGS"' \
  'image_index=-1' \
  'for index in "${!args[@]}"; do' \
  '  if [ "${args[$index]}" = "$FAKE_BACKEND_IMAGE" ]; then' \
  '    [ "$image_index" -eq -1 ] || { echo "Backend image appeared more than once" >&2; exit 1; }' \
  '    image_index=$index' \
  '  fi' \
  'done' \
  '[ "$image_index" -ge 0 ] || { echo "Backend image was not passed to Docker" >&2; exit 1; }' \
  'command=("${args[@]:$((image_index + 1))}")' \
  'expected=(python manage.py shell --no-imports -c)' \
  '[ "${#command[@]}" -eq 6 ] || { echo "Docker did not receive the complete media metadata command" >&2; exit 1; }' \
  'for index in "${!expected[@]}"; do' \
  '  [ "${command[$index]}" = "${expected[$index]}" ] || { echo "Unexpected Docker command argument ${index}: ${command[$index]}" >&2; exit 1; }' \
  'done' \
  '[[ "${command[5]}" == *"from signage.models import MediaAsset"* ]] || { echo "Docker did not receive the MediaAsset query" >&2; exit 1; }' \
  'printf "{\\\"file_size\\\":%s,\\\"normalized_file\\\":\\\"%s\\\",\\\"sha256\\\":\\\"%s\\\"}\\n" "$FAKE_MEDIA_SIZE" "$FAKE_MEDIA_KEY" "$FAKE_MEDIA_SHA256"' > "$fake_bin/docker"

# A continued-line comment regression would otherwise continue as a host
# Python command after Docker saw only the image/default command.
printf '%s\n' \
  '#!/bin/bash' \
  'echo "Unexpected host Python invocation: $*" >&2' \
  'exit 97' > "$fake_bin/python"

chmod 0755 "$fake_bin"/*

output=$(PATH="$fake_bin:$PATH" \
  FAKE_AWS_LOG="$aws_log" \
  FAKE_AWS_REGION=ap-southeast-5 \
  FAKE_AWS_BUCKET=fixture-media-bucket \
  FAKE_BACKEND_IMAGE="$backend_image" \
  FAKE_DOCKER_ARGS="$docker_args" \
  FAKE_MEDIA_KEY="$source_media_key" \
  FAKE_MEDIA_PAYLOAD="$media_payload" \
  FAKE_MEDIA_SHA256="$source_media_sha256" \
  FAKE_MEDIA_SIZE="$source_media_size" \
  FAKE_MEDIA_VERSION="$source_media_version" \
  FAKE_STACK_LOG="$stack_log" \
  bash "$helper" media)

assert_contains 'Exact versioned normalized media matches preflight evidence and the restored MediaAsset key, SHA-256, and file size.' <(printf '%s\n' "$output")
assert_contains 'database' "$stack_log"
assert_contains "--version-id $source_media_version" "$aws_log"

mapfile -d '' -t docker_argv < "$docker_args"
image_index=-1
for index in "${!docker_argv[@]}"; do
  if [ "${docker_argv[$index]}" = "$backend_image" ]; then
    image_index=$index
    break
  fi
done
[ "$image_index" -ge 0 ] || fail 'The metadata query did not reach Docker.'
docker_command=("${docker_argv[@]:$((image_index + 1))}")
expected_command=(python manage.py shell --no-imports -c)
[ "${#docker_command[@]}" -eq 6 ] || fail 'Docker did not receive exactly the metadata query command.'
for index in "${!expected_command[@]}"; do
  [ "${docker_command[$index]}" = "${expected_command[$index]}" ] || fail "Docker command argument $index did not match the media query."
done
[[ "${docker_command[5]}" == *'from signage.models import MediaAsset'* ]] || fail 'The Docker command was missing its MediaAsset query program.'

echo 'Recovery media metadata-query command check passed.'

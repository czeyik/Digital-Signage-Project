#!/usr/bin/env bash
# Exercise the root-only worker handoff without mutating the host's real /tmp.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
scratch_dir=$(mktemp -d)
fake_bin="$scratch_dir/bin"
log_path="$scratch_dir/log"
expected_log="$scratch_dir/expected-log"
readonly repo_root scratch_dir fake_bin log_path expected_log

cleanup() {
  rm -rf -- "$scratch_dir"
}
trap cleanup EXIT

grep -Eq 'apk add --no-cache .*su-exec' "$repo_root/backend/Dockerfile"
grep -Fxq 'USER appuser' "$repo_root/backend/Dockerfile"

mkdir "$fake_bin"

write_fake() {
  local name=$1
  shift
  printf '%s\n' "$@" > "$fake_bin/$name"
  chmod 700 "$fake_bin/$name"
}

write_fake id \
  '#!/bin/sh' \
  'test "$1" = "-u"' \
  'printf "%s\n" "$FAKE_UID"'
write_fake chown \
  '#!/bin/sh' \
  'test "$#" -eq 3' \
  'test "$1" = "10001:10001"' \
  'test "$2" = "/var/lib/clamav"' \
  'test "$3" = "/tmp"' \
  'printf "%s\n" chown >> "$WORKER_ROOT_INIT_LOG"'
write_fake chmod \
  '#!/bin/sh' \
  'test "$#" -eq 3' \
  'test "$1" = "0700"' \
  'test "$2" = "/var/lib/clamav"' \
  'test "$3" = "/tmp"' \
  'printf "%s\n" chmod >> "$WORKER_ROOT_INIT_LOG"'
write_fake su-exec \
  '#!/bin/sh' \
  'test "$1" = "10001:10001"' \
  'shift' \
  'printf "%s\n" su-exec >> "$WORKER_ROOT_INIT_LOG"' \
  'exec "$@"'
write_fake timeout \
  '#!/bin/sh' \
  'test "$#" -ge 2' \
  'shift' \
  'exec "$@"'
write_fake freshclam \
  '#!/bin/sh' \
  'test "$#" -eq 2' \
  'test "$1" = "--datadir=/var/lib/clamav"' \
  'test "$2" = "--stdout"' \
  'printf "%s\n" freshclam >> "$WORKER_ROOT_INIT_LOG"'
write_fake python \
  '#!/bin/sh' \
  'test "$#" -eq 4' \
  'test "$1" = "manage.py"' \
  'test "$2" = "process_media"' \
  'test "$3" = "--asset-id"' \
  'test "$4" = "11111111-1111-1111-1111-111111111111"' \
  'printf "%s\n" python >> "$WORKER_ROOT_INIT_LOG"'

(
  cd "$repo_root/backend"
  PATH="$fake_bin:$PATH" \
    WORKER_ROOT_INIT_LOG="$log_path" \
    FAKE_UID=0 \
    WORKER_ROOT_INIT=1 \
    sh ./worker-entrypoint-root-init.sh \
      --asset-id 11111111-1111-1111-1111-111111111111
)

printf '%s\n' chown chmod su-exec freshclam python > "$expected_log"
cmp -s "$expected_log" "$log_path"

: > "$log_path"
if (
  cd "$repo_root/backend"
  PATH="$fake_bin:$PATH" \
    WORKER_ROOT_INIT_LOG="$log_path" \
    FAKE_UID=0 \
    sh ./worker-entrypoint-root-init.sh \
      --asset-id 11111111-1111-1111-1111-111111111111
) >/dev/null 2>&1; then
  echo "worker init ran without its explicit task opt-in" >&2
  exit 1
fi
test ! -s "$log_path"

if (
  cd "$repo_root/backend"
  PATH="$fake_bin:$PATH" \
    WORKER_ROOT_INIT_LOG="$log_path" \
    FAKE_UID=0 \
    WORKER_ROOT_INIT=0 \
    sh ./worker-entrypoint-root-init.sh \
      --asset-id 11111111-1111-1111-1111-111111111111
) >/dev/null 2>&1; then
  echo "worker init accepted a non-opt-in task value" >&2
  exit 1
fi
test ! -s "$log_path"

if (
  cd "$repo_root/backend"
  PATH="$fake_bin:$PATH" \
    WORKER_ROOT_INIT_LOG="$log_path" \
    FAKE_UID=10001 \
    WORKER_ROOT_INIT=1 \
    sh ./worker-entrypoint-root-init.sh \
      --asset-id 11111111-1111-1111-1111-111111111111
) >/dev/null 2>&1; then
  echo "non-root worker init unexpectedly succeeded" >&2
  exit 1
fi
test ! -s "$log_path"

if (
  cd "$repo_root/backend"
  PATH="$fake_bin:$PATH" \
    WORKER_ROOT_INIT_LOG="$log_path" \
    FAKE_UID=0 \
    WORKER_ROOT_INIT=1 \
    sh ./worker-entrypoint-root-init.sh
) >/dev/null 2>&1; then
  echo "worker init accepted missing asset arguments" >&2
  exit 1
fi
test ! -s "$log_path"

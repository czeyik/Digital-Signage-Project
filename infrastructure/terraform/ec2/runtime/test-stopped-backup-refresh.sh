#!/bin/bash
set -Eeuo pipefail
umask 0077

runtime_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
command=$runtime_dir/duducar-command
test_root=$(mktemp -d /tmp/duducar-backup-refresh-test.XXXXXX)
fake_bin=$(mktemp -d /tmp/duducar-backup-refresh-tools.XXXXXX)
runner_state=$test_root/runner.state
docker_log=$test_root/docker.log
verifier_log=$test_root/verifier.log
receipt=$test_root/receipt
operation_id=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
backend_image=example.test/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

cleanup() {
  status=$?
  rm -rf -- "$test_root" "$fake_bin"
  exit "$status"
}
trap cleanup EXIT

install -d -m 0755 \
  "$test_root/etc/duducar" \
  "$test_root/run/duducar/backend-secrets" \
  "$test_root/srv/duducar/backups" \
  "$test_root/usr/local/sbin"
cat > "$test_root/etc/duducar/host.env" <<'EOF'
AWS_REGION=ap-southeast-5
PILOT_BACKUP_S3_BUCKET=backup-bucket
EOF
cat > "$test_root/etc/duducar/release.env" <<EOF
BACKEND_IMAGE=$backend_image
EOF
: > "$test_root/run/duducar/application.env"
chmod 0600 "$test_root/etc/duducar/host.env" "$test_root/etc/duducar/release.env"

cat > "$fake_bin/docker" <<'EOF'
#!/bin/bash
set -Eeuo pipefail
printf '%s\n' "$*" >> "$DUDUCAR_TEST_DOCKER_LOG"
case "${1:-} ${2:-}" in
  'container inspect')
    name=${3:-}
    [ "$name" = "$DUDUCAR_TEST_BACKUP_CONTAINER" ] && [ -e "$DUDUCAR_TEST_RUNNER_STATE" ]
    ;;
  'run --rm')
    case " $* " in
      *" --name $DUDUCAR_TEST_BACKUP_CONTAINER "*) ;;
      *) echo "Backup runner name was not operation-correlated." >&2; exit 1 ;;
    esac
    case " $* " in
      *' --network duducar '*) ;;
      *) echo "Backup runner must use the private Docker network." >&2; exit 1 ;;
    esac
    case " $* " in
      *' --publish '*) echo "Backup runner must not publish a port." >&2; exit 1 ;;
    esac
    case " $* " in
      *' duducar-web '*) echo "Backup refresh must not start the web service." >&2; exit 1 ;;
    esac
    case " $* " in
      *' --env DEPLOYMENT_COMPONENT=scheduled '*) ;;
      *) echo "Backup runner must identify as scheduled, not web." >&2; exit 1 ;;
    esac
    : > "$DUDUCAR_TEST_RUNNER_STATE"
    if [ "${DUDUCAR_BACKUP_REFRESH_FAIL:-0}" = 1 ]; then
      exit 1
    fi
    rm -f "$DUDUCAR_TEST_RUNNER_STATE"
    ;;
  'rm -f')
    rm -f "$DUDUCAR_TEST_RUNNER_STATE"
    ;;
  *) echo "Unexpected fake Docker command: $*" >&2; exit 1 ;;
esac
EOF

cat > "$test_root/usr/local/sbin/duducar-backup-verify" <<'EOF'
#!/bin/bash
set -Eeuo pipefail
printf '%s:%s\n' "$1" "${DUDUCAR_BACKUP_OPERATION_ID:-}" >> "$DUDUCAR_TEST_VERIFIER_LOG"
case "$1" in
  record)
    if printenv DUDUCAR_BACKUP_ASSUME_ROLE >/dev/null 2>&1; then
      echo "Refresh receipt recording must use the running credential broker." >&2
      exit 1
    fi
    [ "${DUDUCAR_BACKUP_OPERATION_ID:-}" = "$DUDUCAR_TEST_EXPECTED_OPERATION" ]
    [ "${DUDUCAR_BACKUP_RECORD_FAIL:-0}" = 1 ] && exit 1
    printf '%s\n' "${DUDUCAR_BACKUP_OPERATION_ID}" > "$DUDUCAR_TEST_RECEIPT"
    ;;
  *) echo "Unexpected verifier mode: $1" >&2; exit 1 ;;
esac
EOF
chmod 0755 "$fake_bin/docker" "$test_root/usr/local/sbin/duducar-backup-verify"

export DUDUCAR_COMMAND_TEST_ROOT=$test_root
export DUDUCAR_TEST_DOCKER_LOG=$docker_log
export DUDUCAR_TEST_RUNNER_STATE=$runner_state
export DUDUCAR_TEST_BACKUP_CONTAINER=duducar-recovery-backup-$operation_id
export DUDUCAR_TEST_VERIFIER_LOG=$verifier_log
export DUDUCAR_TEST_RECEIPT=$receipt
export DUDUCAR_TEST_EXPECTED_OPERATION=$operation_id
export PATH=$fake_bin:$PATH

if bash "$command" backup-refresh not-an-operation; then
  echo "Backup refresh accepted an invalid operation ID." >&2
  exit 1
fi

if DUDUCAR_BACKUP_REFRESH_FAIL=1 bash "$command" backup-refresh "$operation_id"; then
  echo "Backup refresh accepted a failed backup runner." >&2
  exit 1
fi
test ! -e "$runner_state"

bash "$command" backup-refresh "$operation_id"
test "$(cat "$receipt")" = "$operation_id"
grep -Fq "record:$operation_id" "$verifier_log"
grep -Fq -- "--network duducar" "$docker_log"
! grep -Fq -- '--publish' "$docker_log"
! grep -Fq 'duducar-web' "$docker_log"
! grep -Fq 'duducar-caddy' "$docker_log"

: > "$runner_state"
if bash "$command" backup-refresh "$operation_id"; then
  echo "Backup refresh reused a pre-existing operation runner." >&2
  exit 1
fi
test -e "$runner_state"

echo "Stopped-state backup runner failure, retry, receipt, and no-public-traffic checks passed."

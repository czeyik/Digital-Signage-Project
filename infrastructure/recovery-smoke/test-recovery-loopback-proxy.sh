#!/bin/bash
set -euo pipefail
umask 0077

# Exercise the recovery-only loopback proxy without Docker, systemd, AWS, or a
# mounted clone. The copied stack helper sees a static internal Caddy and fake
# systemd/ss state so this test verifies start/stop ordering, the exact one
# loopback listener, receipt validation, and rejection of broad or extra
# listeners before a real recovery host is needed.
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
test_dir=$(mktemp -d /tmp/duducar-recovery-loopback-proxy.XXXXXX)
fake_bin="$test_dir/bin"
helper="$test_dir/duducar-recovery-stack"
host_config="$test_dir/host.env"
release_config="$test_dir/release.env"
runtime_dir="$test_dir/runtime"
socket_unit="$test_dir/duducar-recovery-loopback-proxy.socket"
service_unit="$test_dir/duducar-recovery-loopback-proxy.service"
receipt="$runtime_dir/loopback-proxy-receipt"
socket_state="$test_dir/socket-active"
residual_listener_state="$test_dir/residual-listener"
systemctl_log="$test_dir/systemctl.log"
docker_log="$test_dir/docker.log"
port=18449
operation_id=0123456789abcdef0123456789abcdef

cleanup() {
  rm -rf -- "$test_dir"
}
trap cleanup EXIT

fail() {
  echo "$*" >&2
  exit 1
}

assert_absent() {
  [ ! -e "$1" ] || fail "Unexpected path remains: $1"
}

assert_contains() {
  local literal=$1
  local file=$2
  grep -Fq -- "$literal" "$file" || fail "Missing expected value '$literal' in $file"
}

assert_not_contains() {
  local literal=$1
  local file=$2
  if grep -Fq -- "$literal" "$file"; then
    fail "Unexpected value '$literal' in $file"
  fi
}

assert_before() {
  local first=$1
  local second=$2
  local file=$3
  local first_line
  local second_line
  first_line=$(grep -Fnm1 -- "$first" "$file" | cut -d: -f1)
  second_line=$(grep -Fnm1 -- "$second" "$file" | cut -d: -f1)
  [ "$first_line" -lt "$second_line" ] || fail "Expected '$first' before '$second' in $file"
}

expect_failure() {
  local output
  local status
  if output=$("$@" 2>&1); then
    fail "Expected command to fail: $*"
  else
    status=$?
  fi
  if [ "$status" -ne 1 ]; then
    printf '%s\n' "$output" >&2
    fail "Expected failure status 1, got $status: $*"
  fi
}

mkdir -p "$fake_bin" "$runtime_dir"
cp "$root_dir/runtime/duducar-recovery-stack" "$helper"
sed -i \
  -e "s|^host_config=.*$|host_config=$host_config|" \
  -e "s|^release_config=.*$|release_config=$release_config|" \
  -e "s|^runtime_dir=.*$|runtime_dir=$runtime_dir|" \
  -e "s|^loopback_proxy_socket_unit=.*$|loopback_proxy_socket_unit=$socket_unit|" \
  -e "s|^loopback_proxy_service_unit=.*$|loopback_proxy_service_unit=$service_unit|" \
  -e "s|^loopback_proxy_receipt=.*$|loopback_proxy_receipt=$receipt|" \
  "$helper"
# The test calls the shipped functions directly instead of running a complete
# database restore. Remove only the final command dispatcher from the copy.
sed -i '/^case "${1:-}" in$/,$d' "$helper"
bash -n "$helper"

printf '%s\n' \
  'AWS_REGION=ap-southeast-5' \
  "RECOVERY_OPERATION_ID=$operation_id" \
  'RECOVERY_HOSTNAME=recovery.example.invalid' \
  "RECOVERY_CADDY_PORT=$port" > "$host_config"
printf '%s\n' \
  'BACKEND_IMAGE=registry.example.invalid/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  'POSTGRES_IMAGE=postgres@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' \
  'CADDY_IMAGE=caddy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc' > "$release_config"
sed "s/__RECOVERY_CADDY_PORT__/$port/g" \
  "$root_dir/runtime/duducar-recovery-loopback-proxy.socket" > "$socket_unit"
sed "s/__RECOVERY_CADDY_PORT__/$port/g" \
  "$root_dir/runtime/duducar-recovery-loopback-proxy.service" > "$service_unit"

# The test runs without root. These fakes model the ownership/mode contracts
# checked by the helper while the real file presence and content remain real.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'if [ "${1:-}" = -d ]; then mkdir -p -- "${!#}"; exit 0; fi' \
  'exec /usr/bin/install "$@"' > "$fake_bin/install"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'exit 0' > "$fake_bin/chown"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'if [ "${1:-}" = -c ]; then' \
  '  path=${!#}' \
  '  [ -e "$path" ] || exit 1' \
  '  case "${2:-}" in' \
  '    %U:%G) printf "%s\\n" root:root; exit 0 ;;' \
  '    %a) if [ "$path" = "$FAKE_RECEIPT" ]; then printf "%s\\n" 600; else printf "%s\\n" 644; fi; exit 0 ;;' \
  '    %U:%G:%a) if [ "$path" = "$FAKE_RECEIPT" ]; then printf "%s\\n" root:root:600; else printf "%s\\n" root:root:644; fi; exit 0 ;;' \
  '  esac' \
  'fi' \
  'exec /usr/bin/stat "$@"' > "$fake_bin/stat"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'case "${1:-}" in' \
  '  verify) exit 0 ;;' \
  '  *) echo "Unexpected systemd-analyze call: $*" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/systemd-analyze"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'command=${1:-}' \
  'unit=${!#}' \
  'printf "%s %s\\n" "$command" "$*" >> "$FAKE_SYSTEMCTL_LOG"' \
  'case "$command" in' \
  '  is-active)' \
  '    [ "$unit" = duducar-recovery-loopback-proxy.socket ] && [ -e "$FAKE_SOCKET_STATE" ] && exit 0' \
  '    exit 3' \
  '    ;;' \
  '  start)' \
  '    [ "$unit" = duducar-recovery-loopback-proxy.socket ] || { echo "Unexpected start unit: $unit" >&2; exit 1; }' \
  '    : > "$FAKE_SOCKET_STATE"' \
  '    ;;' \
  '  stop)' \
  '    : > /dev/null' \
  '    if [ -e "$FAKE_SOCKET_STATE" ]; then unlink "$FAKE_SOCKET_STATE"; fi' \
  '    ;;' \
  '  reset-failed) ;;' \
  '  *) echo "Unexpected systemctl invocation: $*" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/systemctl"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  '[ -e "$FAKE_SOCKET_STATE" ] || [ -e "$FAKE_RESIDUAL_LISTENER_STATE" ] || exit 0' \
  'case "${FAKE_SS_MODE:-good}" in' \
  "  good) printf 'LISTEN 0 4096 127.0.0.1:%s 0.0.0.0:*\\n' \"\$FAKE_CADDY_PORT\" ;;" \
  "  broad) printf 'LISTEN 0 4096 0.0.0.0:%s 0.0.0.0:*\\n' \"\$FAKE_CADDY_PORT\" ;;" \
  "  multiple) printf 'LISTEN 0 4096 127.0.0.1:%s 0.0.0.0:*\\n' \"\$FAKE_CADDY_PORT\"; printf 'LISTEN 0 4096 127.0.0.2:%s 0.0.0.0:*\\n' \"\$FAKE_CADDY_PORT\" ;;" \
  '  *) echo "Unexpected FAKE_SS_MODE: ${FAKE_SS_MODE}" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/ss"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'command=${1:-}' \
  'printf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"' \
  'format=' \
  'for ((index = 1; index <= $#; index++)); do' \
  '  if [ "${!index}" = --format ]; then index=$((index + 1)); format="${!index}"; break; fi' \
  'done' \
  'case "$command" in' \
  '  container)' \
  '    [ "${2:-}" = inspect ] && [ "${3:-}" = duducar-recovery-caddy ] && exit 0' \
  '    ;;' \
  '  inspect)' \
  '    case "$format" in' \
  "      '{{.State.Running}}') printf '%s\\n' true; exit 0 ;;" \
  "      '{{range \$name, \$network := .NetworkSettings.Networks}}{{\$name}}={{\$network.IPAddress}};{{end}}') printf '%s\\n' \"\${FAKE_CADDY_NETWORKS:-duducar-recovery=172.31.0.10;}\"; exit 0 ;;" \
  "      '{{json .HostConfig.PortBindings}}') printf '%s\\n' \"\${FAKE_CADDY_PORT_BINDINGS:-null}\"; exit 0 ;;" \
  '    esac' \
  '    ;;' \
  '  network)' \
  '    [ "${2:-}" = inspect ] || exit 1' \
  '    case "$format" in' \
  "      '{{.Internal}}') printf '%s\\n' true; exit 0 ;;" \
  "      '{{range .IPAM.Config}}{{.Subnet}}{{end}}') printf '%s\\n' 172.31.0.0/24; exit 0 ;;" \
  '    esac' \
  '    ;;' \
  '  port)' \
  '    [ "${FAKE_DOCKER_PORT_STATUS:-0}" -eq 0 ] || exit "${FAKE_DOCKER_PORT_STATUS}"' \
  '    printf "%s" "${FAKE_CADDY_PORTS:-}"; exit 0' \
  '    ;;' \
  'esac' \
  'echo "Unexpected docker invocation: $*" >&2' \
  'exit 1' > "$fake_bin/docker"

# Keep a forced health failure fast and deterministic while start_stack is
# exercised below. The production helper still uses the real commands.
printf '%s\n' \
  '#!/bin/bash' \
  'printf "1\\n"' > "$fake_bin/seq"
printf '%s\n' \
  '#!/bin/bash' \
  'exit 0' > "$fake_bin/sleep"
printf '%s\n' \
  '#!/bin/bash' \
  'exit 1' > "$fake_bin/curl"

chmod 0755 "$fake_bin"/*

run_helper() {
  PATH="$fake_bin:$PATH" \
    FAKE_CADDY_PORT="$port" \
    FAKE_DOCKER_LOG="$docker_log" \
    FAKE_RECEIPT="$receipt" \
    FAKE_RESIDUAL_LISTENER_STATE="$residual_listener_state" \
    FAKE_SOCKET_STATE="$socket_state" \
    FAKE_SYSTEMCTL_LOG="$systemctl_log" \
    HELPER="$helper" \
    bash -c 'set -euo pipefail; source "$HELPER"; "$@"' recovery-loopback-test "$@"
}

run_helper_script() {
  local script=$1
  PATH="$fake_bin:$PATH" \
    FAKE_CADDY_PORT="$port" \
    FAKE_DOCKER_LOG="$docker_log" \
    FAKE_RECEIPT="$receipt" \
    FAKE_RESIDUAL_LISTENER_STATE="$residual_listener_state" \
    FAKE_SOCKET_STATE="$socket_state" \
    FAKE_SYSTEMCTL_LOG="$systemctl_log" \
    HELPER="$helper" \
    bash -c "$script"
}

export FAKE_SS_MODE=good
run_helper start_loopback_proxy
[ -e "$socket_state" ] || fail 'Loopback proxy socket did not become active.'
assert_contains 'start start duducar-recovery-loopback-proxy.socket' "$systemctl_log"
run_helper write_loopback_proxy_receipt
run_helper require_loopback_proxy_receipt
run_helper stop_loopback_proxy
assert_absent "$socket_state"
assert_absent "$receipt"

export FAKE_SS_MODE=broad
expect_failure run_helper start_loopback_proxy
assert_absent "$socket_state"

export FAKE_SS_MODE=multiple
expect_failure run_helper start_loopback_proxy
assert_absent "$socket_state"

# The exact-only startup predicate must not make teardown claim success when a
# broad or additional listener remains after systemd stops its own socket.
export FAKE_SS_MODE=broad
: > "$residual_listener_state"
expect_failure run_helper stop_loopback_proxy
assert_absent "$receipt"
rm -f "$residual_listener_state"

# Container and Docker containment cleanup still runs when proxy teardown
# fails, but both helpers must return failure so callers cannot claim success.
: > "$residual_listener_state"
: > "$docker_log"
expect_failure run_helper stop_stack
assert_contains 'stop --time 120 duducar-recovery-caddy' "$docker_log"
rm -f "$residual_listener_state"

: > "$residual_listener_state"
: > "$systemctl_log"
expect_failure run_helper contain_untrusted_docker
assert_contains 'mask mask docker.service docker.socket' "$systemctl_log"
rm -f "$residual_listener_state"

# start_stack calls start_loopback_proxy in a conditional. A failed isolation
# check must return from that function before it can start the host socket.
: > "$systemctl_log"
export FAKE_CADDY_PORT_BINDINGS='{"8443/tcp":[{"HostIp":"127.0.0.1"}]}'
isolation_failure_output=''
if isolation_failure_output=$(run_helper_script '
  set -euo pipefail
  source "$HELPER"
  start_database() { :; }
  create_web() { :; }
  start_existing_container_if_stopped() { :; }
  wait_for_web() { :; }
  start_stack
' 2>&1); then
  fail 'Expected recovery Caddy isolation failure.'
else
  isolation_failure_status=$?
fi
[ "$isolation_failure_status" -eq 1 ] || fail "Expected isolation failure status 1, got $isolation_failure_status"
assert_not_contains 'start start duducar-recovery-loopback-proxy.socket' "$systemctl_log"
unset FAKE_CADDY_PORT_BINDINGS

# A proxy startup failure occurs after the application containers exist. It
# must trigger full stack cleanup, and a failed proxy teardown must be shown.
: > "$residual_listener_state"
: > "$docker_log"
proxy_start_output=''
if proxy_start_output=$(run_helper_script '
  set -euo pipefail
  source "$HELPER"
  start_database() { :; }
  create_web() { :; }
  start_existing_container_if_stopped() { :; }
  wait_for_web() { :; }
  start_loopback_proxy() { return 1; }
  start_stack
' 2>&1); then
  fail 'Expected recovery proxy startup failure.'
else
  proxy_start_status=$?
fi
[ "$proxy_start_status" -eq 1 ] || fail "Expected proxy startup failure status 1, got $proxy_start_status"
[[ "$proxy_start_output" == *'Proxy startup cleanup failed.'* ]] || \
  fail 'Proxy startup failure did not report failed cleanup.'
assert_contains 'stop --time 120 duducar-recovery-caddy' "$docker_log"
rm -f "$residual_listener_state"

# A readiness failure must report a failed proxy cleanup rather than hiding a
# residual listener behind the already-failing health result.
mkdir -p "$runtime_dir/caddy-data/caddy/pki/authorities/local"
printf 'fixture CA\n' > "$runtime_dir/caddy-data/caddy/pki/authorities/local/root.crt"
: > "$residual_listener_state"
: > "$docker_log"
health_failure_output=''
if health_failure_output=$(run_helper_script '
  set -euo pipefail
  source "$HELPER"
  start_database() { :; }
  create_web() { :; }
  start_existing_container_if_stopped() { :; }
  wait_for_web() { :; }
  start_loopback_proxy() { : > "$FAKE_SOCKET_STATE"; }
  start_stack
' 2>&1); then
  fail 'Expected recovery health failure with failed proxy cleanup.'
else
  health_failure_status=$?
fi
[ "$health_failure_status" -eq 1 ] || fail "Expected health cleanup failure status 1, got $health_failure_status"
[[ "$health_failure_output" == *'Health cleanup failed.'* ]] || \
  fail 'Recovery health failure did not report failed loopback-proxy cleanup.'
assert_contains 'logs --tail 80 duducar-recovery-caddy' "$docker_log"
assert_contains 'stop --time 120 duducar-recovery-caddy' "$docker_log"
assert_before 'logs --tail 80 duducar-recovery-caddy' 'stop --time 120 duducar-recovery-caddy' "$docker_log"
rm -f "$residual_listener_state"

export FAKE_SS_MODE=good
export FAKE_CADDY_PORTS='8443/tcp -> 127.0.0.1:8443'
expect_failure run_helper start_loopback_proxy
assert_absent "$socket_state"
unset FAKE_CADDY_PORTS

export FAKE_DOCKER_PORT_STATUS=1
expect_failure run_helper start_loopback_proxy
assert_absent "$socket_state"
unset FAKE_DOCKER_PORT_STATUS

export FAKE_CADDY_NETWORKS='duducar-recovery=172.31.0.10;unreviewed=172.31.1.10;'
expect_failure run_helper start_loopback_proxy
assert_absent "$socket_state"
unset FAKE_CADDY_NETWORKS

printf 'ListenStream=127.0.0.2:%s\n' "$port" >> "$socket_unit"
expect_failure run_helper ensure_loopback_proxy_units

echo 'Recovery loopback-proxy behavior checks passed.'

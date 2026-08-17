#!/bin/bash
set -euo pipefail
umask 0077

runtime_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
test_root=$(mktemp -d /tmp/duducar-stack-test.XXXXXX)
fake_bin=$(mktemp -d /tmp/duducar-stack-tools.XXXXXX)
cleanup() { rm -rf -- "$test_root" "$fake_bin"; }
trap cleanup EXIT
install -d "$test_root/etc/duducar" "$test_root/run/duducar" "$fake_bin"
: > "$test_root/etc/duducar/host.env"
cat > "$test_root/etc/duducar/release.env" <<'EOF'
BACKEND_IMAGE=example.test/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
POSTGRES_IMAGE=postgres@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CADDY_IMAGE=caddy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
CADDY_CONFIG=/etc/duducar/Caddyfile.post-cutover
REQUIRED_APP_VERSION=1.0.0
EOF

cat > "$fake_bin/docker" <<'EOF'
#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "$DUDUCAR_DOCKER_LOG"
case "${1:-} ${2:-}" in
  'network inspect')
    case "$*" in
      *Subnet*) printf '172.30.0.0/24\n' ;;
      *bridge.name*) printf 'duducar0\n' ;;
    esac
    ;;
  'container inspect')
    name=${3:-}
    if [ "$name" = duducar-caddy ] && [ ! -e "$DUDUCAR_CADDY_CREATED" ]; then
      exit 1
    fi
    ;;
  'inspect --format')
    format=${3:-}
    name=${4:-}
    case "$format" in
      *Config.Image*)
        case "$name" in
          duducar-postgres) printf '%s\n' "$POSTGRES_IMAGE" ;;
          duducar-web) printf '%s\n' "$BACKEND_IMAGE" ;;
          duducar-caddy) printf '%s\n' "$CADDY_IMAGE" ;;
        esac
        ;;
      *RestartPolicy*) printf 'no\n' ;;
      *NetworkSettings*)
        case "$name" in
          duducar-postgres) printf '172.30.0.11\n' ;;
          duducar-web) printf '172.30.0.10\n' ;;
          duducar-caddy) printf '172.30.0.12\n' ;;
        esac
        ;;
      *State.Running*) printf 'true\n' ;;
      *State.Health.Status*) printf 'healthy\n' ;;
    esac
    ;;
  'run -d')
    case "$*" in
      *'--name duducar-caddy'*) : > "$DUDUCAR_CADDY_CREATED" ;;
      *) echo "Only Caddy may be created in the post-deploy start fixture." >&2; exit 1 ;;
    esac
    ;;
  *) echo "Unexpected fake Docker command: $*" >&2; exit 1 ;;
esac
EOF
chmod 0755 "$fake_bin/docker"

export DUDUCAR_STACK_TEST_ROOT=$test_root
export DUDUCAR_DOCKER_LOG=$test_root/docker.log
export DUDUCAR_CADDY_CREATED=$test_root/caddy-created
export BACKEND_IMAGE=example.test/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export POSTGRES_IMAGE=postgres@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
export CADDY_IMAGE=caddy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
export PATH=$fake_bin:$PATH

bash "$runtime_dir/duducar-stack" start
! grep -Fq 'start duducar-postgres' "$DUDUCAR_DOCKER_LOG"
! grep -Fq 'start duducar-web' "$DUDUCAR_DOCKER_LOG"
grep -Fq -- '--name duducar-caddy' "$DUDUCAR_DOCKER_LOG"

echo "Post-deploy systemd start reused running PostgreSQL/web and created only Caddy."

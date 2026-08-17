#!/bin/sh
set -eu

if [ "$#" -ne 2 ] || [ "$1" != "--asset-id" ]; then
    echo "worker root init requires --asset-id with one media UUID" >&2
    exit 64
fi
if [ "${WORKER_ROOT_INIT:-}" != "1" ]; then
    echo "worker root init requires WORKER_ROOT_INIT=1" >&2
    exit 78
fi
if [ "$(id -u)" != "0" ]; then
    echo "worker root init must start as root" >&2
    exit 77
fi

# Fargate creates these task-local mounts as root:root. Make only their roots
# private to the worker, then drop privileges before any network or media work.
chown 10001:10001 /var/lib/clamav /tmp
chmod 0700 /var/lib/clamav /tmp
exec su-exec 10001:10001 ./worker-entrypoint.sh "$@"

#!/bin/sh
set -eu

if [ "$#" -ne 2 ] || [ "$1" != "--asset-id" ]; then
    echo "worker-entrypoint requires --asset-id with one media UUID" >&2
    exit 64
fi

freshclam --datadir=/var/lib/clamav --stdout
exec python manage.py process_media --asset-id "$2"

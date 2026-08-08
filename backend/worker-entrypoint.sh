#!/bin/sh
set -eu

if [ "$#" -ne 2 ] || [ "$1" != "--asset-id" ]; then
    echo "worker-entrypoint requires --asset-id with one media UUID" >&2
    exit 64
fi

freshclam_timeout_seconds=${FRESHCLAM_TIMEOUT_SECONDS:-120}
media_worker_timeout_seconds=${MEDIA_WORKER_TIMEOUT_SECONDS:-1200}
processing_lease_seconds=${MEDIA_PROCESSING_LEASE_SECONDS:-1800}
for timeout_setting in \
    "FRESHCLAM_TIMEOUT_SECONDS:$freshclam_timeout_seconds" \
    "MEDIA_WORKER_TIMEOUT_SECONDS:$media_worker_timeout_seconds" \
    "MEDIA_PROCESSING_LEASE_SECONDS:$processing_lease_seconds"
do
    timeout_name=${timeout_setting%%:*}
    timeout_value=${timeout_setting#*:}
    case "$timeout_value" in
        ''|*[!0-9]*|0)
            echo "$timeout_name must be a positive integer" >&2
            exit 64
            ;;
    esac
done
if [ "$media_worker_timeout_seconds" -ge "$processing_lease_seconds" ]; then
    echo "MEDIA_WORKER_TIMEOUT_SECONDS must be shorter than the processing lease" >&2
    exit 64
fi

timeout "$freshclam_timeout_seconds" freshclam \
    --datadir=/var/lib/clamav \
    --stdout
exec timeout "$media_worker_timeout_seconds" \
    python manage.py process_media --asset-id "$2"

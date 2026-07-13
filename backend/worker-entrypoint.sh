#!/bin/sh
set -eu

freshclam --datadir=/var/lib/clamav --stdout
freshclam --daemon --checks=2 --datadir=/var/lib/clamav --stdout &
exec python manage.py process_media --loop

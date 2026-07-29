#!/bin/sh
set -eu

owner_password=$(cat "$OWNER_DB_PASSWORD_FILE")
runtime_password=$(cat "$RUNTIME_DB_PASSWORD_FILE")

psql \
  --set ON_ERROR_STOP=1 \
  --set owner_password="$owner_password" \
  --set runtime_password="$runtime_password" \
  --username "$POSTGRES_USER" \
  --dbname postgres <<'SQL'
CREATE ROLE signage_owner
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOREPLICATION
  PASSWORD :'owner_password';

CREATE ROLE signage_app
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOREPLICATION
  PASSWORD :'runtime_password';

ALTER DATABASE signage OWNER TO signage_owner;
SQL

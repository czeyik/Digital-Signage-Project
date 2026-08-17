#!/bin/sh
set -eu

owner_password=$(cat "$OWNER_DB_PASSWORD_FILE")
runtime_password=$(cat "$RUNTIME_DB_PASSWORD_FILE")
worker_password=$(cat "$WORKER_DB_PASSWORD_FILE")

psql \
  --set ON_ERROR_STOP=1 \
  --set owner_password="$owner_password" \
  --set runtime_password="$runtime_password" \
  --set worker_password="$worker_password" \
  --username "$POSTGRES_USER" \
  --dbname postgres <<'SQL'
SELECT 'CREATE ROLE signage_owner'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'signage_owner') \gexec
ALTER ROLE signage_owner
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOREPLICATION
  PASSWORD :'owner_password';

SELECT 'CREATE ROLE signage_app'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'signage_app') \gexec
ALTER ROLE signage_app
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOREPLICATION
  PASSWORD :'runtime_password';

SELECT 'CREATE ROLE signage_worker'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'signage_worker') \gexec
ALTER ROLE signage_worker
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOREPLICATION
  PASSWORD :'worker_password';

ALTER DATABASE signage OWNER TO signage_owner;
SQL

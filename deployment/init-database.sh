#!/bin/sh
set -eu
# Only the official PostgreSQL image invokes this, in an explicitly new personal volume.
# No secrets in argv; psql quotes the value obtained from the child environment.
COMMERCE_APP_DATABASE_PASSWORD=$(cat /run/secrets/COMMERCE_DB_PASSWORD)
export COMMERCE_APP_DATABASE_PASSWORD
psql -X --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv app_password COMMERCE_APP_DATABASE_PASSWORD
CREATE ROLE commerce_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'app_password';
\set app_role commerce_app
\set app_database commerce
\i /deployment/database-privileges.sql
SQL
unset COMMERCE_APP_DATABASE_PASSWORD

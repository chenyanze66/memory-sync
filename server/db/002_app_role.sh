#!/bin/sh
set -eu

: "${APP_DB_USER:?APP_DB_USER is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_user="$APP_DB_USER" \
  --set=app_password="$APP_DB_PASSWORD" <<'SQL'
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
    :'app_user', :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:'app_user') \gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_user') \gexec

-- Least-privilege table grants: only the operations the API actually performs.
-- Tenant isolation on the business tables below is still enforced by FORCE RLS.
SELECT format('GRANT SELECT, INSERT ON auth_accounts TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE ON refresh_tokens TO %I', :'app_user') \gexec
SELECT format('GRANT INSERT ON profiles TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE ON devices TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT ON spaces TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE ON documents TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT ON document_versions TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT ON operations TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT ON sync_events TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT ON device_nonces TO %I', :'app_user') \gexec

-- Revoke any broader grants left by an earlier deployment of the previous
-- "ALL TABLES" grant. REVOKE is idempotent: revoking a non-granted privilege
-- only warns and does not fail with ON_ERROR_STOP.
SELECT format('REVOKE UPDATE, DELETE ON auth_accounts FROM %I', :'app_user') \gexec
SELECT format('REVOKE DELETE ON refresh_tokens FROM %I', :'app_user') \gexec
SELECT format('REVOKE SELECT, UPDATE, DELETE ON profiles FROM %I', :'app_user') \gexec
SELECT format('REVOKE DELETE ON devices FROM %I', :'app_user') \gexec
SELECT format('REVOKE UPDATE, DELETE ON spaces FROM %I', :'app_user') \gexec
SELECT format('REVOKE DELETE ON documents FROM %I', :'app_user') \gexec
SELECT format('REVOKE UPDATE, DELETE ON document_versions FROM %I', :'app_user') \gexec
SELECT format('REVOKE UPDATE, DELETE ON operations FROM %I', :'app_user') \gexec
SELECT format('REVOKE UPDATE, DELETE ON sync_events FROM %I', :'app_user') \gexec
SELECT format('REVOKE UPDATE, DELETE ON device_nonces FROM %I', :'app_user') \gexec

SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', :'app_user') \gexec
SELECT format('GRANT EXECUTE ON FUNCTION app_user_id() TO %I', :'app_user') \gexec
SELECT format('GRANT EXECUTE ON FUNCTION cleanup_expired_security_rows() TO %I', :'app_user') \gexec
SQL

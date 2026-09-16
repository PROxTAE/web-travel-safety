#!/usr/bin/env bash
# Runs once on an empty PostgreSQL data volume.
# Creates one schema + one login role per service so that a service cannot
# write to another service's tables (00_SHARED_PROJECT_CONTEXT §9).
# Passwords come from the environment injected by compose; blank passwords fail fast.
set -euo pipefail

require() {
  if [ -z "${!1:-}" ]; then
    echo "FATAL: $1 is not set — refusing to create role with empty password" >&2
    exit 1
  fi
}

for v in POSTGRES_API_PASSWORD POSTGRES_AGENT_PASSWORD POSTGRES_PROVIDER_PASSWORD \
         POSTGRES_INTEGRATION_PASSWORD POSTGRES_KNOWLEDGE_PASSWORD \
         POSTGRES_DECISION_PASSWORD POSTGRES_RECOMMENDATION_PASSWORD; do
  require "$v"
done

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE EXTENSION IF NOT EXISTS postgis;
  CREATE EXTENSION IF NOT EXISTS pgcrypto;
  CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

  -- Keycloak keeps its own schema in the same instance for local dev.
  CREATE SCHEMA IF NOT EXISTS keycloak AUTHORIZATION "$POSTGRES_USER";

  -- service role helper -------------------------------------------------------
  CREATE OR REPLACE FUNCTION sta_bootstrap_role(role_name text, pw text, schemas text[])
  RETURNS void LANGUAGE plpgsql AS \$fn\$
  DECLARE s text;
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', role_name, pw);
    ELSE
      EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', role_name, pw);
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), role_name);
    FOREACH s IN ARRAY schemas LOOP
      EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I', s, role_name);
      EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO %I', s, role_name);
    END LOOP;
    -- every service needs to read PostGIS functions in public but must not create there
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', role_name);
    EXECUTE format('REVOKE CREATE ON SCHEMA public FROM %I', role_name);
  END
  \$fn\$;

  SELECT sta_bootstrap_role('sta_api',            '$POSTGRES_API_PASSWORD',            ARRAY['identity','travel']);
  SELECT sta_bootstrap_role('sta_agent',          '$POSTGRES_AGENT_PASSWORD',          ARRAY['agent']);
  SELECT sta_bootstrap_role('sta_provider',       '$POSTGRES_PROVIDER_PASSWORD',       ARRAY['provider']);
  SELECT sta_bootstrap_role('sta_integration',    '$POSTGRES_INTEGRATION_PASSWORD',    ARRAY['integration']);
  SELECT sta_bootstrap_role('sta_knowledge',      '$POSTGRES_KNOWLEDGE_PASSWORD',      ARRAY['knowledge']);
  SELECT sta_bootstrap_role('sta_decision',       '$POSTGRES_DECISION_PASSWORD',       ARRAY['decision']);
  SELECT sta_bootstrap_role('sta_recommendation', '$POSTGRES_RECOMMENDATION_PASSWORD', ARRAY['recommendation']);

  -- Read-only cross-schema access is NOT granted by default (needs an ADR).
  REVOKE ALL ON SCHEMA public FROM PUBLIC;
  GRANT USAGE ON SCHEMA public TO PUBLIC;
EOSQL

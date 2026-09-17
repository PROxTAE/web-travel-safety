#!/usr/bin/env bash
# Generate per-machine secrets into .env (never commit the result).
# Usage: ops/scripts/gen-secrets.sh   (run from repo root after `cp .env.example .env`)
set -euo pipefail

ENV_FILE="${1:-.env}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found; copy .env.example first" >&2; exit 1; }

rand_hex() { python -c "import secrets; print(secrets.token_hex($1))"; }
rand_b64() { python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes($1)).decode())"; }

set_var() {
  local name="$1" value="$2"
  if grep -qE "^${name}=" "$ENV_FILE"; then
    # only fill if empty
    if grep -qE "^${name}=\s*(#.*)?$" "$ENV_FILE"; then
      python - "$ENV_FILE" "$name" "$value" <<'PY'
import re, sys
path, name, value = sys.argv[1:]
text = open(path, encoding="utf-8").read()
text = re.sub(rf"^{re.escape(name)}=.*$", f"{name}={value}", text, count=1, flags=re.M)
open(path, "w", encoding="utf-8", newline="\n").write(text)
PY
      echo "set $name"
    else
      echo "keep $name (already set)"
    fi
  else
    echo "$name=$value" >> "$ENV_FILE"
    echo "append $name"
  fi
}

for v in POSTGRES_PASSWORD POSTGRES_API_PASSWORD POSTGRES_AGENT_PASSWORD POSTGRES_PROVIDER_PASSWORD \
         POSTGRES_INTEGRATION_PASSWORD POSTGRES_KNOWLEDGE_PASSWORD POSTGRES_DECISION_PASSWORD \
         POSTGRES_RECOMMENDATION_PASSWORD KEYCLOAK_ADMIN_PASSWORD OIDC_CLIENT_SECRET GRAFANA_ADMIN_PASSWORD; do
  set_var "$v" "$(rand_hex 16)"
done
set_var SERVICE_AUTH_TOKEN "$(rand_hex 32)"
set_var AUTH_SECRET "$(rand_b64 32)"
set_var EMERGENCY_PROFILE_ENCRYPTION_KEY "$(rand_b64 32)"
set_var USER_SCOPE_SALT "$(rand_hex 16)"
set_var PSEUDONYM_SALT "$(rand_hex 16)"
echo "done — review $ENV_FILE; provider keys (ORS_API_KEY, OPENAI_API_KEY, AMADEUS_*) stay optional"

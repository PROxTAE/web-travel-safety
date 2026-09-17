#!/usr/bin/env bash
# Restore a backup made by ops/scripts/backup.sh into a SEPARATE throwaway compose project
# (compose.restore.yaml, project `sta-restore`) and smoke-read it against the manifest.
# The running stack is never touched.
#
# Usage: ops/scripts/restore.sh BACKUP_DIR [--teardown]      (run from the repo root)
#   --teardown  remove the sta-restore project and its volumes afterwards (only that project)
set -euo pipefail

BK="${1:?backup dir (e.g. backups/20260917T130000Z)}"; shift || true
TEARDOWN=0; [ "${1:-}" = "--teardown" ] && TEARDOWN=1
[ -f "$BK/manifest.json" ] || { echo "$BK/manifest.json not found" >&2; exit 1; }
ENV_FILE="${ENV_FILE:-.env}"
env_get() { grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2- | tr -d '\r'; }
PG_USER="$(env_get POSTGRES_USER)"; PG_DB="$(env_get POSTGRES_DB)"
RC=(docker compose -f compose.restore.yaml)
export MSYS_NO_PATHCONV=1  # Git Bash: keep /tmp/... container paths intact

echo "== verify file hashes"
python - "$BK" <<'PY'
import hashlib, json, os, sys
bk = sys.argv[1]; m = json.load(open(os.path.join(bk, "manifest.json")))
for rel, meta in m["files"].items():
    h = hashlib.sha256(open(os.path.join(bk, rel), "rb").read()).hexdigest()
    assert h == meta["sha256"], f"hash mismatch: {rel}"
print(f"{len(m['files'])} files verified")
PY
COLL="$(python -c "import json,sys; print(json.load(open(sys.argv[1]))['qdrant']['collection'])" "$BK/manifest.json")"
ALIAS="$(python -c "import json,sys; a=json.load(open(sys.argv[1]))['result']['aliases']; print(next(x['alias_name'] for x in a if x['alias_name'].endswith('active')))" "$BK/qdrant/aliases.json")"

echo "== fresh restore target (project sta-restore)"
"${RC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
"${RC[@]}" up -d --wait postgres qdrant tools qtools

echo "== postgres: pg_restore (schemas/extensions/roles/bootstrap function come from the init script; tables, data, owners and grants from the dump)"
"${RC[@]}" cp "$BK/postgres.dump" tools:/tmp/postgres.dump
"${RC[@]}" exec -T tools bash -c "
  set -e
  export PGPASSWORD=\"\$POSTGRES_PASSWORD\"
  pg_restore -l /tmp/postgres.dump | grep -vE '^;|(SCHEMA|EXTENSION|COMMENT) - |ACL - SCHEMA public|FUNCTION public sta_bootstrap_role' > /tmp/restore.list
  pg_restore -h postgres -U $PG_USER -d $PG_DB --no-password --exit-on-error -L /tmp/restore.list /tmp/postgres.dump
  echo restored \$(grep -c . /tmp/restore.list) catalog entries"

echo "== qdrant: upload snapshot + recreate alias"
"${RC[@]}" cp "$BK/qdrant/$COLL.snapshot" qtools:/tmp/coll.snapshot
"${RC[@]}" exec -T qtools sh -c "
  set -e
  curl -sf -X POST 'http://qdrant:6333/collections/$COLL/snapshots/upload?priority=snapshot' -F 'snapshot=@/tmp/coll.snapshot' >/dev/null
  curl -sf -X POST http://qdrant:6333/collections/aliases -H 'Content-Type: application/json' \
    -d '{\"actions\":[{\"create_alias\":{\"collection_name\":\"$COLL\",\"alias_name\":\"$ALIAS\"}}]}' >/dev/null
  echo 'snapshot uploaded, alias $ALIAS -> $COLL'"

echo "== smoke read vs manifest"
{
"${RC[@]}" exec -T tools bash -c "
  export PGPASSWORD=\"\$POSTGRES_PASSWORD\"
  psql -h postgres -U $PG_USER -d $PG_DB -At -c \"
  SELECT json_build_object(
    'travel.trips', (SELECT count(*) FROM travel.trips),
    'travel.requests', (SELECT count(*) FROM travel.requests),
    'agent.runs', (SELECT count(*) FROM agent.runs),
    'integration.snapshots', (SELECT count(*) FROM integration.snapshots),
    'decision.audit_traces', (SELECT count(*) FROM decision.audit_traces),
    'recommendation.recommendations', (SELECT count(*) FROM recommendation.recommendations),
    'identity.user_profiles', (SELECT count(*) FROM identity.user_profiles),
    'keycloak.user_entity', (SELECT count(*) FROM keycloak.user_entity))\"
  psql -h postgres -U $PG_USER -d $PG_DB -At -c \"SELECT string_agg(nspname||':'||pg_get_userbyid(nspowner), ' ' ORDER BY nspname) FROM pg_namespace WHERE nspname IN ('identity','travel','agent','provider','integration','knowledge','decision','recommendation')\""
"${RC[@]}" exec -T qtools curl -sf "http://qdrant:6333/collections/$ALIAS"
echo
} | tr -d '\r' > "$BK/restore_smoke.txt"
cat "$BK/restore_smoke.txt"
python - "$BK" <<'PY'
import json, sys, os
bk = sys.argv[1]; m = json.load(open(os.path.join(bk, "manifest.json")))
lines = [l for l in open(os.path.join(bk, "restore_smoke.txt")).read().splitlines() if l.strip()]
pg = json.loads(lines[0]); qd = json.loads(lines[-1])["result"]
bad = [k for k, v in m["postgres"].items() if pg.get(k) != v]
if qd["points_count"] != m["qdrant"]["points_count"]: bad.append("qdrant.points_count")
print("schema owners:", lines[1])
print("RESTORE DRILL:", "PASS" if not bad else f"FAIL {bad}", "| postgres", pg, "| qdrant points", qd["points_count"], qd["status"])
sys.exit(1 if bad else 0)
PY
if [ "$TEARDOWN" = 1 ]; then echo "== teardown sta-restore (its own volumes only)"; "${RC[@]}" down -v --remove-orphans; fi

#!/usr/bin/env bash
# Back up the stateful stores of a running stack: PostgreSQL (all 7 service schemas + keycloak)
# and the active Qdrant knowledge collection (snapshot + alias/config metadata).
# Redis is deliberately NOT backed up: it only holds caches, rate-limit windows, idempotency
# fingerprints and run event streams, all with TTLs, and every entry is rebuildable.
#
# Usage: ops/scripts/backup.sh [OUT_DIR]          (run from the repo root)
#   COMPOSE_PROJECT=<name>  compose project of the running stack (default: compose.yaml `name`)
#   ENV_FILE=.env           where POSTGRES_USER / POSTGRES_DB are read from
# Output: OUT_DIR/<UTC timestamp>/{postgres.dump,pg_counts.json,qdrant/<collection>.snapshot,qdrant/*.json,manifest.json}
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash: keep /collections/... URL paths intact when passed to docker exec

ENV_FILE="${ENV_FILE:-.env}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found" >&2; exit 1; }
env_get() { grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2- | tr -d '\r'; }
PG_USER="$(env_get POSTGRES_USER)"; PG_DB="$(env_get POSTGRES_DB)"
[ -n "$PG_USER" ] && [ -n "$PG_DB" ] || { echo "POSTGRES_USER/POSTGRES_DB missing in $ENV_FILE" >&2; exit 1; }

COMPOSE=(docker compose)
[ -n "${COMPOSE_PROJECT:-}" ] && COMPOSE+=(-p "$COMPOSE_PROJECT")
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${1:-backups}/$STAMP"
mkdir -p "$OUT/qdrant"

COUNTS_SQL="SELECT json_build_object(
  'travel.trips', (SELECT count(*) FROM travel.trips),
  'travel.requests', (SELECT count(*) FROM travel.requests),
  'agent.runs', (SELECT count(*) FROM agent.runs),
  'integration.snapshots', (SELECT count(*) FROM integration.snapshots),
  'decision.audit_traces', (SELECT count(*) FROM decision.audit_traces),
  'recommendation.recommendations', (SELECT count(*) FROM recommendation.recommendations),
  'identity.user_profiles', (SELECT count(*) FROM identity.user_profiles),
  'keycloak.user_entity', (SELECT count(*) FROM keycloak.user_entity))"

echo "== postgres: pg_dump -Fc $PG_DB (all schemas)"
"${COMPOSE[@]}" exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" -Fc --no-password > "$OUT/postgres.dump"
# exact counts the restore drill compares against (taken from the live database, not the dump file)
"${COMPOSE[@]}" exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -At -c "$COUNTS_SQL" | tr -d '\r' > "$OUT/pg_counts.json"

echo "== qdrant: snapshot of the active collection"
# risk-knowledge already has httpx and sits on the internal network; qdrant publishes no host port
q() { "${COMPOSE[@]}" exec -T risk-knowledge python -c "
import sys, httpx
m, path = sys.argv[1], sys.argv[2]
r = httpx.request(m, 'http://qdrant:6333' + path, timeout=120); r.raise_for_status()
sys.stdout.write(r.text)" "$@" | tr -d '\r'; }
q GET /aliases > "$OUT/qdrant/aliases.json"
COLL="$(python -c "import json,sys; a=json.load(open(sys.argv[1]))['result']['aliases']; print(next(x['collection_name'] for x in a if x['alias_name'].endswith('active')))" "$OUT/qdrant/aliases.json")"
q GET "/collections/$COLL" > "$OUT/qdrant/collection.json"
SNAP="$(q POST "/collections/$COLL/snapshots" | python -c "import json,sys; print(json.load(sys.stdin)['result']['name'])")"
# stream the snapshot file out of the container (binary-safe)
"${COMPOSE[@]}" exec -T risk-knowledge python -c "
import sys, httpx
with httpx.stream('GET', 'http://qdrant:6333/collections/$COLL/snapshots/$SNAP', timeout=300) as r:
    r.raise_for_status()
    for chunk in r.iter_bytes(): sys.stdout.buffer.write(chunk)" > "$OUT/qdrant/$COLL.snapshot"
q DELETE "/collections/$COLL/snapshots/$SNAP" > /dev/null

echo "== manifest"
python - "$OUT" "$COLL" "$STAMP" <<'PY'
import hashlib, json, os, sys
out, coll, stamp = sys.argv[1:]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
files = {}
for root, _, names in os.walk(out):
    for n in names:
        p = os.path.join(root, n); rel = os.path.relpath(p, out).replace(os.sep, "/")
        if rel != "manifest.json": files[rel] = {"bytes": os.path.getsize(p), "sha256": sha(p)}
qc = json.load(open(os.path.join(out, "qdrant", "collection.json")))["result"]
manifest = {"schema_version": "1.0.0", "captured_at": stamp, "postgres": json.load(open(os.path.join(out, "pg_counts.json"))),
            "qdrant": {"collection": coll, "points_count": qc.get("points_count"), "vectors": qc["config"]["params"]["vectors"]},
            "files": files, "not_included": ["redis (caches/TTL only)", "model-artifacts volume (rebuilt by the training pipeline)"]}
json.dump(manifest, open(os.path.join(out, "manifest.json"), "w"), indent=1)
print(json.dumps({k: manifest[k] for k in ("captured_at", "postgres", "qdrant")}, indent=1))
PY
echo "backup written to $OUT"

# Backup / restore runbook

Stateful stores and what happens to each:

| Store | Backed up | How | Why |
| --- | --- | --- | --- |
| PostgreSQL `smart_travel` (7 service schemas + `keycloak`) | yes | `pg_dump -Fc` of the whole database | user, trip, run, audit, recommendation and Keycloak state live here |
| Qdrant active knowledge collection | yes | collection snapshot + `aliases.json` + `collection.json` | re-indexing is possible but slow (embeddings); the snapshot keeps the released collection byte-identical |
| Redis | **no** | — | caches, rate-limit windows, idempotency fingerprints and run event streams, all with TTLs; every entry is rebuildable and none is a system of record |
| `model-artifacts` volume | no (separate) | training pipeline re-creates a versioned artifact; copy the directory if a release must be pinned | artifacts are versioned by `knowledge.model_versions` |

Backups contain personal data (trips, contacts, emergency profiles stay AES-GCM encrypted — the key is in `.env`, never in the
backup). Treat `backups/` like the database: local disk only, gitignored, delete after the retention window.

## Take a backup

```bash
COMPOSE_PROJECT=<running project name> ops/scripts/backup.sh backups
```

Output `backups/<UTC stamp>/`: `postgres.dump`, `pg_counts.json`, `qdrant/<collection>.snapshot`, `qdrant/aliases.json`,
`qdrant/collection.json`, `manifest.json` (sha256 of every file + row counts + point count). Takes ~20 s on the acceptance data set
(9 MB dump, 0.9 MB snapshot).

## Restore drill (never touches the running stack)

```bash
ops/scripts/restore.sh backups/<UTC stamp> --teardown
```

1. verifies every file against `manifest.json`
2. starts a throwaway project `sta-restore` (`compose.restore.yaml`: postgres + qdrant + helpers, no host ports, own volumes)
3. the init script creates extensions, roles and schemas; `pg_restore` then loads everything else with the original owners and
   grants (`--exit-on-error`, catalog entries for schemas/extensions/the bootstrap function are filtered out of the list)
4. uploads the Qdrant snapshot and recreates the `*_active` alias
5. smoke read: row counts of 8 tables + schema owners + collection status/point count, compared with the manifest → `RESTORE DRILL: PASS|FAIL`
6. `--teardown` removes only the `sta-restore` project and its volumes

`docker compose down -v` on the real project is still forbidden as a routine command (00_GIT_DOCKER_DELIVERY_RULES).

## Restoring for real

Same steps against a fresh `postgres`/`qdrant` of the real project (empty volumes so the init script runs), then `alembic upgrade
head` per service is a no-op check, restart the services, and run the readiness sweep from the acceptance runbook. Keycloak reads its
schema from the same dump, so realm, clients and users come back with it.

## Evidence

Last drill: see the current file in `docs/acceptance/` (§6 Database / storage).

# data-integration (คน 5)

Turns คน 4's `ExternalContext` into an immutable, versioned `IntegratedTravelContext` (snapshot).

Pipeline (`app/pipeline`): validate → normalize (+lineage) → deduplicate/resolve conflicts → corridor +
temporal alignment → features (`config/feature_schema.yaml`, v1.0.0) → quality gate → snapshot.

| Stage | Key rules |
| --- | --- |
| validate | invalid geometry / NaN / negative precipitation / impossible times → `integration.quarantine`; nothing dropped silently |
| normalize | UTC timestamps, duplicate vertex removal, `make_valid` geometry repair with lineage rows (transform v1.0.0) |
| dedup | provider id → content hash → spatial/temporal cluster (50 km / 3 h, same type). Official records of different authorities are never merged away; severity/closure conflicts are recorded and resolved monotonically (never averaged) |
| corridor | densify 5 km, buffer 15 km ground / 60 km flight in a local AEQD projection, split at the antimeridian; events get a severity-scaled area of effect; time overlap uses `effective_at/ends_at` or a per-type default active window |
| weather | match by distance ≤ 40 km and ETA tolerance ≤ 3 h; coverage = expected positions (route length / 50 km, cap 12) that are covered |
| features | 31 features + `_missing` indicators; the same `build_features` runs online and in `app.cli.features_parity` |
| gate | `PASS` / `DEGRADED` (score < 0.6, weather coverage < 0.5, conflicts, inferred route, stale official alerts) / `BLOCK` (no usable route, no evidence, score < 0.25) |

API (`/internal/v1`, service token): `POST snapshots` (idempotent on `request_id + input hash + schema_version`),
`GET snapshots/{id}`, `POST snapshots/{id}/validate`, `GET events/search?bbox=&at=`, `GET feature-schema`.

```bash
uv sync --all-extras && uv run pytest -q   # 19 golden/pipeline/API tests
uv run python -m app.cli.features_parity --snapshots exports/ --out features.parquet
docker compose run --rm data-integration alembic upgrade head
```

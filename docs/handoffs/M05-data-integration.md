# [M05] Data Integration — Completion Report

## 1. Metadata

| Field | Value |
| --- | --- |
| Module/owner | M05 Data Integration — คน 5 |
| Issue/PR | `feat/05-data-integration` → main (squash title `[M05] Add canonical pipeline, route corridor and immutable snapshots`) |
| Branch | `feat/05-data-integration` |
| Base/final commit SHA | base `620475c` (M04 merge) |
| Date/time/timezone | 2026-09-17, Asia/Bangkok |
| Reviewers | คน 4 (provider mapping), คน 6 (feature schema), Team Lead (contract widening) |
| Contract version | 1.0.0 (+ `route_corridor_geojson: Polygon | MultiPolygon`, backward compatible) |
| Docker image | `sta/data-integration:1.0.0`, non-root |
| Versions | schema 1.0.0 · feature_schema 1.0.0 · transform 1.0.0 · quality weights 1.0.0 · event buffer table 1.0.0 |

## 2. Executive summary

- สร้าง pipeline 7 ขั้น (validate → normalize → dedup/conflict → corridor/time alignment → features → quality gate →
  immutable snapshot) เป็น pure functions ทดสอบด้วย golden case ที่คำนวณมือ 19 เคส และ persist ลง PostGIS
- Route corridor คำนวณใน local azimuthal-equidistant projection (หน่วยเมตรจริง ไม่ใช่องศา) buffer 15 km (ground) /
  60 km (flight), แยก MultiPolygon เมื่อข้าม antimeridian, event ทุกชนิดมี area-of-effect ตาม severity และหน้าต่างเวลา
- Feature schema v1 (31 features + missing indicators) เป็นไฟล์ YAML ที่ทั้ง online และ training ใช้ร่วมกันผ่านฟังก์ชันเดียว
- Snapshot idempotent ด้วย `(request_id, input_content_hash, schema_version)`; รัน input เดิมได้ `content_hash` เดิม
- ข้อจำกัดสำคัญ: transport matching เป็น aggregate ระดับ region (ไม่ match segment/service เพราะ GTFS static join ไม่อยู่ใน MVP)
- พร้อม merge: ruff + mypy strict + 19 tests ผ่าน; migration `0001_integration_baseline` (PostGIS GiST/BRIN indexes)

## 3. Original responsibility and acceptance criteria

- [x] input invalid ไม่ถูก drop เงียบและ quarantine trace ได้ — `pipeline/validate.py`, `test_invalid_records_go_to_quarantine`
- [x] canonical unit/time/geometry ถูกต้องและ versioned — `pipeline/normalize.py` lineage rows (transform v1.0.0)
- [x] dedup idempotent และ source conflict ไม่สูญ — `pipeline/deduplicate.py`, `DataConflict` records, tests
- [x] hazards/weather match เต็ม corridor + travel time — `enrich_geospatial.py` (`intersect_events`, `match_weather`)
- [x] every important field มี lineage ถึง source — `integration.lineage` + `source_ids` on snapshot
- [x] feature schema v1 ใช้ร่วม online/training และไม่มี leakage — `config/feature_schema.yaml`, `cli/features_parity.py`; features use only snapshot-time data
- [x] quality gate/degraded flags ส่งถึงคน 7/1 — `QualitySummary.gate/reasons/degraded_services`
- [x] snapshot immutable/repeatable/content-hashed — `test_snapshot_is_deterministic_and_input_hash_stable`
- [x] PostGIS จริง + migrations + indexes — `migrations/versions/0001_integration_baseline.py`
- [ ] backup/restore drill — **pending** full-stack acceptance (runbook §6)
- [ ] Docker integration กับคน 4/6 — **pending** compose run (unit-level contract verified via `ExternalContext` model)

## 4. What was implemented

| Feature | Behavior now | Entry point | Status |
| --- | --- | --- | --- |
| Validation + quarantine | geometry validity, NaN/Inf, negative precip, implausible delays, time order, request id match | `validate_context` | Complete |
| Normalize + lineage | UTC, duplicate vertices, `make_valid`, per-field lineage rows | `normalize` | Complete |
| Dedup + conflicts | id/hash/cluster dedup; severity & closure conflicts recorded; monotonic resolution | `deduplicate` | Complete |
| Corridor | densify, AEQD buffer, antimeridian split, H3 res-3 coverage | `build_corridor` | Complete |
| Event intersection | severity-scaled buffer per type, active window per type, distance to route | `intersect_events` | Complete |
| Weather alignment | distance + ETA tolerance, coverage vs expected positions | `match_weather` | Complete |
| Exposure | per-route `RouteExposure` (score v1, closed, severe minutes, max severity, min distance) | `exposure_for_route` | Complete |
| Features v1 | 31 features + `_missing` indicators, schema guard against unknown features | `build_features` | Complete |
| Quality gate | group summaries + overall + PASS/DEGRADED/BLOCK with reasons | `overall_and_gate` | Complete |
| Snapshot API | idempotent create, get, validate (strict), events search (bbox/time), feature schema | `api/internal.py` | Complete |
| Persistence | PostGIS tables snapshots/disaster_events/weather_records/transport_records/lineage/quarantine | `repositories/` | Complete |
| Parity CLI | recompute features offline, export Parquet | `cli/features_parity.py` | Complete |

Flow:

```text
SnapshotCreateRequest{travel_request, external_context}
  -> input_hash -> existing? return (idempotent)
  -> validate -> normalize -> deduplicate
  -> for each route: corridor -> hits -> weather matches -> exposure (closed => usable=false)
  -> features(primary) -> group qualities -> overall + gate
  -> IntegratedTravelContext{content_hash, supersedes_snapshot_id} -> persist (unique constraint) -> 201
Error: no valid routes -> 422 INSUFFICIENT_EVIDENCE; request mismatch -> ValueError -> 422
```

Not implemented: transport segment/service matching (region aggregate only); GDACS polygon fetch (centroid + buffer
instead); monthly partitioning (no volume evidence yet).

## 5. Architecture and design decisions

| Path | Purpose |
| --- | --- |
| `app/pipeline/{validate,normalize,deduplicate,enrich_geospatial,build_snapshot}.py` | pure pipeline stages |
| `app/domain/{features,quality}.py` | feature schema builder, quality weights/gate |
| `app/repositories/{models,snapshot_repo}.py` | PostGIS persistence, in-memory repo for tests |
| `config/feature_schema.yaml` | data dictionary (unit, nullable, definition, critical list) |

Decisions:
- AEQD per-route projection instead of a global metric CRS: correct metres anywhere incl. polar/dateline routes.
- Coverage denominator derived from route length (50 km spacing, cap 12) so sparse provider data cannot claim 100%.
- Official closure forces `usable=false` and exposure 1.0 → gate BLOCK when no other route (decision engine → AVOID).
- Event with unknown timing is treated as active (conservative), flagged in quality notes.
- Contract widened: `route_corridor_geojson` may be a MultiPolygon (antimeridian). Optional/backward compatible.

## 6. API / contract

| Producer | Method/path | Request | Response | Consumer |
| --- | --- | --- | --- | --- |
| data-integration | `POST /internal/v1/snapshots` | `SnapshotCreateRequest` | `IntegratedTravelContext` | agent, risk-knowledge |
| data-integration | `GET /internal/v1/snapshots/{id}` | — | `IntegratedTravelContext` | risk, decision, recommendation |
| data-integration | `POST /internal/v1/snapshots/{id}/validate` | `{strict}` | quality report | agent |
| data-integration | `GET /internal/v1/events/search?bbox&at` | — | stored canonical events | api (safety map) |
| data-integration | `GET /internal/v1/feature-schema` | — | schema YAML as JSON | risk-knowledge training |

## 7. Database

| Revision | Tables/indexes |
| --- | --- |
| `0001_integration_baseline` | `snapshots` (unique input triple, GiST corridor), `disaster_events` (GiST geom, time, severity idx, unique event+hash), `weather_records` (GiST, BRIN valid_at, unique hash), `transport_records`, `lineage`, `quarantine` |

Retention: quarantine rows are OPEN until reviewed; cleanup job documented for ops (30 days).

## 8. Real data

Consumes only คน 4 canonical records. Tests use synthetic-but-plausible records for golden geometry math
(hand-calculated distances), never presented as live data; the API test suite asserts behaviour, not provider values.

## 9. Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `POSTGRES_INTEGRATION_PASSWORD` | — | role `sta_integration` (required non-test) |
| corridor/weather/gate thresholds | see `app/settings.py` | all thresholds are settings, not code constants |

## 10. Tests

| Type | Command | Result |
| --- | --- | --- |
| Lint/type | `uv run ruff check . && uv run mypy app` | pass (18 files) |
| Unit/golden/API | `uv run pytest -q` | 19 passed |

Scenarios: buffer width in metres (10 km inside / 30 km outside), severity-scaled area of effect, event before/during/after
trip, unknown timing, weather tolerance (12 h off and 200 km off ignored), coverage 6/12 vs 13/12, cross-source dedup with
severity conflict (monotonic raise), closure conflict, quarantine, request mismatch, deterministic hash, closure → BLOCK,
inferred route + missing weather → DEGRADED, no routes → INSUFFICIENT_EVIDENCE, dateline corridor MultiPolygon, idempotent
POST, supersedes chain, auth.

## 12. Safety review

- [x] official warning priority: cluster representative can only be raised to max official severity; closure wins
- [x] no PII: snapshots hold coordinates of routes/events only (trip data, not live location)
- [x] no invented data: missing features are `null` + `_missing=1`; gate reasons list every degradation

## 13. Problems and resolutions

| Problem | Root cause | Resolution |
| --- | --- | --- |
| `ZoneInfoNotFoundError` on Windows | no system tz database | added `tzdata` dependency (also required in slim images) |
| coverage 1.0 with 2 points | denominator came from input | denominator from route length (expected positions) |
| dateline corridor invalid | planar centroid + lon wrap | geodesic midpoint centre + antimeridian split → MultiPolygon |

## 15. Known limitations

| Limitation | Impact | Owner |
| --- | --- | --- |
| Transport region aggregate | `delayed_segment_count` counts records, not segments | คน 4/5 (GTFS static join) |
| Centroid events buffered by severity table | approximate area of effect | คน 5 (fetch GDACS polygons) |

## 16. Handoff

| Recipient | Ready | Must do |
| --- | --- | --- |
| คน 6 | `IntegratedTravelContext.features` (schema v1), `route_candidates[].exposure/usable`, `official_alerts` | consume `features` as-is; do not recompute; use `cli/features_parity.py` for training export |
| คน 7 | `quality_summary.gate/reasons`, `conflict_summary.safety_critical` | BLOCK ⇒ no normal path; CONFLICTING ⇒ conservative |
| คน 3 | `POST /snapshots` idempotent; 422 `INSUFFICIENT_EVIDENCE` when no routes | map to `run.failed`/degraded |
| คน 2 | `GET /events/search` | proxy for `/api/v1/safety/events` layer `natural_hazards` |

## 17. Commits

`feat(integration): validation/normalization/lineage, dedup+conflicts, PostGIS corridor alignment, feature schema v1, quality gate, immutable snapshot API`

## 18. Rollback

Downgrade `0001_integration_baseline` drops `integration.*` (no other schema depends on it). Snapshots are immutable;
a bad transform version is fixed forward by bumping `transform_version` and rebuilding.

ผู้จัดทำ: คน 5 (simulated) · วันที่: 2026-09-17

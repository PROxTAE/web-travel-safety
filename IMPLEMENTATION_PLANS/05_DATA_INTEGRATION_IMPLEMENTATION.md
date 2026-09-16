# คนที่ 5 — Data Integration Implementation Plan

## Mission

สร้าง pipeline ที่รวม canonical recordsจาก providerหลายแหล่งให้เป็น `IntegratedTravelContext` ที่ repeatable, immutable, versioned และ traceย้อนกลับได้ โดย normalizeเวลา/หน่วย/ชื่อสถานที่/พิกัด, deduplicate, resolve conflict, สร้าง route corridor, alignเหตุการณ์ตามตำแหน่งและเวลาจริง, คำนวณ feature และ data-quality flags ก่อนส่ง local risk model

ความสำคัญ: ข้อมูลจาก providerแต่ละเจ้ามีหน่วย เวลา geometry และความหมายต่างกัน หาก moduleนี้ไม่เสร็จ risk modelจะรับ inputไม่ได้ หากรวมผิด route/time อาจพลาดภัยบนเส้นทางหรือใช้ forecastผิดเวลา

## Ownership and dependencies

- `services/data-integration/**`
- schema PostgreSQL `integration`
- internal data-integration OpenAPIร่วมกับคน 3/4/6
- canonical normalization/feature schema versions
- quarantine/dead-letter/data-quality dashboard

Consumesคน 4; produces snapshotให้คน 3/6/7 โดยคน 6ต้องร่วม review feature schema และคน 4ร่วม review provider mapping

## Stack

- Python 3.12, FastAPI, Pydantic v2
- SQLAlchemy 2 async + Alembic; PostgreSQL 16 + PostGIS
- Shapely, pyproj, GeoAlchemy2, H3
- Polarsเป็น main batch/dataframe engine; NumPyสำหรับ numeric feature; Panderaสำหรับ offline data checks
- Redisสำหรับ recent dedup/cache/locks
- OpenTelemetry/Prometheus/structlog
- pytest, Hypothesis, Testcontainers PostGIS
- Kafka/Redpandaยังไม่ใช้ใน MVP; เพิ่มได้เฉพาะมี load evidenceและ ADR

## Target structure

```text
services/data-integration/
├─ app/
│  ├─ main.py
│  ├─ api/internal.py
│  ├─ domain/
│  │  ├─ canonical.py
│  │  ├─ snapshot.py
│  │  ├─ quality.py
│  │  └─ features.py
│  ├─ pipeline/
│  │  ├─ validate.py
│  │  ├─ normalize.py
│  │  ├─ deduplicate.py
│  │  ├─ resolve_conflicts.py
│  │  ├─ enrich_geospatial.py
│  │  ├─ align_time.py
│  │  ├─ build_features.py
│  │  └─ build_snapshot.py
│  ├─ repositories/
│  ├─ lineage/
│  ├─ quarantine/
│  └─ settings.py
├─ migrations/
├─ tests/
├─ pyproject.toml
└─ Dockerfile
```

## Processing invariants

1. inputทุก record validate schemaก่อน transform; invalidไป quarantineพร้อม reason/source hash ไม่ dropเงียบ ๆ
2. `observed_at`, `published_at`, `fetched_at`, `valid_at`, `expires_at` แยกกัน ห้าม overwrite
3. storage CRS = EPSG:4326; distance/bufferเปลี่ยนเป็น local metric/equidistant projectionที่เหมาะสมแล้ว transformกลับ
4. GeoJSON coordinate `[lon, lat]`; reject/flag lat-lon swapที่ตรวจได้
5. missingเป็น `null` + flag ไม่เติม 0/ค่าเฉลี่ยโดยไม่ระบุ `INFERRED`
6. source conflictเก็บหลักฐานทุกแหล่งและ resolution reason
7. snapshot immutable; rerun input/versionเดียวกันได้ content hashเดียวกัน
8. online inferenceและ offline trainingใช้ function/feature definitionเดียวกัน
9. full route corridor + ETA windowเป็น scope; origin/destinationอย่างเดียวไม่พอ
10. official alertไม่ถูก dedupทิ้งเพราะ community/curated eventคล้ายกัน

## Pipeline stages

### 1. Validate

- contract/schema version compatible
- request/trip/route IDs match
- time window valid, geometry valid, finite numeric values
- source/freshness fieldsครบ
- invalid record insert `integration.quarantine`พร้อม `error_code`, `field_path`, `content_hash`; raw contentเก็บเฉพาะ policy/licenseอนุญาต

### 2. Normalize

- timestamps -> UTC; retain source timezone/name
- temperature -> Celsius, wind -> km/h, distance -> meter, duration -> seconds, precipitation -> mm
- severity -> canonical enum พร้อม mapping version
- place/country/admin codes normalized แต่ไม่ทับ source label
- geometry repairเฉพาะ safe operation; ถ้าเปลี่ยน topologyต้อง flagและเก็บ transform version

### 3. Deduplicate

ลำดับ key:

1. provider + provider record ID + version/update
2. official cross-reference ID
3. content hash exact
4. spatial/time similarity candidate; ต้องเก็บ cluster membersและ confidence

ใช้ idempotent upsert unique constraints; official recordsต่าง authorityไม่ mergeจนสูญ source

### 4. Resolve conflicts

ranking inputs: authority, official status, freshness, coverage, completeness, provider health, agreement

- selected valueมี field-level lineage
- losing valuesยัง queryได้ใน conflict record
- unresolved safety-critical conflict -> `CONFLICTING`, confidenceลด, decision engineเห็น conflict
- ไม่ average severity/closure boolean

### 5. Route corridor and temporal alignment

- densify route lineตาม maximum spacing configและ attach ETAจาก segment duration
- buffer routeตาม mode/hazard (เช่น ground routeใช้ km configurable; flight corridorต่างกัน)
- dateline/polar/multi-segment geometryต้อง handle
- intersect events/alertsกับ corridor geometryและ active/effective window
- match weather pointกับ nearest route sample + ETA tolerance; report coverage percentage
- transport eventต้อง match operator/service/segment/time ไม่ matchชื่อเมืองอย่างเดียว

### 6. Feature build

Feature schema v1 (ตัวอย่างขั้นต่ำที่ต้องตกลงกับคน 6):

```text
route_distance_km, route_duration_hours, travel_mode_encoded
weather_max_precip_probability, weather_max_precip_mm
weather_max_wind_gust_kmh, weather_min_visibility_m
weather_max_temperature_c, weather_min_temperature_c
severe_weather_exposure_minutes, weather_coverage_ratio
earthquake_max_magnitude_near_corridor, earthquake_min_distance_km
active_official_alert_count, max_disaster_severity
closed_segment_count, delayed_segment_count, max_delay_minutes
transport_realtime_coverage_ratio
source_quality_mean, source_quality_min, stale_source_ratio
conflict_count, missing_critical_count
departure_hour_local, month, route_region_h3_features
```

- encode/imputeอยู่ใน versioned sklearn pipelineของคน 6 แต่ moduleนี้สร้าง raw deterministic feature valuesและ missing indicators
- feature units/definition/nullability/ownerเก็บ `feature_schema.yaml`
- feature leakage testห้ามใช้ข้อมูลที่เกิดหลัง recommendation time

### 7. Quality gate and snapshot

Quality scoreคำนวณจาก configurable/versioned weights: freshness, completeness, coverage, agreement, authority

Gate outcome:

- `PASS`: critical fieldsครบและ freshพอ
- `DEGRADED`: inferenceได้แต่ต้อง flag/ลด confidence
- `BLOCK`: schema/geometry/identityผิดหรือหลักฐาน criticalไม่พอ; ห้ามส่งโมเดลเป็นปกติ

Snapshotเก็บ canonical record refs, features, route corridor, quality/conflicts, source lineage, schema/transform versionsและ content hash

## Database and indexing

ขั้นต่ำ:

- `weather_records`: unique source record/time/location hash; BRIN time + GiST point
- `transport_records`: index service/operator/time/status
- `disaster_events`/`official_alerts`: GiST geometry + B-tree effective/end/severity/source
- `lineage`: record/field/source/transform index
- `snapshots`: unique `(request_id, input_content_hash, schema_version)`; GiST corridor
- `quarantine`: status/error/source hash/created; retention cleanup

Partition time-series tableรายเดือนเมื่อ volumeจริงjustify; อย่าเพิ่ม premature partitionโดยไม่มี test

## Implementation steps

### Phase 0 — Schema/semantic agreement

1. ร่วมคน 4ทำ field/unit/time mappingจากทุก provider
2. ร่วมคน 6 lock feature schema v1, null/missing semanticsและ online/offline code sharing
3. สร้าง data dictionary + transformation registry + severity/source priority table
4. เตรียม sanitized real input golden setที่มี normal, duplicate, conflict, stale, routeข้าม timezone/dateline
5. ตกลง quality gateกับคน 3/7

### Phase 1 — Service/storage scaffold

1. FastAPI/internal auth/health/metrics/settings
2. PostGIS migrations/tables/indexes/roles
3. repository/UoW/idempotent snapshot create
4. quarantineและ retention job
5. Docker/Testcontainers PostGIS/non-root

Exit: fresh/upgrade DB, spatial queryและ quarantine testผ่าน

### Phase 2 — Validation and normalization

1. generated input models + strict validation
2. unit/time/severity/place/geometry transformsแบบ pure functions
3. transformation version/checksumและ field lineage
4. invalid/outlier/non-finite/null vs zero tests
5. store canonical recordsด้วย idempotent upsert

### Phase 3 — Dedup/conflict

1. exact/provider/content hash rules
2. spatial-temporal candidate clustering
3. authority/freshness/confidence resolution
4. retain original evidence/conflict details
5. concurrency testยิง inputซ้ำพร้อมกัน

### Phase 4 — Geospatial/time enrichment

1. validate/densify/project/buffer route
2. ETAต่อ route sample/segment
3. spatial+temporal joins weather/disaster/closure/transport
4. admin/H3 enrichmentจาก approved boundary dataพร้อม version
5. dateline/high latitude/invalid geometry/performance tests

Exit: route corridor exposureถูกตรวจด้วย hand-calculated golden cases

### Phase 5 — Features/quality/snapshot

1. deterministic feature builderตาม schema v1
2. missing indicators/no leakage/time cutoff
3. quality score/gate/conflict summary
4. immutable snapshot/content hash/supersedes
5. endpoint create/get/validateและ generated contract tests

Exit: คน 6อ่าน snapshotและ inferenceได้โดยไม่ transform semanticsซ้ำ

### Phase 6 — Online/offline parity and operations

1. expose same feature package/CLIให้ training pipelineของคน 6
2. parity test batch vs online bitwise/tolerance
3. cleanup/backfill/rebuild CLIที่ idempotent
4. metrics: invalid/quarantine/dedup/conflict/coverage/freshness/schema drift/latency
5. load test route/event volumeและ optimize index/queryด้วย EXPLAIN evidence

### Phase 7 — Final integration/handoff

1. real provider context -> DB -> snapshotหลาย routes/timezones
2. simulate late event, changed provider update, duplicate, conflicting official/general source
3. restore DB backupและ rebuild snapshot by refs/version
4. docs/data dictionary/ERD/lineage example/completion report

## Test matrix

| Area | Must cover |
| --- | --- |
| Validation | schema mismatch, NaN/Inf, invalid time/geometry/coordinate/source |
| Units/time | C/F, mph/kmh, inches/mm, source timezone/DST, event vs ingestion time |
| Dedup | exact, updated same ID, cross-source similar, false-positive spatial proximity |
| Conflict | official vs general, newer low-authority vs older active warning, unresolved |
| Spatial | corridor edge, buffer units, dateline, multipolygon, invalid self-intersection |
| Temporal | event before/during/after trip, late arrival, forecast time tolerance |
| Features | definitions, missing indicators, no future leakage, batch/online parity |
| Persistence | concurrent idempotency, immutable snapshot, migrations/index/retention |
| Quality | fresh/stale/partial/conflicting/block boundaries |

## Acceptance checklist

- [ ] input invalidไม่ถูก dropเงียบและ quarantine traceได้
- [ ] canonical unit/time/geometryถูกต้องและ versioned
- [ ] dedup idempotentและ source conflictไม่สูญ
- [ ] hazards/weather matchเต็ม corridor + travel time
- [ ] every important fieldมี lineageถึง source
- [ ] feature schema v1ใช้ร่วม online/trainingและไม่มี leakage
- [ ] quality gate/degraded flagsส่งถึงคน 7/1
- [ ] snapshot immutable/repeatable/content-hashed
- [ ] PostGISจริง + migrations + indexes + backup/restore
- [ ] Docker integrationกับคน 4/6ผ่าน

## Branch/commit/PR breakdown

1. `contract/05-integrated-context-schema`
2. `feat/05-postgis-foundation`
3. `feat/05-normalization-lineage`
4. `feat/05-dedup-conflict`
5. `feat/05-route-corridor`
6. `feat/05-feature-quality`
7. `feat/05-snapshot-api`
8. `test/05-parity-spatial-resilience`

## Completion report requirements

สร้าง `docs/handoffs/M05-data-integration.md` พร้อม ERD, data dictionary, transform/feature schema versions, corridor/buffer formulas, quality weights/gates, lineageตัวอย่าง, quarantine summary, EXPLAIN/performance evidence, parity results, migrationsและ known coverage gaps


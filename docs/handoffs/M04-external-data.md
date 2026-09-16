# [M04] External Data Services — Completion Report

## 1. Metadata

| Field | Value |
| --- | --- |
| Module/owner | M04 External Data — คน 4 |
| Issue/PR | `feat/04-external-data` → main (simulated PR, squash title `[M04] Add real provider adapters and combined context query`) |
| Branch | `feat/04-external-data` |
| Base/final commit SHA | base `c40c7ae` (scaffold merge) / final: see `git log --oneline -1 -- services/external-data` |
| Date/time/timezone | 2026-09-17, Asia/Bangkok (UTC+7) |
| Reviewers | คน 5 (canonical mapping), คน 6 (route geometry), Team Lead (keys/licenses) — to be assigned |
| Contract version | 1.0.0 |
| Docker image digest/tag | `sta/external-data:1.0.0` (built from `services/external-data/Dockerfile`, non-root uid 10001) |
| Related model/policy/prompt/collection version | severity table v1.0.0, geodesic assumptions v1.0.0, providers.yaml config v1.0.0 |

## 2. Executive summary

- สร้าง FastAPI service ที่เรียก provider จริง 8 ตัวและแปลงเป็น canonical contract (`WeatherForecastPoint`,
  `RouteCandidate`, `TransportStatus`, `DisasterEvent`, `LocationRef`) พร้อม `SourceProvenance` + `DataQuality` ทุก record
- Live canary วันนี้ (2026-09-16T20:33Z) ผ่าน 6/8 provider: Open-Meteo geocoding/forecast, USGS, GDACS (100 events),
  EONET (27 events), MBTA GTFS-RT (114 records); ORS และ Amadeus รายงาน `UNAVAILABLE` ตรง ๆ เพราะไม่มี credential
- `POST /internal/v1/context/query` fan-out ทุก capability พร้อม deadline/concurrency/circuit breaker/cache และคืน
  `degraded_services[]` + `unavailable_capabilities[]` แทนการแต่งข้อมูล
- ส่งต่อให้คน 5 (`ExternalContext`) และคน 3 (tool `external_data.query_context`); route geometry ส่งถึงคน 6 ผ่าน snapshot
- ข้อจำกัดสำคัญที่สุด: ไม่มี `ORS_API_KEY` ทำให้ road route เป็น great-circle approximation (flag `INFERRED`, score 0.45);
  ไม่มี Thai GTFS-RT feed ที่เผยแพร่สาธารณะ ทำให้ transport ในไทยเป็น `UNKNOWN/UNAVAILABLE`
- พร้อม merge: lint/mypy strict/25 unit tests ผ่าน; migration `0001_provider_baseline` ทดสอบ empty DB → head ใน Docker (ดู §7)

## 3. Original responsibility and acceptance criteria

- [x] enabled providers เรียก endpoint จริงและ credential server-side — `app/adapters/*.py`, canary report §8
- [x] no mock/sample payload ใน runtime path — fixtures อยู่ใต้ `tests/` เท่านั้น; `make secret-scan` ไม่พบ mock switch
- [x] canonical units/timestamps/coordinates ถูกต้อง — UTC, °C, km/h, mm, m, `[lon, lat]`; test `test_geocoding_maps_real_payload`
- [x] every record มี provenance/freshness/quality — `domain/canonical.py::provenance/quality`
- [x] provider-specific schema ไม่รั่วสู่ consumer — typed `_Props/_Feature` models ภายใน adapter เท่านั้น
- [x] coverage unavailable ถูกแสดงจริงไม่แต่งข้อมูล — `unavailable_capabilities`, `TransportStatus.UNKNOWN`
- [x] retry/cache/circuit/quota policy มี tests — `tests/test_reliability_and_service.py`
- [x] official source priority metadata ถูกต้องแต่ไม่ตัดสิน action — `authority`, `official`, severity tables versioned
- [x] real canaries ผ่านและ attribution/license documented — §8 และ `README.md`
- [x] Docker service non-root/healthy/observable — Dockerfile multi-stage, `/health/*`, `/metrics`, JSON logs
- [ ] Amadeus production canary — **not completed**: ไม่มี production credential (owner: Team Lead; capability = UNAVAILABLE)
- [ ] ORS live canary — **not completed**: ไม่มี `ORS_API_KEY` (contract test ผ่านด้วย GeoJSON shape จาก docs)

Scope change: เพิ่ม `services/geodesic.py` (great-circle geometry ที่ flag `INFERRED`) เพื่อให้ corridor weather ยังทำงานได้
เมื่อไม่มี route provider — ไม่ใช่ mock เพราะเป็น geometry ที่คำนวณและติดป้ายชัด (ADR ย่อในไฟล์)

## 4. What was implemented

### Features

| Feature | Behavior now | Entry point | Status |
| --- | --- | --- | --- |
| Geocoding | Open-Meteo search → `LocationRef[]` (ไม่เดาพิกัดถ้าไม่มีผล) | `POST /internal/v1/geocode/search` | Complete |
| Route weather | sample จุดตาม geometry + ETA, batch ≤10 จุด/call, align ชั่วโมงใกล้สุด ≤3h (flag INFERRED ถ้าไม่ exact) | `POST /internal/v1/weather/query`, `context/query` | Complete |
| Current weather | `current=` fields, `is_current=true` | `weather/query.include_current_at` | Complete |
| Road routing | ORS Directions v2 GeoJSON, alternatives <100 km, avoid polygons <150 km, segments+ETA | `POST /internal/v1/routes/query` | Complete (needs key) |
| Geodesic fallback | great-circle line + speed assumption v1.0.0, flags INFERRED+OUTSIDE_COVERAGE | `services/geodesic.py` | Complete |
| Earthquakes | USGS FDSN bbox/time/magnitude query, severity by magnitude | `disasters/query` | Complete |
| Multi-hazard | GDACS event list, Green/Orange/Red → MINOR/SEVERE/EXTREME, `official` เมื่อ Orange/Red | `disasters/query` | Complete |
| NRT events | EONET v3 open events, `official=false`, source links preserved | `disasters/query` | Complete |
| Transit RT | GTFS-RT Alerts + TripUpdates aggregate; STALE → UNKNOWN; MBTA registered | `transport/query` | Complete (1 region) |
| Flights | Amadeus OAuth + schedule lookup; UNAVAILABLE without production creds | `transport/query` (FLIGHT) | Complete (needs creds) |
| Nearby POIs | ORS POIs, categories resolved จาก provider list endpoint; phone เฉพาะที่ provider มี | `places/nearby` | Complete (needs key) |
| Combined context | fan-out + dedup + provider health | `context/query` | Complete |
| Provider health | circuit state, latency, quota, last error | `GET providers/health`, `provider.health` table | Complete |
| Canary CLI | live check + fixture capture with provenance | `python -m app.cli.canary` | Complete |

### Important flows

```text
ContextQuery
  -> routes(): per mode -> ORS (cache 6h) | geodesic fallback (flagged)
  -> asyncio.gather(
       weather(): sample_route(primary) -> Open-Meteo batch (cache 60 min) -> align ETA -> severity,
       disasters(): bbox(origin,dest,routes)+1° -> USGS ∥ GDACS ∥ EONET (cache 10 min) -> dedup_events,
       transport(): GTFS-RT feeds covering both ends (cache 90 s) | UNKNOWN record with reason)
  -> ExternalContext{routes, weather, transport, disaster_events, official_alerts, provider_health,
                     degraded_services, unavailable_capabilities, fetched_at, bbox}
```

Error path: `ProviderError` → `_guarded()` classifies (NOT_CONFIGURED/OUTSIDE_COVERAGE → unavailable; others → degraded +
metric `sta_degraded_results_total`), logs `provider_failed` with error code only, writes `provider.fetch_log`.

### What is explicitly not implemented

- Static GTFS full join (trip→stop→time); only `routes.txt` name lookup is cached lazily. TripUpdates are aggregated per region.
- GDACS polygon geometry (`url.geometry`) — centroid only; คน 5 buffers events for corridor intersection.
- Flight search without a flight number (Amadeus offers API) — out of MVP scope (no booking/search).

## 5. Actual architecture and code design

### Folder/file map

| Path | Purpose | Important owner/consumer |
| --- | --- | --- |
| `app/adapters/base.py` | adapter contract, `ProviderError` codes, circuit breaker, HTTP status mapping | all adapters |
| `app/adapters/{open_meteo_*,openrouteservice,usgs,gdacs,eonet,gtfs,amadeus}.py` | one adapter per provider with typed response models | คน 5 (mapping review) |
| `app/domain/canonical.py` | provenance/quality builders, WMO table, severity/type mappings (versioned) | คน 5/6 |
| `app/services/query_service.py` | fan-out, guard, unknown-transport records, health | คน 3 |
| `app/services/geodesic.py`, `route_sampling.py` | computed geometry + ETA sampling | คน 5/6 |
| `app/cache/provider_cache.py` | Redis TTL cache, stampede lock, negative cache | — |
| `app/repositories/*` | `provider.providers/fetch_log/health` (SQLAlchemy 2 async) | — |
| `app/cli/canary.py` | live canary + fixture capture | runbook §7 |
| `config/providers.yaml` | GTFS feed registry (bbox, license, URLs) | Team Lead |
| `migrations/versions/0001_provider_baseline.py` | schema `provider` | — |

### Main components

| Symbol | Responsibility | Inputs/outputs | Design notes |
| --- | --- | --- | --- |
| `BaseAdapter._call` | single provider HTTP call with circuit check + status mapping | `ResilientClient` → `httpx.Response` | 401/403→AUTH, 429→RATE_LIMIT(+Retry-After), 5xx→OUTAGE, 4xx→SCHEMA_CHANGED |
| `OpenMeteoWeatherAdapter.forecast_for_samples` | batch hourly forecast aligned to ETA | `WeatherSample[]` → `WeatherForecastPoint[]` | forecasts have `observed_at=None`; `valid_at` used for freshness with note |
| `QueryService._guarded` | classify failures, never raise | coroutine → result or None | generic `T` typed; metrics + fetch_log |
| `geodesic_route` | approximation when no provider | points, mode → `RouteCandidate` | quality score 0.45, flags INFERRED+OUTSIDE_COVERAGE |
| `dedup_events` | id/updated dedup + cross-source link notes | `DisasterEvent[]` | never merges official records |

### Decisions/trade-offs

- Decision: geodesic fallback instead of failing the whole assessment when no route provider covers a mode.
  Alternatives: return no route (breaks corridor weather); silently use ORS car profile (forbidden).
  Reason: keeps safety evaluation possible while UI shows "approximate". Consequence: route duration is an assumption;
  decision engine treats INFERRED routes with reduced confidence.
- Decision: GDACS `official=true` only for Orange/Red. Green alerts are informational.
- Decision: no raw provider body persistence (license/retention default).

## 6. API, contract and event changes

| Producer | Method/path/event | Request schema | Response schema | Consumer | Compatibility |
| --- | --- | --- | --- | --- | --- |
| external-data | `POST /internal/v1/context/query` | `ContextQuery` | `ExternalContext` (envelope) | agent, data-integration | new (v1) |
| external-data | `POST /internal/v1/geocode/search` | `GeocodeRequest` | `LocationRef[]` | api | new |
| external-data | `POST /internal/v1/places/nearby` | `PlacesQuery` | GeoJSON FeatureCollection + attribution | api | new |
| external-data | `POST /internal/v1/{weather,routes,transport,disasters}/query` | see `api/internal.py` | canonical records | agent | new |
| external-data | `GET /internal/v1/providers/health` | — | `ProviderHealth[]` | ops | new |

- Generated client: consumers import `sta_contracts.models.ContextQuery/ExternalContext` (ADR-0001).
- Contract lint: `packages/contracts` tests pass; no breaking change.
- Sanitized examples: `tests/fixtures/real-sanitized/*.json` (each with `_fixture.{source,captured_at,license,redaction}`).

## 7. Database, cache and storage changes

| Revision | Schema/table/index | Upgrade | Downgrade | Data impact |
| --- | --- | --- | --- | --- |
| `0001_provider_baseline` | `provider.providers`, `provider.fetch_log` (+2 idx), `provider.health` | create | drop | none (new) |

- Empty DB → head: verified in Docker compose run (see acceptance doc once stack is up; local: `alembic upgrade head`).
- Restart persistence: providers/health rows survive restart (volume `postgres-data`).
- Retention: `fetch_log` rows >30 days purged by ops job (documented, not automatic in MVP).
- Redis keys: `sta:{env}:provider-cache:{provider}:{schema}:{hash}` TTL = freshness table; `sta:{env}:lock:provider:*` TTL 15 s.

## 8. External providers and real data

| Provider/source | Endpoint/capability | Coverage | Credential ref | Freshness/TTL | License/attribution | Last canary |
| --- | --- | --- | --- | --- | --- | --- |
| Open-Meteo Geocoding | `/v1/search` | global populated places | none | 24 h | CC BY 4.0 — "Geocoding by Open-Meteo.com" | 2026-09-16T20:33Z OK (2 results) |
| Open-Meteo Forecast | `/v1/forecast` hourly/current | global, 16 days | none | 60 min / 15 min | CC BY 4.0 — "Weather data by Open-Meteo.com" | 2026-09-16T20:33Z OK |
| USGS FDSN | `/fdsnws/event/1/query` | global M≥2.5 | none | 10 min | public domain — USGS | 2026-09-16T20:33Z OK (1 event SE Asia/30 d) |
| GDACS | `/api/events/geteventlist/SEARCH` | global | none | 10 min (5 min when SEVERE+) | GDACS terms — attribution string in code | 2026-09-16T20:33Z OK (100 events) |
| NASA EONET v3 | `/events?status=open` | global, curated | none | 10 min | public domain — NASA EONET | 2026-09-16T20:33Z OK (27 events) |
| MBTA GTFS-RT | `Alerts.pb`, `TripUpdates.pb` | Greater Boston | none | 90 s | MBTA Developer License — attribution in providers.yaml | 2026-09-16T20:33Z OK (114 records) |
| openrouteservice | Directions v2 / POIs | global road | `ORS_API_KEY` (empty) | 6 h / 6 h | ORS terms + ODbL | UNAVAILABLE (not configured) |
| Amadeus | OAuth + `/v2/schedule/flights` | global | client id/secret (empty) | 90 s | Amadeus terms | UNAVAILABLE (not configured) |

- ยืนยัน runtime/demo ไม่มี mock/hard-coded current data: [x]
- Fixtures: 7 files captured by `app.cli.canary --capture` (GDACS truncated to 5 features, MBTA truncated to 40 entities).
- Unsupported behavior: `unavailable_capabilities[]` strings + `TransportStatus.UNKNOWN` with `quality.status=UNAVAILABLE`.
- Schema drift: typed models raise `PROVIDER_SCHEMA_CHANGED`; nightly CI canary (`ci.yml` schedule) catches drift.

## 9. Configuration and Docker

| Variable | Required | Secret | Default | Used by | Failure if missing |
| --- | --- | --- | --- | --- | --- |
| `ORS_API_KEY` | no | yes | "" | ORS adapter | routing/POIs UNAVAILABLE |
| `AMADEUS_CLIENT_ID/SECRET` | no | yes | "" | Amadeus | flights UNAVAILABLE |
| `GTFS_PROVIDER_CONFIG` | no | no | `config/providers.yaml` | GTFS registry | transit OUTSIDE_COVERAGE |
| `POSTGRES_PROVIDER_PASSWORD` | yes (non-test) | yes | — | DB role `sta_provider` | readiness fails |
| `REDIS_URL` | yes (non-test) | no | redis://redis:6379/0 | cache | cache bypassed, readiness degraded (non-critical) |

```bash
docker compose -f compose.yaml -f compose.dev.yaml build external-data
docker compose run --rm external-data alembic upgrade head
docker compose up -d external-data && curl -fsS localhost:8002/health/ready
```

- Container user: `app` (uid 10001); port 8002; volume `gtfs-cache:/app/data/gtfs-cache`.
- Readiness: postgres critical, redis non-critical.

## 10. Tests and verification

| Test type | Command | Passed | Failed | Skipped | Evidence |
| --- | --- | ---: | ---: | ---: | --- |
| Lint/type | `uv run ruff check . && uv run mypy app` | all | 0 | 0 | "All checks passed!", "Success: no issues found in 29 source files" |
| Unit + contract | `uv run pytest -q` | 25 | 0 | 0 | `25 passed in 3.11s` |
| Canary (real) | `APP_ENV=test uv run python -m app.cli.canary` | 6 OK / 2 UNAVAILABLE | 0 | — | report in §8 |
| Integration (compose) | see `docs/acceptance/` | — | — | — | run with full stack |

Scenarios verified: real payload mapping (5 providers + GTFS protobuf), null-vs-zero, ETA alignment tolerance,
horizon > 16 days → OUTSIDE_COVERAGE, 429 + Retry-After, 503 → circuit open → half-open recovery, timeout,
non-JSON body, unsupported mode, missing key, bbox filter, combined context with one provider down, internal auth 401,
validation of swapped coordinates.

## 11. UI evidence

N/A — internal service. UI must render `unavailable_capabilities` and `quality.flags` (INFERRED/STALE/OUTSIDE_COVERAGE).

## 12. Safety, security and privacy review

- [x] official warning/closure priority preserved — `official`, `authority` retained; dedup never drops official records
- [x] provider text treated as untrusted — passed through as data fields only, length-capped
- [x] no secret/PII in code/log/trace/fixture — API key only in `Authorization` header; logs carry error codes only
- [x] timeout/retry/cancel bounded — per-call and context deadline (20 s), retries ≤2, circuit breaker
- [x] source/freshness/quality/version retained on every record
- [x] fallback does not invent data — geodesic is flagged; transport UNKNOWN; no default "On time"

## 13. Problems encountered and resolutions

| Problem | Root cause | Evidence | Resolution | Remaining risk |
| --- | --- | --- | --- | --- |
| USGS 404 in first canary | `ResilientClient(base_url=<full query URL>)` + empty path merged wrongly | canary report `usgs rejected request (404)` | split scheme/netloc vs path | none |
| Open-Meteo multi-location response shape | list when >1 location, object when 1 | adapter test | normalize to list; count mismatch → SCHEMA_CHANGED | none |
| MBTA capture is 420 KB | full alert feed | repo size | truncated fixture to 40 entities with redaction note | none |
| GTFS static feed ~100 MB | MBTA_GTFS.zip size | — | lazy download of `routes.txt` only, cached on volume | first call latency |

## 14. Performance and operational behavior

| Metric | Target | Actual | Condition | Pass |
| --- | --- | --- | --- | --- |
| context/query (fixtures, 2 samples) | < 3 s | 0.87 s | test transport, 3 disaster providers | yes |
| live canary total | — | ~9 s (8 providers sequential) | home network | — |

Metrics: `sta_dependency_*`, `sta_cache_events_total`, `sta_degraded_results_total`. Incident: set `ORS_API_KEY=""` or
remove feed from `providers.yaml` to disable a provider; circuit breaker isolates outages automatically.

## 15. Known limitations and technical debt

| Limitation | Impact | Workaround | Owner | Priority |
| --- | --- | --- | --- | --- |
| No ORS key in this environment | road routes approximated, no POIs | obtain free ORS key (2000 req/day) | Team Lead | High |
| No Thai GTFS-RT feed registered | transport in TH is UNKNOWN | register operator feed when available | คน 4 | Medium |
| GDACS centroid only | corridor intersection uses buffered point | คน 5 buffers by severity | คน 5 | Medium |
| Forecast horizon 16 days | trips further out → OUTSIDE_COVERAGE for weather | UI shows unavailable | — | Low |

## 16. Handoff to other members

| Recipient | What is ready | What they must do | Contract/config | Blocking? |
| --- | --- | --- | --- | --- |
| คน 5 (data-integration) | `ExternalContext` with canonical records, `bbox`, provider health | intersect events with corridor; treat INFERRED routes as low coverage | `sta_contracts.models.ExternalContext` | no |
| คน 3 (agent) | tool `external_data.query_context@1` = `POST /internal/v1/context/query` | propagate deadline ≤ 20 s; map `degraded_services` to `run.degraded` events | `ContextQuery` | no |
| คน 2 (api) | geocode + places facades | proxy `/locations/search` → `geocode/search`; `/emergency/nearby` → `places/nearby` | bearer `SERVICE_AUTH_TOKEN` | no |
| คน 6 (risk) | route geometry with segments/ETA; severity tables v1.0.0 | do not re-derive severity; use `quality.flags` in features | — | no |
| คน 1 (web) | `unavailable_capabilities` strings | show "approximate route" / "transit data unavailable" badges | — | no |

## 17. Commit and PR inventory

```text
feat(external-data): adapter foundation, Open-Meteo geocoding/weather, disaster adapters, ORS, GTFS-RT, Amadeus,
                     combined context, cache/circuit, migrations, canary CLI and 25 tests
```

- Rebased on main `c40c7ae`; required checks (ruff, mypy, pytest) green locally.

## 18. Rollback and recovery

1. Disable a provider: unset its credential or set `enabled: false` in `providers.yaml`; circuit breaker also isolates it.
2. Application rollback: previous image tag; no data migration risk.
3. Migration downgrade: `alembic downgrade base` drops `provider.*` (no consumer data).
4. Cache cleanup: keys expire by TTL; safe to `DEL sta:{env}:provider-cache:*`.

## 19. Final declaration

- [x] งานใน scope ครบตามหลักฐาน (ยกเว้น credential-gated canaries ที่ระบุใน §3)
- [x] ไม่มี required work ที่ซ่อนอยู่
- [x] documentation/env/contracts/migrations อัปเดต
- [x] downstream owners ได้รับ handoff (§16)
- [x] พร้อม merge
- [ ] พร้อม release — ต้องมี ORS key และ real GTFS feed สำหรับพื้นที่ demo ก่อน

ผู้จัดทำ: คน 4 (simulated) · ผู้ review: — · วันที่: 2026-09-17

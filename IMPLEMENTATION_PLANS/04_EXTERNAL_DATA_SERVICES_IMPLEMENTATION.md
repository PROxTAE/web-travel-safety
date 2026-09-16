# คนที่ 4 — External Data Services Implementation Plan

## Mission

สร้าง adapter service ที่เรียก geocoding, weather, route/transport และ disaster providers จริง แปลง responseแต่ละเจ้าเป็น canonical contract, เก็บ provenance/freshness/quality, จัดการ cache/quota/retry/failover และไม่เลือก final recommendation

ความสำคัญ: เป็นแหล่ง evidenceสดของระบบ หากไม่เสร็จ risk model/decisionไม่มีข้อมูล หาก normalizeผิดหน่วย/เวลา/พิกัดหรืออ้าง stale dataเป็นปัจจุบัน recommendationอาจอันตราย

## Ownership and dependencies

- `services/external-data/**`
- `config/providers.yaml` ภายใน module
- internal external-data OpenAPIร่วมกับคน 3/5
- schema `provider`
- real sanitized provider fixturesตาม license

Consumers: คน 3, คน 5; route base candidatesส่งให้คน 6ผ่าน snapshot

ต้องประสาน:

- คน 5เรื่อง canonical records/unit/quality
- คน 6เรื่อง route geometryและ hazard query coverage
- Team Leadเรื่อง API keys/quotas/licenses

## Provider baseline

| Capability | Provider | Endpoint/format | Credential |
| --- | --- | --- | --- |
| Geocoding | Open-Meteo Geocoding | `/v1/search` JSON | none/non-commercial; keyตาม planเมื่อจำเป็น |
| Weather | Open-Meteo Forecast | `/v1/forecast` JSON | none/non-commercial; respect license/limits |
| Road route | openrouteservice | `/v2/directions/{profile}/geojson` | `ORS_API_KEY` |
| Flight | Amadeus Self-Service production | OAuth client credentials + flight endpoints | client ID/secret |
| Transit | Registered agency GTFS/GTFS-RT feeds | static ZIP + protobuf realtime | per feed |
| Nearby emergency POI | openrouteservice POIs or an approved OSM-based provider | GeoJSON | `ORS_API_KEY` or provider key |
| Earthquake | USGS GeoJSON | official feeds/catalog | none |
| Multi-hazard | GDACS API | event list GeoJSON/JSON | noneตาม terms |
| Additional NRT hazard | NASA EONET v3 | `/events`/GeoJSON | none |

Acceptanceห้ามใช้ Amadeus test dataเป็น real production result หากยังไม่มี production credential ให้ capabilityเป็น `UNAVAILABLE`และ UI disable/อธิบาย coverage ห้ามคืนตัวอย่างเที่ยวบินแทน

## Target structure

```text
services/external-data/
├─ app/
│  ├─ main.py
│  ├─ api/internal.py
│  ├─ domain/canonical.py
│  ├─ adapters/
│  │  ├─ base.py
│  │  ├─ open_meteo_geocoding.py
│  │  ├─ open_meteo_weather.py
│  │  ├─ openrouteservice.py
│  │  ├─ amadeus.py
│  │  ├─ gtfs.py
│  │  ├─ usgs.py
│  │  ├─ gdacs.py
│  │  └─ eonet.py
│  ├─ services/
│  │  ├─ provider_selector.py
│  │  ├─ query_service.py
│  │  ├─ dedup.py
│  │  └─ quality.py
│  ├─ cache/
│  ├─ repositories/
│  ├─ observability/
│  └─ settings.py
├─ config/providers.yaml
├─ migrations/
├─ tests/fixtures/real-sanitized/
├─ pyproject.toml
└─ Dockerfile
```

## Common adapter interface

ทุก adapter implement conceptเดียวกัน:

```text
provider_name/version/kind
coverage(query) -> supported/unsupported/reason
build_request(canonical_query) -> fixed approved URL + params/body
fetch(request, timeout, cancellation) -> provider response
validate(response) -> typed provider model
normalize(provider_model) -> canonical records
provenance(response) -> SourceProvenance
health() -> ProviderHealth
```

Error contract:

`PROVIDER_AUTH`, `PROVIDER_QUOTA`, `PROVIDER_RATE_LIMIT`, `PROVIDER_TIMEOUT`, `PROVIDER_SCHEMA_CHANGED`, `PROVIDER_OUTAGE`, `OUTSIDE_COVERAGE`, `LICENSE_RESTRICTION`

Provider-specific responseห้ามรั่วออก internal API

## Canonical query behavior

- input: coordinates/bbox/route geometry, departure/arrival window, modes, requested fields, locale
- route weather sample: sample along geometryตาม distanceและ ETAของแต่ละ point ไม่ queryแค่ origin/destination; cap pointsตาม provider quotaและแนบ coverage
- disaster query: bbox/corridor + buffer + time window; source APIที่ไม่มี corridor queryให้ fetch bboxแล้วคน 5ทำ precise intersection
- transport: เลือก feedตาม geographic coverage/operator; static scheduleไม่เท่ากับ realtime status
- flight: ใช้ airport/city mappingที่ verifiedและ production endpoint; departure date/timeต้องตรง
- unit output: UTC, Celsius, km/h, mm, meters/seconds; preserve original unit metadataถ้าจำเป็น

## Provider implementation notes

### Open-Meteo Geocoding

- query `/v1/search?name=&count=&language=&countryCode=`
- debounce/cachingเกิดได้ทั้ง web/API แต่ adapter cache resultตาม normalized query/locale/country
- map timezone/country/admin/place ID; returnหลาย candidateให้ user confirm
- ไม่เดา coordinateจากชื่อถ้า no result

### Open-Meteo Weather

- request current/hourly fieldsที่ risk featureต้องใช้เท่านั้น: temperature, apparent temperature, precipitation/probability, rain/snowfall, weather code, visibility, wind/gust, timezone metadata
- ใช้ coordinatesหลายจุดเมื่อ providerรองรับและแบ่ง batchตาม limit
- align forecast timeกับ route ETA; timestampที่ไม่มี point exactไม่ interpolateเงียบ ๆ ให้ record transform flag
- `0` เป็นค่าจริง; missingเป็น `null`
- map WMO weather codeเป็น canonical category/severityด้วย versioned table แต่ไม่ตัดสิน action

### openrouteservice

- POST Directions v2 GeoJSON, Authorization header server-side
- profile mappingสำหรับ car/walk/bicycle; modeไม่รองรับคืน coverage errorไม่ปลอมเป็น car
- request alternative routes/avoid polygonsภายใน provider limits; ตรวจ 150km avoid/100km alternativesและข้อจำกัดล่าสุดผ่าน config/docs
- preserve provider route ID/geometry/distance/duration/segments/instructions/source
- routesสำหรับ flight/train/busไม่ได้มาจาก ORSอย่างเดียว

### Amadeus production

- OAuth client credential token cacheก่อน expiryพร้อม lockกัน stampede
- production base URLเท่านั้นสำหรับ acceptance; redact token
- map flight offer/status/scheduleตาม endpointที่ credentialเข้าถึง
- provider coverage/limitationต้องส่งขึ้น quality; absence of resultไม่เท่ากับ cancellation
- retry token/read callsตาม idempotencyและ response code

### GTFS / GTFS-Realtime

- `providers.yaml` ระบุ agency, country/region bbox, static URL, realtime trip updates/alerts/vehicle URLs, license, timezone, auth ref, refresh interval
- download static feed, validate tables/foreign keys/date range, versionด้วย checksum
- parse protobuf realtimeและจับคู่ trip/route/stopกับ static feed
- map delay/cancel/service alert/freshness; feed timestampเกิน TTLเป็น STALE
- ไม่อ้าง global coverage; unsupported regionส่ง explicit `OUTSIDE_COVERAGE`

### Nearby emergency places

- query police/fire/rescue, hospital/clinic และ embassy/consulateจาก approved real POI providerตาม coordinate/radius/category
- preserve provider POI ID, geometry, category, name, source/attributionและ fetched time
- POI listingไม่ใช่ official emergency number; responseต้องไม่สร้าง phoneถ้า providerไม่มี
- cap radius/result count, validate geometryและไม่ส่ง exact user coordinateเข้า log
- provider coverage/termsไม่อนุญาตให้ return `UNAVAILABLE`; ห้าม scrape search engineหรือใช้ sample POIs

### USGS

- consume GeoJSON feed/catalogตาม time/bbox/magnitudeที่เหมาะสม
- preserve event ID, magnitude, depth, geometry, update time, detail URL
- severity mappingเป็น versioned transform; official source priority
- dedupด้วย USGS ID/updated timestamp

### GDACS

- use official Swagger/API event list, filter event type/date/alert level/geometry
- preserve alert level, episode/event ID, geometry, from/to dates, source/resource URL
- attribution `Global Disaster Awareness and Coordination System, GDACS`
- API/feed schemaแตกต่างกันให้แยก provider modelแต่ normalizeเหมือนกัน

### NASA EONET

- v3 current stable; query open events/category/bbox/daysตาม docs
- map severe storms, wildfires, volcanoes, floods/iceฯลฯ
- EONETเป็น curated near-real-time metadata; retain underlying source linksและอย่าเรียกว่า official warningถ้า authorityไม่ใช่ official

## Cache, quota and failover policy

- Redis cache keyรวม provider, schema version, normalized spatial/time query, fields, locale
- TTLตาม shared freshness tableและ provider update rate; negative cacheสั้นมาก
- stale-while-revalidateใช้ได้เฉพาะ policyอนุมัติ; recordต้อง `STALE`และมี `expires_at`
- distributed lockป้องกัน cache stampede
- per-provider concurrency/rate limiterและ quota metrics
- circuit breakerเปิดหลัง threshold; half-open probe; serviceอื่นยังทำงาน
- retryเฉพาะ 408/429/temporary 5xx, respect `Retry-After`, exponential backoff+jitter, deadlineรวม
- backup providerไม่รวม recordแบบไม่บอก source; conflictเก็บทั้งสองและให้คน 5resolve

## Implementation steps

### Phase 0 — Provider governance

1. สร้าง provider registry matrix: capability, coverage, terms/license, quota, attribution, retention, credential owner, health URL
2. ให้ Team Leadอนุมัติ provider/account; บันทึก link official docs
3. นิยาม canonical modelsกับคน 5และ required feature fieldsกับคน 6
4. สร้าง real sanitized fixturesโดยเรียก providerจริง 1 ครั้ง, ลบ key/PII, เพิ่ม metadata; ตรวจว่า termsอนุญาต
5. ระบุ unsupported coverage/degraded UXกับคน 1/2

### Phase 1 — Service foundation

1. FastAPI/internal auth/health/metrics/settings
2. base adapter/interface, error mapping, shared HTTP transport
3. timeout/retry/circuit/rate/quota/cache middleware
4. provider health repository/migrations
5. Docker non-root/TLS CA/timezone UTC

### Phase 2 — Geocoding and weather

1. typed provider response modelsที่ `extra=forbid` หรือ explicit drift capture
2. normalization/units/time/source/quality
3. route point batchingและ ETA alignment
4. cache/health/contract tests + real canary
5. expose internal endpoints

Exit: Bangkok/Chiang Maiและ international location queryจริงได้ พร้อม forecast/freshness

### Phase 3 — Disaster sources

1. USGS adapter
2. GDACS adapter
3. EONET adapter
4. cross-source preliminary dedup by IDs/content hash; ห้ามลบ conflict
5. authority priority metadata, bbox/time filter, GeoJSON validation
6. schema drift/nightly canary

Exit: safety mapได้รับ eventsจริงและ source linkเปิดได้

### Phase 4 — Routing

1. ORS auth/profile/GeoJSON adapter
2. alternative/avoid-area validationตาม limits
3. route segments/duration/distance/instructions normalization
4. coverage/error/cache tests
5. handoff geometryกับคน 5/6และ mapกับคน 1ผ่าน API

จากนั้นเพิ่ม nearby emergency POI endpointและ adapterจริงสำหรับ police/hospital/embassy พร้อม attribution/coverage tests

### Phase 5 — Transport/flight

1. GTFS registry + static ingestion/validation/version
2. GTFS-RT parser/join/freshness/status
3. Amadeus production token/client/response mappingหาก credentialพร้อม
4. explicit unavailable stateต่อ unsupported/credential missing
5. tests cancellation/delay/no realtime/stale/unknown

Exit: อย่างน้อย 1 real GTFS-RT regionที่ทีมสาธิตต้องทำงาน; flight capabilityเปิดเฉพาะ production dataจริง

### Phase 6 — Combined context and reliability

1. `/context/query` fan-out independent callsด้วย bounded concurrency
2. combine canonical records/provider healthโดยไม่ตัดสิน risk
3. cancellation propagationและ overall deadline
4. cache stampede/quota/circuit tests
5. metrics dashboards: latency, errors, quota, cache hit, schema drift, disagreements

### Phase 7 — Final verification

1. real canaryทุก enabled providerและบันทึก fetched/source times
2. simulate 429/timeout/malformed/stale/main failure+backup
3. ตรวจ unit/timezone/coordinate orderกับคน 5
4. verify no key/raw sensitive dataใน logs/fixtures/DB
5. provider license/attribution docs, completion report, PR

## Test matrix

| Layer | Must cover |
| --- | --- |
| Adapter unit | provider JSON/protobuf -> canonical, null vs zero, units, timezone, enum unknown |
| Contract | internal OpenAPI inputs/outputs, provenance/quality required |
| HTTP | auth, 200/204/400/401/403/404/408/429/5xx, invalid JSON, oversized body |
| Cache | hit/miss/TTL/stale/negative/stampede/schema version |
| Coverage | supported/unsupported country/mode/time/feed |
| Geospatial | bbox dateline, reversed coordinates, invalid geometry, route sample coverage |
| Reliability | timeout/cancel/retry-after/circuit/backup/quota exhausted |
| Canary | live schema/required fields/source time for every enabled provider |

## Acceptance checklist

- [ ] enabled providersเรียก endpointจริงและ credential server-side
- [ ] no mock/sample payloadใน runtime path
- [ ] canonical units/timestamps/coordinatesถูกต้อง
- [ ] every recordมี provenance/freshness/quality
- [ ] provider-specific schemaไม่รั่วสู่ consumer
- [ ] coverage unavailableถูกแสดงจริงไม่แต่งข้อมูล
- [ ] retry/cache/circuit/quota policyมี tests
- [ ] official source priority metadataถูกต้องแต่ไม่ตัดสิน action
- [ ] real canariesผ่านและ attribution/license documented
- [ ] Docker service non-root/healthy/observable

## Branch/commit/PR breakdown

1. `contract/04-provider-canonical-schema`
2. `feat/04-adapter-foundation`
3. `feat/04-geocoding-weather`
4. `feat/04-disaster-adapters`
5. `feat/04-route-adapter`
6. `feat/04-gtfs-transport`
7. `feat/04-flight-provider`
8. `feat/04-combined-context`
9. `test/04-provider-resilience-canary`

## Completion report requirements

สร้าง `docs/handoffs/M04-external-data.md` พร้อม provider matrix, exact enabled endpoints/coverage, account/key setupโดยไม่เผยค่า, field mapping, TTL/retry/quota, fixture provenance/license, live canary timestamps, failure matrix และ unsupported capabilitiesที่ UIต้องแสดง

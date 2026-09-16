# 00 — API and Data Contracts

สถานะ: **baseline v1** ทุกคนต้อง implement ตามเอกสารนี้ก่อน หากพบว่าต้องเปลี่ยน ให้แก้ JSON Schema/OpenAPI พร้อม consumer tests ใน PR เดียวกันและขอ review จาก owner ทุกฝั่ง ห้ามตกลงกันเฉพาะในแชตแล้วเปลี่ยนโค้ดเงียบ ๆ

ตัวอย่าง JSON ในเอกสารนี้เป็นตัวอย่างโครงสร้าง ไม่ใช่ mock runtime data

## 1. Cross-service conventions

- Public prefix: `/api/v1`; internal prefix: `/internal/v1`
- Content type: `application/json; charset=utf-8`; map geometry ใช้ GeoJSON RFC 7946
- UUID เป็น lowercase canonical string
- Timestamp เป็น ISO-8601 UTC เช่น `2026-09-17T08:30:00Z`
- Date/time ที่ผู้ใช้เลือกเก็บทั้ง `departure_time` ที่มี offset และ IANA `timezone`
- coordinate/GeoJSON ใช้ `[longitude, latitude]`
- enum ส่ง uppercase ตามที่กำหนด; unknown provider value map เป็น `UNKNOWN` ไม่ส่ง string ใหม่โดยพลการ
- ทุก request header ภายในมี `X-Request-ID`, `X-Correlation-ID`, `traceparent`, `X-Contract-Version: 1`
- Mutating public request รองรับ `Idempotency-Key`; retry ด้วย key เดิมต้องคืน resource/result เดิม
- Error ห้ามเผย stack trace, SQL, secret หรือ provider credential
- ทุก external/internal client ตั้ง connect/read/total timeout และส่ง cancellation เมื่อ upstream ยกเลิก

### Standard success envelope

```json
{
  "data": {},
  "meta": {
    "request_id": "uuid",
    "correlation_id": "uuid",
    "contract_version": "1.0.0",
    "generated_at": "ISO-8601",
    "degraded_services": []
  }
}
```

Public list เพิ่ม `page`:

```json
{
  "data": [],
  "meta": { "request_id": "uuid", "contract_version": "1.0.0" },
  "page": { "cursor": null, "next_cursor": null, "has_more": false }
}
```

### Standard error envelope

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable message safe for users",
    "field_errors": [{ "path": "destination", "code": "REQUIRED" }],
    "retryable": false,
    "retry_after_seconds": null
  },
  "meta": {
    "request_id": "uuid",
    "correlation_id": "uuid",
    "contract_version": "1.0.0"
  }
}
```

Stable error codes ขั้นต่ำ:

`VALIDATION_ERROR`, `AUTHENTICATION_REQUIRED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `IDEMPOTENCY_CONFLICT`, `RATE_LIMITED`, `DEPENDENCY_TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `INSUFFICIENT_EVIDENCE`, `UNSUPPORTED_COVERAGE`, `POLICY_VALIDATION_FAILED`, `INTERNAL_ERROR`

## 2. Shared enums

```text
RiskLevel          = LOW | MEDIUM | HIGH | UNKNOWN
ActionCode         = NORMAL | CHANGE_ROUTE | DELAY | AVOID
RunStatus          = QUEUED | RUNNING | NEEDS_INPUT | COMPLETED | PARTIAL | FAILED | CANCELLED
TravelMode         = FLIGHT | TRAIN | BUS | CAR | WALK | BICYCLE | MULTIMODAL
DataStatus         = FRESH | STALE | UNAVAILABLE | CONFLICTING | PARTIAL
Severity           = INFO | MINOR | MODERATE | SEVERE | EXTREME | UNKNOWN
SourceAuthority    = OFFICIAL | INTERGOVERNMENTAL | LICENSED_PROVIDER | COMMUNITY | UNKNOWN
FeedbackCategory   = HELPFUL | INCORRECT | STALE | UNSAFE | ROUTE_ISSUE | SOURCE_ISSUE | OTHER
ConsentType        = LOCATION_ONCE | LOCATION_LIVE | ALERT_NOTIFICATION | ANALYTICS | EMERGENCY_PROFILE
DeliveryChannel    = IN_APP | EMAIL | SMS | PUSH
ProviderKind       = GEOCODING | WEATHER | ROUTE | FLIGHT | TRANSIT | DISASTER | EMERGENCY_DIRECTORY
QualityFlag        = MISSING | STALE | CONFLICTING | INFERRED | INCOMPLETE | OUTSIDE_COVERAGE
```

ห้ามใช้ `risk_level` เป็น action โดยตรง `HIGH` มักนำไป `AVOID` แต่ action ต้องผ่าน policy ใน Module 07

## 3. Core schema summaries

ไฟล์จริงต้องอยู่ใน `packages/contracts/jsonschema/` และ generate Pydantic/TypeScript clients จาก source เดียวกัน ไม่ copy model แยก 8 ชุด

### 3.1 LocationRef

```json
{
  "place_id": "provider-scoped-id",
  "display_name": "Bangkok, Thailand",
  "coordinates": { "type": "Point", "coordinates": [100.5018, 13.7563] },
  "country_code": "TH",
  "admin1": "Bangkok",
  "timezone": "Asia/Bangkok",
  "provider": "open_meteo",
  "confirmed_by_user": true
}
```

Validation:

- longitude `[-180, 180]`, latitude `[-90, 90]`
- `country_code` ISO-3166-1 alpha-2 uppercase
- การขอ recommendation ต้อง `confirmed_by_user=true` ทั้ง origin/destination
- เก็บ exact coordinate เท่าที่จำเป็นและอยู่ภายใต้ retention/consent

### 3.2 TravelRequest

```json
{
  "request_id": "uuid",
  "trip_id": "uuid",
  "conversation_id": "uuid-or-null",
  "origin": { "$ref": "LocationRef" },
  "destination": { "$ref": "LocationRef" },
  "departure_time": "2026-09-20T09:30:00+07:00",
  "return_time": null,
  "travel_modes": ["TRAIN"],
  "preferences": {
    "prefer_safer_route": true,
    "prefer_lower_cost": false,
    "prefer_lower_emissions": false,
    "max_extra_duration_minutes": 90,
    "avoid_tolls": false,
    "accessibility": []
  },
  "question": "Is this route safe?",
  "locale": "th-TH",
  "timezone": "Asia/Bangkok",
  "live_location_consent_id": null
}
```

Rules:

- departure ต้องไม่ย้อนหลังเกิน policy ยกเว้น endpoint วิเคราะห์ historical ที่แยกต่างหาก
- `return_time > departure_time`
- `travel_modes` อย่างน้อย 1 ค่าและต้องตรวจ coverage
- question จำกัด 2,000 Unicode characters และ normalize control chars
- API สร้าง/ยืนยัน `request_id`; client ห้าม override user ownership

### 3.3 SourceProvenance

```json
{
  "source_id": "uuid",
  "provider": "USGS",
  "provider_record_id": "us7000xxxx",
  "authority": "OFFICIAL",
  "source_url": "https://...",
  "license": "provider-specific",
  "observed_at": "ISO-8601-or-null",
  "published_at": "ISO-8601-or-null",
  "fetched_at": "ISO-8601",
  "expires_at": "ISO-8601-or-null",
  "content_hash": "sha256-hex",
  "schema_version": "1.0.0"
}
```

ทุก fact ที่มีผลต่อ decision ต้องย้อนกลับไปหา source ได้ หาก provider ไม่ให้ `observed_at` ต้องเป็น `null` และตั้ง quality flag ห้ามใช้ `fetched_at` ปลอมเป็นเวลาสังเกต

### 3.4 DataQuality

```json
{
  "status": "FRESH",
  "score": 0.91,
  "flags": [],
  "coverage": 0.88,
  "completeness": 0.95,
  "freshness_seconds": 143,
  "conflicts": [],
  "notes": []
}
```

`score` อยู่ `[0,1]` และสูตร/weights มี version ห้ามใช้ score เพียงค่าเดียวเพื่อซ่อน flags

### 3.5 WeatherForecastPoint

Required fields:

`id`, `location`, `valid_at`, `temperature_c`, `apparent_temperature_c`, `precipitation_mm`, `precipitation_probability`, `snowfall_cm`, `wind_speed_kmh`, `wind_gust_kmh`, `visibility_m`, `weather_code`, `severity`, `quality`, `source`

ค่าที่ provider ไม่มีให้เป็น `null` พร้อม quality flag ไม่แทนด้วย 0

### 3.6 TransportStatus

Required fields:

`id`, `mode`, `operator`, `service_number`, `origin_stop`, `destination_stop`, `scheduled_departure`, `estimated_departure`, `scheduled_arrival`, `estimated_arrival`, `status`, `delay_minutes`, `cancellation`, `quality`, `source`

`status = ON_TIME | DELAYED | CANCELLED | DISRUPTED | UNKNOWN` โดย `ON_TIME` ต้องมีข้อมูล real-time หรือเงื่อนไขที่ policy อนุมัติ ห้ามสรุปจากการไม่มี alert

### 3.7 DisasterEvent / OfficialAlert

Required fields:

`event_id`, `event_type`, `title`, `description`, `severity`, `geometry`, `effective_at`, `ends_at`, `instruction`, `official`, `quality`, `source`

`event_type = EARTHQUAKE | CYCLONE | STORM | FLOOD | WILDFIRE | VOLCANO | LANDSLIDE | EXTREME_TEMPERATURE | HEALTH | TRANSPORT_CLOSURE | OTHER`

### 3.8 RouteCandidate

```json
{
  "route_id": "uuid",
  "provider_route_id": "opaque-id",
  "label": "RECOMMENDED",
  "mode": "CAR",
  "geometry": { "type": "LineString", "coordinates": [] },
  "segments": [],
  "distance_m": 890000,
  "duration_seconds": 53700,
  "transfers": 0,
  "exposure": {
    "score": 0.22,
    "hazard_event_ids": [],
    "weather_window_ids": [],
    "closed": false
  },
  "risk_level": "LOW",
  "quality": { "$ref": "DataQuality" },
  "sources": []
}
```

`label = ORIGINAL | RECOMMENDED | FASTEST | LOWEST_RISK | ALTERNATIVE`; route ที่มี official closure ต้อง `closed=true` และไม่เป็น recommended

### 3.9 IntegratedTravelContext

Required fields:

- identity: `snapshot_id`, `request_id`, `trip_id`, `schema_version`, `feature_schema_version`
- time/route: `travel_window`, `route_candidates`, `route_corridor_geojson`
- evidence: `weather`, `transport`, `disaster_events`, `official_alerts`
- derived: `features`, `quality_summary`, `conflict_summary`
- provenance: `source_ids`, `created_at`, `content_hash`

Snapshot immutable เมื่อสร้างแล้ว การ refresh สร้าง `snapshot_id` ใหม่และ link `supersedes_snapshot_id`

### 3.10 RiskAssessment

```json
{
  "assessment_id": "uuid",
  "snapshot_id": "uuid",
  "route_id": "uuid",
  "score": 0.73,
  "probability_high": 0.68,
  "risk_level": "HIGH",
  "uncertainty": 0.12,
  "reason_codes": ["SEVERE_WEATHER_CORRIDOR"],
  "safety_overrides": [],
  "model": {
    "name": "route-risk-baseline",
    "version": "semver-or-mlflow-version",
    "feature_schema_version": "1.0.0"
  },
  "quality": { "$ref": "DataQuality" },
  "created_at": "ISO-8601"
}
```

`reason_codes` เป็น controlled vocabulary ใน contract; explanation จาก LLM ห้ามอ้าง causality เกินข้อมูล

### 3.11 RetrievedEvidence

Required fields:

`evidence_id`, `document_id`, `authority`, `title`, `source_url`, `page`, `section`, `language`, `effective_at`, `expires_at`, `passage`, `retrieval_score`, `rerank_score`, `collection_version`, `content_hash`

ถ้า score ต่ำกว่า threshold ให้ไม่คืน passage และเพิ่ม limitation `NO_RELIABLE_KNOWLEDGE_EVIDENCE`

### 3.12 DecisionResult

```json
{
  "decision_id": "uuid",
  "request_id": "uuid",
  "snapshot_id": "uuid",
  "action_code": "CHANGE_ROUTE",
  "risk_level": "HIGH",
  "confidence": 0.86,
  "selected_route_id": "uuid",
  "rules_fired": ["OFFICIAL_WARNING_PRIORITY", "SAFER_ROUTE_AVAILABLE"],
  "escalation_required": false,
  "summary": "...",
  "reasons": [],
  "immediate_actions": [],
  "citations": [],
  "limitations": [],
  "versions": {
    "policy": "1.0.0",
    "prompt": "1.0.0",
    "llm_model": "provider-model-id",
    "contract": "1.0.0"
  },
  "validation": { "schema": true, "citations": true, "locked_action": true },
  "created_at": "ISO-8601"
}
```

### 3.13 RecommendationResponse

นี่คือ object หลักที่ Web ใช้:

```json
{
  "recommendation_id": "uuid",
  "request_id": "uuid",
  "trip_id": "uuid",
  "conversation_id": "uuid",
  "status": "COMPLETED",
  "action_code": "CHANGE_ROUTE",
  "risk_level": "HIGH",
  "confidence": 0.86,
  "short_summary": "Use the safer route to avoid severe weather.",
  "immediate_actions": [],
  "reasons": [],
  "primary_route": { "$ref": "RouteCandidate" },
  "alternatives": [],
  "alerts": [],
  "emergency_instructions": [],
  "official_contacts": [],
  "sources": [],
  "freshness": {
    "observed_at": "ISO-8601-or-null",
    "fetched_at": "ISO-8601",
    "expires_at": "ISO-8601-or-null"
  },
  "limitations": [],
  "degraded_services": [],
  "versions": {},
  "created_at": "ISO-8601"
}
```

`official_contacts` ต้องมี `country_code`, `service_type`, `label`, `phone`, `source_url`, `effective_at`, `verified_at`; ห้ามแสดงเบอร์หมดอายุหรือใช้เบอร์จาก LLM

## 4. Public API contract — owner คน 2

ทุก endpoint ใช้ JWT ยกเว้น `/health/*`, OIDC callback และ public metadata ที่ระบุ

| Method | Path | Purpose | Important response |
| --- | --- | --- | --- |
| GET | `/api/v1/me` | profile/consent summary | `UserProfile` |
| PATCH | `/api/v1/me` | locale/timezone/profile | updated profile |
| GET/PUT | `/api/v1/me/emergency-profile` | medical/contact/insurance refs | encrypted/minimized profile |
| POST | `/api/v1/consents` | opt in/out with version | `ConsentRecord` |
| GET | `/api/v1/locations/search?q=&locale=` | proxied real geocoding | `LocationRef[]` |
| POST | `/api/v1/trips` | create trip | `Trip` |
| GET/PATCH/DELETE | `/api/v1/trips/{trip_id}` | manage own trip | `Trip`/204 |
| POST | `/api/v1/trips/{trip_id}/assessments` | start real assessment | 202 `RunRef` |
| GET | `/api/v1/runs/{request_id}` | poll run | status/progress/result link |
| GET | `/api/v1/runs/{request_id}/events` | SSE progress/final | SSE stream |
| GET | `/api/v1/recommendations/{id}` | final result | `RecommendationResponse` |
| POST | `/api/v1/trips/{trip_id}/apply-route` | apply selected route | new trip revision + new assessment ref |
| GET | `/api/v1/safety/events?bbox=&at=&layers=` | map data | canonical event summaries |
| GET | `/api/v1/conversations` | recent chats | conversation summaries |
| POST | `/api/v1/conversations/{id}/messages` | follow-up | 202 `RunRef` |
| POST | `/api/v1/feedback` | explicit feedback | `FeedbackEvent` |
| POST/DELETE | `/api/v1/alert-subscriptions` | live alert opt-in/out | subscription |
| GET | `/api/v1/emergency/contacts?lat=&lon=` | verified local directory | official contacts |
| GET | `/api/v1/emergency/nearby?type=&lat=&lon=` | hospital/police/embassy lookup | GeoJSON POIs + sources |

DELETE trip/profile เป็น soft delete + async retention cleanup; response ต้องบอก deletion status

### RunRef

```json
{
  "request_id": "uuid",
  "status": "QUEUED",
  "events_url": "/api/v1/runs/{id}/events",
  "poll_url": "/api/v1/runs/{id}",
  "submitted_at": "ISO-8601"
}
```

## 5. Internal service API contracts

Internal endpoints ใช้ service authentication/network policy และไม่รับ browser token โดยตรง

### 5.1 Agent — owner คน 3

| Method | Path | Input | Output |
| --- | --- | --- | --- |
| POST | `/internal/v1/runs` | normalized `TravelRequest` + approved context/budget | `RunRef` |
| GET | `/internal/v1/runs/{id}` | id | Agent run state without chain-of-thought |
| POST | `/internal/v1/runs/{id}/resume` | missing user fields / follow-up | `RunRef` |
| POST | `/internal/v1/runs/{id}/cancel` | reason | cancel status |

ห้าม expose hidden chain-of-thought; เก็บเฉพาะ execution plan แบบ structured, tools called, evidence IDs, policy/model versions และ errors

### 5.2 External Data — owner คน 4

| Method | Path | Input | Output |
| --- | --- | --- | --- |
| POST | `/internal/v1/geocode/search` | query, locale, country | `LocationRef[]` |
| POST | `/internal/v1/weather/query` | coordinate samples + time window | forecasts/observations |
| POST | `/internal/v1/routes/query` | locations, modes, preferences | raw-canonical `RouteCandidate[]` |
| POST | `/internal/v1/transport/query` | route/travel time/mode | `TransportStatus[]` |
| POST | `/internal/v1/disasters/query` | bbox/corridor/time/types | events + alerts |
| POST | `/internal/v1/places/nearby` | coordinate, radius, type, locale | sourced GeoJSON POIs |
| POST | `/internal/v1/context/query` | combined query | all canonical records + provider health |
| GET | `/internal/v1/providers/health` | none | quota/latency/status, no secrets |

### 5.3 Data Integration — owner คน 5

| Method | Path | Input | Output |
| --- | --- | --- | --- |
| POST | `/internal/v1/snapshots` | request + canonical provider records | `IntegratedTravelContext` |
| GET | `/internal/v1/snapshots/{id}` | id | immutable snapshot |
| POST | `/internal/v1/snapshots/{id}/validate` | optional strict flag | quality report |

Create snapshot รองรับ idempotency จาก `request_id + input_content_hash + schema_version`

### 5.4 Risk & Knowledge — owner คน 6

| Method | Path | Input | Output |
| --- | --- | --- | --- |
| POST | `/internal/v1/risk/assess` | snapshot + route IDs | `RiskAssessment[]` |
| POST | `/internal/v1/knowledge/retrieve` | hazard/location/action/locale | `RetrievedEvidence[]` |
| POST | `/internal/v1/routes/evaluate` | snapshot/routes/avoid geometries | ranked `RouteCandidate[]` |
| POST | `/internal/v1/evidence/package` | snapshot id + locale | risk + evidence + routes |
| GET | `/internal/v1/models/current` | none | approved model metadata |
| GET | `/internal/v1/knowledge/status` | none | collection version/cutoff |

### 5.5 Decision Engine — owner คน 7

| Method | Path | Input | Output |
| --- | --- | --- | --- |
| POST | `/internal/v1/decisions` | request + snapshot summary + risk/evidence/routes | `DecisionResult` |
| POST | `/internal/v1/decisions/validate` | existing result/evidence package | validation report |
| GET | `/internal/v1/policies/current` | none | policy version/checksum |

### 5.6 Recommendation — owner คน 8

| Method | Path | Input | Output |
| --- | --- | --- | --- |
| POST | `/internal/v1/recommendations` | validated decision + data summaries | `RecommendationResponse` |
| GET | `/internal/v1/recommendations/{id}` | id | response |
| POST | `/internal/v1/feedback` | authorized feedback | `FeedbackEvent` |
| POST | `/internal/v1/subscriptions` | consent + trip scope/channel | subscription |
| DELETE | `/internal/v1/subscriptions/{id}` | owner/reason | 204 |
| POST | `/internal/v1/alerts/evaluate` | changed condition | delivery decisions |
| GET | `/internal/v1/emergency/contacts?country_code=&subdivision=` | verified location-specific directory | official contacts + verification metadata |

## 6. SSE contract

Endpoint: `GET /api/v1/runs/{request_id}/events`

Headers:

```text
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

Events มี `id`, `event`, `data` JSON; reconnect ส่ง `Last-Event-ID`

| Event | Payload | UI |
| --- | --- | --- |
| `run.accepted` | request/status/time | แสดง queued |
| `run.progress` | stage, percent nullable, message_key | แสดง Checking weather/transport/disaster ฯลฯ |
| `run.needs_input` | missing_fields, prompt_key | เปิด form ขอข้อมูล ห้ามเดา |
| `run.degraded` | service, reason, retrying | banner เตือน |
| `run.completed` | recommendation_id, result URL | fetch final แล้วปิด stream |
| `run.failed` | safe error code/message/retryable | error state |
| `heartbeat` | server time | ไม่แสดง |

Stage controlled values:

`VALIDATING`, `FETCHING_EXTERNAL_DATA`, `INTEGRATING_DATA`, `ASSESSING_RISK`, `RETRIEVING_GUIDANCE`, `EVALUATING_ROUTES`, `MAKING_DECISION`, `EXPLAINING`, `FORMATTING_RESPONSE`

SSE ห้ามส่ง provider raw body, prompt, chain-of-thought, access token หรือ PII

## 7. Redis key/event conventions

Key ทุกอันมี environment prefix:

```text
sta:{env}:rate:{subject}:{endpoint}
sta:{env}:idempotency:{user_id}:{key}
sta:{env}:provider-cache:{provider}:{schema}:{hash}
sta:{env}:provider-health:{provider}
sta:{env}:run:{request_id}:status
sta:{env}:run:{request_id}:events
sta:{env}:lock:{resource}:{id}
```

- ห้ามเก็บ raw JWT/medical note/full live-location historyใน Redis
- ทุก cache key ต้องรวม location bucket, time window, locale, provider และ schema versionตามความเกี่ยวข้อง
- กำหนด TTL ทุก key; ห้ามสร้าง key ถาวรโดยไม่ review
- Pub/Sub/Streams event envelope: `event_id`, `event_type`, `occurred_at`, `producer`, `schema_version`, `correlation_id`, `payload`

## 8. PostgreSQL table ownership

ชื่อจริงอาจปรับด้วย migration แต่ relationship/owner ต้องคงไว้

### `identity` / `travel` — คน 2

- `identity.user_profiles(id, subject_id, locale, timezone, created_at, updated_at, deleted_at)`
- `identity.consents(id, user_id, type, granted, policy_version, granted_at, revoked_at)`
- `identity.emergency_profiles(id, user_id, encrypted_payload, key_version, updated_at)`
- `travel.trips(id, user_id, revision, origin_json, destination_json, departure_time, return_time, modes, preferences_json, selected_route_id, status, ...)`
- `travel.requests(id, trip_id, user_id, idempotency_key_hash, status, contract_version, created_at, completed_at, ...)`

### `agent` — คน 3

- LangGraph checkpointer tablesตาม official adapter
- `agent.runs(request_id, conversation_id, graph_version, prompt_version, policy_version, budgets_json, final_state, ...)`
- `agent.tool_calls(id, request_id, tool_name, input_hash, status, started_at, duration_ms, error_code)` — ห้ามเก็บ secret/raw sensitive payload

### `provider` — คน 4

- `provider.providers(id, kind, name, enabled, coverage_json, license_url, config_version)`
- `provider.fetch_log(id, provider_id, query_hash, http_status, fetched_at, expires_at, content_hash, quality_json)`
- `provider.health(provider_id, status, latency_ms, quota_remaining, checked_at)`

Raw provider body เก็บได้เฉพาะ license/retention อนุญาตและต้อง encrypt/expire; ค่าเริ่มต้นคือไม่เก็บ

### `integration` — คน 5

- `integration.weather_records`, `transport_records`, `disaster_events`, `official_alerts`
- `integration.lineage(record_id, field_path, source_id, transform_version)`
- `integration.snapshots(id, request_id, content_hash, schema_version, quality_json, route_corridor geometry, created_at, supersedes_id)`
- records geospatial ใช้ GiST index; dedup keys unique ตาม source/event/content hash

### `knowledge` — คน 6

- `knowledge.model_versions(id, name, version, stage, feature_schema, metrics_json, artifact_uri, checksum, approved_by, approved_at)`
- `knowledge.risk_assessments(...)`
- `knowledge.documents(id, authority, title, source_url, language, effective_at, expires_at, checksum, review_status)`
- `knowledge.chunks(id, document_id, section, page, text_hash, qdrant_point_id, collection_version)`
- `knowledge.route_evaluations(...)`

### `decision` — คน 7

- `decision.policy_versions(id, version, checksum, status, approved_by, approved_at)`
- `decision.prompt_versions(...)`
- `decision.audit_traces(id, request_id, input_hashes, rules_fired, locked_action, llm_output_hash, validation_json, versions_json, created_at)`

### `recommendation` — คน 8

- `recommendation.recommendations(id, request_id, decision_id, action, risk, response_json, expires_at, created_at)`
- `recommendation.feedback(id, user_id, recommendation_id, category, text_redacted, review_status, created_at)`
- `recommendation.subscriptions(id, user_id, trip_id, channel, consent_id, status, cooldown_until, created_at)`
- `recommendation.delivery_log(id, subscription_id, event_hash, channel, status, provider_message_id, attempted_at)`
- `recommendation.safety_review_queue(id, feedback_id, severity, status, assigned_to, created_at)`
- `recommendation.emergency_contacts(id, country_code, subdivision, service_type, labels_json, phone, source_url, authority, effective_at, verified_at, review_due_at, status, checksum)`

## 9. Contract generation and verification

Source of truth:

```text
packages/contracts/
├─ openapi/public-api.yaml
├─ openapi/internal-agent.yaml
├─ openapi/internal-external-data.yaml
├─ openapi/internal-data-integration.yaml
├─ openapi/internal-risk-knowledge.yaml
├─ openapi/internal-decision.yaml
├─ openapi/internal-recommendation.yaml
├─ jsonschema/common/*.schema.json
├─ examples/real-sanitized/*
└─ scripts/
```

CI บังคับ:

1. lint OpenAPI/JSON Schema
2. validate example ทุกไฟล์
3. generate TypeScript types/client ไป `packages/contracts/generated/typescript`
4. generate Python models/client ไป `packages/contracts/generated/python`
5. fail หาก generate แล้ว working tree เปลี่ยน
6. run provider/consumer contract tests
7. detect breaking change; breaking change ต้องใช้ `/v2` หรือมี deprecation window/adapter

ห้ามแก้ generated code ด้วยมือ

## 10. Contract acceptance checklist

- [ ] ทุก request/response validate ด้วย generated schema ทั้ง producer และ consumer
- [ ] enum, nullability, coordinate order, timestamp และ unit ตรงกัน
- [ ] response ทุกระดับมี source/freshness/quality/version เมื่อเกี่ยวกับ safety
- [ ] error code stable และมี timeout/degraded tests
- [ ] idempotency test ผ่านเมื่อยิงซ้ำและเมื่อ payload ต่างแต่ key เดิม
- [ ] SSE reconnect ผ่านและไม่ส่ง event ซ้ำโดยไม่มี event id
- [ ] ไม่มี PII/secret/raw prompt/chain-of-thought ใน contract examples/log
- [ ] DB migration มี downgrade หรือ documented forward-fix และไม่แตะ schema คนอื่นโดยไม่ review
- [ ] real sanitized fixtures ระบุ source URL, captured_at, license และ redaction note

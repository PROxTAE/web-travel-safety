# คนที่ 2 — API and Backend Implementation Plan

## Mission

สร้าง public trust boundary ของระบบด้วย FastAPI: OIDC/JWT, user/trip/consent persistence, input validation, rate limit, idempotency, orchestrationไป Agent, SSE proxy, response validation, privacy/retention และ public OpenAPI ที่เป็น contract กลาง

ความสำคัญ: ถ้า module นี้ไม่เสร็จ frontend จะต้องเรียก internal serviceโดยตรง, ไม่มี auth/ownership/rate limit/audit และทุก serviceจะใช้ schemaไม่ตรงกัน ความผิดพลาดที่นี่สามารถ leakตำแหน่ง/ข้อมูลแพทย์หรือส่ง recommendationผิดคนได้

## Ownership

- `services/api/**`
- `packages/contracts/openapi/public-api.yaml`
- `packages/contracts/jsonschema/common/**` ในฐานะ maintainerโดยต้องให้ consumer review
- migrations schema `identity`, `travel`
- `infra/keycloak/**` ร่วมกับ Team Lead
- public API integration tests

Dependency:

- คน 1 consume public REST/SSE/OIDC
- คน 3 รับ normalized requestและคืน run status
- คน 8 เก็บ/คืน recommendation, feedback, subscription ผ่าน internal API
- ทุกคน consume generated common contracts

## Stack

- Python 3.12, uv, FastAPI, Pydantic v2/pydantic-settings
- SQLAlchemy 2 async + asyncpg + Alembic
- PostgreSQL/PostGIS, Redis
- Keycloak OIDC; `PyJWT[crypto]` หรือ Authlibสำหรับ JWT verification/JWKS cache
- httpx async + Tenacityเฉพาะ safe transient retry
- `slowapi` หรือ custom Redis token bucket; เลือกหนึ่งและเขียน test
- structlog, OpenTelemetry, Prometheus
- pytest, pytest-asyncio, respx, Testcontainers, Hypothesisสำหรับ boundary

## Target structure

```text
services/api/
├─ app/
│  ├─ main.py
│  ├─ api/v1/
│  │  ├─ me.py
│  │  ├─ consents.py
│  │  ├─ locations.py
│  │  ├─ trips.py
│  │  ├─ runs.py
│  │  ├─ recommendations.py
│  │  ├─ conversations.py
│  │  ├─ safety.py
│  │  ├─ emergency.py
│  │  └─ feedback.py
│  ├─ application/       # use cases
│  ├─ domain/            # entities/policies independent of FastAPI/DB
│  ├─ repositories/
│  ├─ clients/           # agent/recommendation/external facade
│  ├─ auth/
│  ├─ middleware/
│  ├─ schemas/generated/
│  ├─ observability/
│  └─ settings.py
├─ migrations/
├─ tests/
├─ pyproject.toml
├─ uv.lock
└─ Dockerfile
```

## Public API scope

Implement endpointทุกตัวใน section 4 ของ `00_API_AND_DATA_CONTRACTS.md` โดยลำดับ MVP:

1. `/me`, `/consents`, emergency profile
2. `/locations/search`
3. CRUD `/trips`
4. POST assessments + `/runs` + SSE
5. recommendations, route apply
6. safety map, conversations/follow-up
7. emergency contact/nearby
8. feedback/subscriptions

APIไม่คำนวณ risk ไม่เรียก providerโดยตรงนอกจาก geocode proxyผ่าน internal external-data adapter และไม่แก้ decisionจากคน 7/8

## Domain and persistence rules

### User identity

- Keycloak `sub` เป็น external identity; internal `user_id` เป็น UUID ห้ามใช้ emailเป็น primary key
- ตรวจ issuer, audience, signature, expiry, not-before และ required scopes/roles
- JWKS cacheมี TTL/refreshเมื่อ `kid` ใหม่; ถ้า verifyไม่ได้ให้ 401ไม่ fallback decode unsigned
- endpointทุก resourceตรวจ `user_id` ownershipที่ repository query ไม่รับ user IDจาก bodyเป็น authority
- emergency profileเข้ารหัส application-level envelopeหรือ field encryption; keyไม่ได้อยู่ DBเดียวกันใน production

### Trip

- optimistic concurrencyด้วย `revision`/ETag; PATCHต้องมี `If-Match`
- origin/destinationเป็น confirmed `LocationRef`
- validate departure/return/timezone/mode/coverage inputก่อนส่ง agent
- apply-routeสร้าง revisionใหม่, เก็บ previous selected route, แล้วเริ่ม assessmentใหม่เสมอ
- DELETEเป็น soft delete, background purgeตาม retention; child data handledตาม policy

### Assessment run

- clientส่ง Idempotency-Key; store hash(user,key,payload)
- keyเดิม+payloadเดิมคืน runเดิม; keyเดิม+payloadต่างคืน 409 `IDEMPOTENCY_CONFLICT`
- request status state machineต้อง reject transitionย้อนกลับผิดกฎ
- APIสร้าง `request_id`/`correlation_id`, commit requestก่อน call agent
- agent call timeoutสั้นพอรับ 202; long processingติดตามผ่าน status/SSE
- cancellationจาก clientส่งต่อ agentแต่ไม่ลบ audit

### SSE

- authorize ownerก่อนเปิด stream
- proxyเฉพาะ event contract; sanitizeและ validateทุก payload
- heartbeats, Last-Event-ID, bounded buffer/backpressure
- stream closeหลัง completed/failed/cancelled
- ไม่ให้เปิด connectionsเกิน limitต่อ user/IP

### Privacy/retention

- exact coordinatesไม่อยู่ access log/query log; middleware redact query/body fields
- consentทุกชนิด versionedและ revokeได้
- location-onceหมดอายุตาม use case; live locationมี session/stop/expiry
- export/delete jobต้อง track statusและทำ across owned schemasผ่าน service APIs
- analytics pseudonymousและ opt-in

## Implementation steps

### Phase 0 — Freeze public contract

1. อ่าน 8 module inputs/outputsและทำ field ownership matrix
2. สร้าง OpenAPI public v1และ common JSON Schemaจาก contract doc
3. ใส่ success/error/SSE examplesที่ sanitizeแล้ว
4. ตั้ง Spectral/Redocly lint + breaking change check + generator
5. ให้คน 1/3/8 reviewก่อน merge contract PR

Exit: TS/Python generateสำเร็จและ consumer contract testsเริ่มได้

### Phase 1 — Service scaffold

1. FastAPI app factory, settings validation, lifespan
2. middleware order: trusted proxy/request size -> request ID/trace -> auth context -> rate limit -> logging/error mapping
3. `/health/live`, `/health/ready`, `/metrics`; readinessตรวจ DB/Redis/Keycloak discovery/agentแบบ bounded
4. SQLAlchemy session/UoW, Alembic per schema
5. Docker non-root/multi-stageและ Compose health
6. error handler mapทุก exceptionเป็น stable envelope

Exit: fresh DB migrateได้, container healthy, logsไม่มี body/token

### Phase 2 — OIDC and authorization

1. configure Keycloak realm/client/rolesแบบ import fileที่ไม่มี secretจริง
2. verify JWT/JWKSและ map `sub` -> user profile
3. implement role/scope dependenciesและ object ownership
4. auth tests: expired, wrong issuer/audience, unknown kid refresh, no scope, other user's trip
5. CORS allowlist exact origin; credentialsเฉพาะ HTTPS/approved local

Exit: protected endpointsผ่าน real Keycloak tokenและ negative tests

### Phase 3 — User, consent and emergency profile

1. migrations/tables/indexes
2. `/me`, consent grant/revoke with policy version
3. emergency profile encrypt/decrypt/redact response
4. audit create/update/deleteโดยไม่บันทึก content sensitive
5. retention/export/delete skeleton jobsและ status

Exit: persistenceจริง, restart containerข้อมูลยังอยู่, unauthorizedอ่านไม่ได้

### Phase 4 — Trip domain

1. Trip entity/value objectsและ validators
2. repositoriesที่ filter ownerทุก query
3. CRUD + pagination + ETag/revision
4. location search proxyไปคน 4พร้อม cancellation/cache headers
5. reject unconfirmed/out-of-range coordinates, invalid timezone/date/mode
6. migration upgrade/downgradeและ concurrency tests

Exit: frontendสร้าง/แก้ tripจริงได้และ stale revisionได้ 409/412

### Phase 5 — Assessment orchestration

1. request/idempotency tablesและ state machine
2. POST assessment normalize input/locale/timezoneแล้ว call agent `/internal/v1/runs`
3. poll statusและ SSE bridgeผ่าน Redis stream/agent status
4. propagate trace, timeout, cancellation; map agent errorเป็น partial/503อย่างถูกต้อง
5. validate final recommendation schemaจากคน 8ก่อน expose
6. cache only safe GET by user/resource/freshness; ห้าม cache private responseข้าม user

Exit: create assessmentยิงซ้ำไม่เกิด duplicateและ progressถึง finalได้

### Phase 6 — Remaining facades

1. recommendation GETและ apply-route transaction/reassessment
2. conversation list/messageต่อคน 3
3. safety events query: validate bbox/time/layers/limit, proxy canonical data
4. emergency contacts/nearby: consent/coordinate precision/source/effective date validation
5. feedback/subscription proxyต่อคน 8; validate consent/ownership

Exit: public contractครบและ frontendไม่ต้องรู้ internal topology

### Phase 7 — Resilience, security and observability

1. Redis token bucket by user/IP/endpoint; `Retry-After`
2. request size/decompression limits, slow client/SSE connection limits
3. outbound allowlistผ่าน configured base URLs, no user URL
4. timeout budgetแต่ละ downstream, circuit breakerถ้าจำเป็น, no retry unsafe mutationโดยไม่มี idempotency
5. structured logs/metrics/tracesและ PII redaction tests
6. dependency failure matrix: DB, Redis, Keycloak, agent, recommendation
7. security headersร่วมกับ web/reverse proxy

### Phase 8 — Final verification

1. OpenAPI snapshotตรง implementation
2. empty DB -> migrate -> seed Keycloak config -> tests
3. load test representative endpoints/SSE; ตรวจ connection/memory limit
4. run full E2E real provider flowและเก็บ correlation trace
5. backup/restoreและ deletion flow sample
6. docs/runbook/completion report/PR

## Test matrix

| Area | Cases |
| --- | --- |
| Auth | valid, expired, revoked/disabled user, wrong issuer/aud, unknown kid, missing scope |
| Ownership | own/other user's trip, recommendation, conversation, emergency profile |
| Validation | coordinates, timezones, date boundaries, long Unicode, bbox/limit, enum unknown |
| Idempotency | same key/body, same key/different body, concurrent duplicate, expired key |
| Persistence | migration fresh/upgrade, restart persistence, optimistic concurrency, soft delete |
| Downstream | timeout, connect error, 429+Retry-After, malformed schema, partial result, cancellation |
| SSE | reconnect, Last-Event-ID, heartbeat, terminal close, backpressure, authorization |
| Privacy | log/trace redaction, cache separation, consent revoke, export/delete |
| Rate limit | user/IP/endpoint buckets, trusted proxy handling, retry header |
| Contract | OpenAPI lint/generate/snapshot, error envelope, generated client compatibility |

## Acceptance checklist

- [ ] public OpenAPI v1ครบและ generated clients reproducible
- [ ] OIDC/JWT verifyครบ ไม่ใช้ custom insecure password endpoint
- [ ] user/trip/consent/emergency profileเก็บใน PostgreSQLจริงด้วย migrations
- [ ] object ownershipและ rate limitผ่าน negative tests
- [ ] assessment async/idempotentและ SSE reconnectได้
- [ ] final response validate schemaก่อนส่ง web
- [ ] exact location/medical/tokenไม่รั่ว log/cache/trace
- [ ] dependencyล่มคืน stable error/partialไม่ stack trace
- [ ] health/readiness/metrics/tracesทำงาน
- [ ] Docker production non-rootและ full E2Eผ่าน

## Branch/commit/PR breakdown

1. `contract/02-public-travel-schema` — OpenAPI/common schemas/generation
2. `feat/02-api-scaffold` — app/error/health/DB/observability/Docker
3. `feat/02-oidc-auth` — Keycloak/JWT/authorization/tests
4. `feat/02-profile-consent` — persistence/encryption/retention skeleton
5. `feat/02-trip-api` — CRUD/geocode facade/revision
6. `feat/02-assessment-runs` — idempotency/agent/SSE/status
7. `feat/02-public-facades` — recommendation/chat/safety/emergency/feedback
8. `test/02-api-resilience-security` — failure/load/privacy/contract suite

Commitแยก contract, migration, implementation, tests และ docsให้ review/rollbackง่าย

## Completion report requirements

สร้าง `docs/handoffs/M02-api-backend.md` และระบุเพิ่ม:

- public endpoint matrix + auth/scope/owner rule
- DB migration heads/table/index/retention
- idempotency/state transition diagrams
- downstream timeout/retry budgets
- rate limit valuesและ load result
- Keycloak realm/client setupที่ไม่มี secret
- sample sanitized request/correlation trace
- privacy deletion/export statusและ known limitations


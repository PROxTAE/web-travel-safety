# 00 — Shared Project Context

เอกสารนี้เป็น context กลางที่ทุกคนและ AI coding agent ต้องถือเป็นข้อกำหนดเดียวกัน หาก implementation detail ของแต่ละไฟล์ขัดกัน ให้เรียงลำดับอำนาจดังนี้: ข้อกำหนดจากผู้ใช้ > ไฟล์นี้ > API/Data Contract > แผนรายคน > ความสะดวกในการ implement

## 1. Product definition

ชื่อระบบ: **Smart Travel Assistant**

เป้าหมาย: ช่วยผู้เดินทางตรวจความปลอดภัยของเส้นทางก่อนและระหว่างเดินทาง โดยรวมสภาพอากาศ สถานะขนส่ง เหตุภัยพิบัติ การปิดเส้นทาง และคำแนะนำฉุกเฉิน แล้วคืน action ที่เข้าใจง่ายพร้อมหลักฐาน

Action หลักมีเพียง 4 ค่า:

- `NORMAL` — เดินทางได้ตามแผน
- `CHANGE_ROUTE` — มีเส้นทางอื่นที่ปลอดภัยกว่า
- `DELAY` — ความเสี่ยงขึ้นกับเวลาและน่าจะลดลงเมื่อเลื่อน
- `AVOID` — ความเสี่ยงสูง, มี official closure หรือไม่มีเส้นทางที่ยอมรับได้

กรณีฉุกเฉินสามารถแนบ `emergency_instructions` ได้ทุก action แต่หากผู้ใช้กด SOS ให้เข้า emergency flow โดยตรง ไม่ต้องรอ agent ตอบ

### สิ่งที่ระบบต้องทำใน MVP

1. สมัคร/เข้าสู่ระบบและเก็บ user profile, consent, locale, emergency profile จริงในฐานข้อมูล
2. สร้าง/แก้ไขทริป: ต้นทาง ปลายทาง วันเวลา โหมดเดินทาง และ preference
3. Geocode สถานที่และให้ผู้ใช้ยืนยันพิกัดบนแผนที่
4. เรียกข้อมูล weather, route/transport และ disaster จาก provider จริง
5. เชื่อมเหตุการณ์กับ route corridor และช่วงเวลาเดินทาง ไม่ประเมินเฉพาะจุดต้นทาง/ปลายทาง
6. รัน local risk model + deterministic safety override
7. ดึงคู่มือฉุกเฉินจากเอกสารหน่วยงานที่อนุมัติผ่าน RAG พร้อม citation
8. สร้าง route alternatives และอธิบาย trade-off เวลา ระยะทาง ความเสี่ยง และ freshness
9. ให้ decision engine lock action ก่อน LLM เขียนคำอธิบาย
10. ส่งผลลัพธ์และ progress ผ่าน REST + SSE, บันทึก feedback และติดตาม alert เมื่อผู้ใช้ opt in
11. หน้า Dashboard, Trip Planner, Safety Map, AI Assistant, Emergency Center และ Route Comparison ต้องใช้งานได้จริง

### Non-goals ของ MVP

- ไม่จองหรือซื้อเที่ยวบิน/โรงแรม/ตั๋ว
- ไม่โทรออกหรือ dispatch หน่วยฉุกเฉินอัตโนมัติ; ทำได้เพียงเปิด dial link/แสดงช่องทางที่ตรวจสอบแล้วและต้องให้ผู้ใช้ยืนยัน
- ไม่รับประกันความปลอดภัย 100% และไม่แทนประกาศทางการ
- ไม่ฝึกโมเดลจาก feedback สดโดยอัตโนมัติ
- ไม่ทำ autonomous browsing หรือให้ agent เรียก URL/คำสั่งระบบตามใจ
- ไม่อ้างว่ารองรับ transport mode ในพื้นที่ที่ไม่มีข้อมูลจริง; ต้อง disable หรือแสดง unavailable

## 2. UI source of truth

Visual master อยู่ใน `assets/ui-screens/` ขนาด 1672 × 941:

| Route | Reference | หน้าที่และ interaction บังคับ |
| --- | --- | --- |
| `/dashboard` | `01-dashboard-overview.png` | Weather/Transport/Risk cards, current trip, route map, recommendation, SOS, assistant teaser |
| `/trips/new` และ `/trips/[id]` | `02-trip-planner.png` | From/To, date, mode, preferences, map/list, route options, Find safe routes |
| `/safety-map` | `03-global-safety-map.png` | risk layers, marker details, freshness, Now/6h/12h, Avoid area |
| `/assistant/[conversationId]` | `04-ai-assistant.png` | recent chats, streaming answer, safer plan, quick prompts, trip context, live-location consent |
| `/emergency` | `05-emergency-center.png` | hold 3s, confirm, share location, contacts/hospital/embassy, emergency profile |
| `/trips/[id]/compare` | `06-route-comparison.png` | original vs safer route, explicit risk acceptance, apply route, live alert opt-in |

สิ่งที่เห็นในภาพ เช่น เมือง วันที่ อุณหภูมิ เบอร์ 191/1669 และสถานะ “On time” เป็นตัวอย่าง visual เท่านั้น ห้าม hard-code เป็นข้อมูล runtime ต้องมาจาก API/DB ตามตำแหน่งและเวลาจริง หากหาไม่ได้ให้แสดง `Not available` และบอกเวลา/สาเหตุ

### Design tokens ขั้นต่ำ

```text
primary-teal   #08B88A
deep-teal      #087B73
mint-surface   #DDF9EE
app-background #F5FFFC
heading-navy   #101A4B
weather-blue   #2F86F6
warning-amber  #FF9D1F
emergency      #F24E54
```

- ใช้ HeroUI v3 เป็น component primitive, Tailwind CSS v4 สำหรับ layout/token และ custom component เฉพาะกรณีที่ HeroUI ไม่มีรูปแบบตามภาพ
- map ต้องเป็น interactive MapLibre layer; ห้ามใช้ภาพ `global-map-background.png` แทนแผนที่จริง ภาพนั้นใช้เป็น decorative empty/hero state เท่านั้น
- desktop อ้างอิงที่ 1672×941; breakpoint บังคับ 1440, 1024, 768, 390 px
- risk ต้องมี text + icon เสมอ ไม่ใช้สีอย่างเดียว; focus, keyboard, screen reader และ contrast ต้องผ่าน WCAG 2.1 AA
- UI ทุก data card ต้องแสดง `updated_at` หรือเปิดดู freshness ได้

## 3. Architecture ที่ทีมจะ implement

```text
Browser / PWA
  -> apps/web (Next.js 16, React 19, HeroUI 3, MapLibre)
  -> services/api (FastAPI public trust boundary, OIDC/JWT, trips/users)
  -> services/agent (LangGraph finite state workflow)
       -> services/external-data (provider adapters)
       -> services/data-integration (canonical + geospatial snapshot)
       -> services/risk-knowledge (local model + RAG + routes)
       -> services/decision-engine (deterministic policy + LLM explanation)
       -> services/recommendation (response + alerts + feedback)

Shared infrastructure:
  PostgreSQL 16 + PostGIS | Redis | Qdrant | Keycloak | OpenTelemetry Collector
  Prometheus | Grafana | optional Mailpit for local notification inspection
```

บริการสื่อสารกันผ่าน internal Docker network ด้วย JSON over HTTP; public browser เข้าถึงเฉพาะ `web` และ `api` เท่านั้น ห้าม expose internal service port ใน production compose

### Service/port map สำหรับ local development

| Service | Container name | Host port | Owner |
| --- | --- | ---: | --- |
| Web | `web` | 3000 | คน 1 |
| Public API | `api` | 8000 | คน 2 |
| Agent | `agent` | 8001 | คน 3 |
| External data | `external-data` | 8002 | คน 4 |
| Data integration | `data-integration` | 8003 | คน 5 |
| Risk/knowledge | `risk-knowledge` | 8004 | คน 6 |
| Decision engine | `decision-engine` | 8005 | คน 7 |
| Recommendation | `recommendation` | 8006 | คน 8 |
| PostgreSQL/PostGIS | `postgres` | 5432 | infra |
| Redis | `redis` | 6379 | infra |
| Qdrant | `qdrant` | 6333 | คน 6 |
| Keycloak | `keycloak` | 8080 | คน 2 |
| Prometheus | `prometheus` | 9090 | shared |
| Grafana | `grafana` | 3001 | shared |

Host ports ใช้เฉพาะ local profile; service-to-service ต้องเรียก container DNS เช่น `http://agent:8001`

## 4. Technology decisions ที่ lock แล้ว

### Frontend

- Node.js 22.22+ และ pnpm workspace
- Next.js 16 App Router, React 19, TypeScript strict
- HeroUI v3 + Tailwind CSS v4; TanStack Query; React Hook Form + Zod
- MapLibre GL JS; `deck.gl` ใช้ได้เมื่อ heat/large marker layer จำเป็น
- Auth.js OIDC client เชื่อม Keycloak; token เก็บใน secure HttpOnly cookie ไม่ใช้ localStorage
- Vitest + Testing Library + MSW เฉพาะ test; Playwright + axe สำหรับ E2E/accessibility

เอกสาร HeroUI ปัจจุบันระบุ React 19/Tailwind 4 และ CLI template ใช้ Next.js 16: <https://heroui.com/en/docs/react/getting-started/quick-start>

### Python services

- Python 3.12, `uv` สำหรับ lock/install, FastAPI, Pydantic v2, `pydantic-settings`
- `httpx`, Tenacity, structlog, OpenTelemetry, Prometheus client
- SQLAlchemy 2 + Alembic สำหรับ service ที่เขียน DB
- pytest, pytest-asyncio, respx และ Testcontainers
- ทุก Docker image ใช้ multi-stage build, pinned lockfile, non-root user, healthcheck

### Storage

- PostgreSQL 16 + PostGIS เป็น system of record; แบ่ง schema/DB role ตาม service
- Redis ใช้ cache, rate limit, idempotency, task/progress pub-sub และ short-lived locks ไม่ใช่ system of record
- Qdrant เก็บ vector index; source document metadata และ manifest อยู่ PostgreSQL/Git
- MinIO ยังไม่เพิ่มใน MVP; artifact model เก็บใน MLflow local artifact volume หรือ path ที่กำหนด ถ้าต้อง scale ค่อยเสนอ ADR

### AI/ML

- Agent: LangGraph แบบ finite graph + Postgres checkpointer; ไม่มี open-ended loop
- Local risk baseline: scikit-learn calibrated classifier + deterministic safety overrides; จะเพิ่ม XGBoost/LightGBM เมื่อ metric ดีกว่า baseline อย่างมีหลักฐานเท่านั้น
- Embedding: multilingual Sentence Transformers ที่รองรับ Thai/English; Qdrant + BM25 hybrid + reranker
- LLM: OpenAI Responses API ผ่าน server-side SDK, model กำหนดด้วย env `OPENAI_EXPLAINER_MODEL`; `text.format` strict JSON Schema, `store: false` โดยค่าเริ่มต้น, temperature ต่ำ/0, ตรวจ refusal/incomplete และมี fixed-template fallback
- action ถูก lock ก่อนเรียก LLM; LLM แก้ `action_code`, official warning, number หรือ citation ไม่ได้

## 5. Real external data policy

Provider baseline สำหรับ MVP:

| Domain | Primary | Backup/เสริม | หมายเหตุ |
| --- | --- | --- | --- |
| Geocoding | Open-Meteo Geocoding | openrouteservice geocoding | เก็บ `provider_place_id`, lat/lon, timezone |
| Weather | Open-Meteo Forecast | provider อื่นเมื่อทีมมี key | forecast สูงสุดตามข้อจำกัด provider; query ตาม route sample/time |
| Road route | openrouteservice Directions v2 | self-host ORS ในอนาคต | API key server-side; alternatives/avoid polygon มีข้อจำกัด |
| Flight search/status | Amadeus Self-Service production | unavailable อย่างซื่อสัตย์ | test environment ไม่ถือเป็น real acceptance data |
| Ground transit | GTFS + GTFS-Realtime feed ของ agency ที่ลงทะเบียนใน `providers.yaml` | ORS สำหรับ road-only | coverage ไม่ทั่วโลก; UI ต้องบอก coverage |
| Nearby emergency POI | openrouteservice POIs หรือ approved OSM-based provider | unavailable อย่างซื่อสัตย์ | ใช้ค้น police/hospital/embassy; ไม่ใช้แทน official phone directory |
| Earthquake | USGS GeoJSON feed/catalog | GDACS | เก็บ event id และ source URL |
| Cyclone/flood/other disaster | GDACS API | NASA EONET v3 | official/curated source ได้ priority สูงกว่า |
| Map tiles | provider ที่อนุญาตและระบุ attribution | configurable | ห้ามใช้ public tile โดยละเมิด usage policy |
| Emergency knowledge | approved government/UN/WHO documents | none | ทุกเอกสารต้องมี authority/effective/expiry/review |

เอกสาร provider ที่ต้องใช้ตรวจ implementation:

- Open-Meteo Weather/Geocoding: <https://open-meteo.com/en/docs> และ <https://open-meteo.com/en/docs/geocoding-api>
- openrouteservice Directions v2: <https://openrouteservice.org/dev/> และข้อจำกัด <https://openrouteservice.org/restrictions/>
- USGS GeoJSON: <https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php>
- GDACS API/feeds: <https://www.gdacs.org/gdacsapi/swagger/index.html>
- NASA EONET v3: <https://eonet.gsfc.nasa.gov/docs/v3>
- GTFS-Realtime: <https://gtfs.org/documentation/realtime/reference/>

ห้ามรวม key ใน frontend, log, trace หรือ commit ทุก provider adapter ต้องเก็บ provenance (`provider`, `source_url`, `source_record_id`, `observed_at`, `fetched_at`, `expires_at`, `license`) และคืน health/quality status

## 6. Repository structure และ ownership

```text
smart-travel-assistant/
├─ apps/
│  └─ web/                         # คน 1
├─ services/
│  ├─ api/                         # คน 2
│  ├─ agent/                       # คน 3
│  ├─ external-data/               # คน 4
│  ├─ data-integration/            # คน 5
│  ├─ risk-knowledge/              # คน 6
│  ├─ decision-engine/             # คน 7
│  └─ recommendation/              # คน 8
├─ packages/
│  ├─ contracts/                   # shared; คน 2 เป็น maintainer
│  │  ├─ openapi/
│  │  ├─ jsonschema/
│  │  └─ generated/
│  ├─ python-common/               # shared observability/error only
│  └─ ts-config/                   # คน 1 maintainer
├─ infra/
│  ├─ postgres/init/
│  ├─ keycloak/
│  ├─ qdrant/
│  ├─ otel/
│  ├─ prometheus/
│  └─ grafana/
├─ ops/
│  ├─ scripts/
│  └─ runbooks/
├─ tests/
│  ├─ contract/
│  ├─ integration/
│  └─ e2e/
├─ assets/                         # supplied visual assets; read-only by default
├─ docs/
│  ├─ adr/
│  ├─ api/
│  └─ diagrams/
├─ IMPLEMENTATION_PLANS/
├─ .github/workflows/
├─ compose.yaml
├─ compose.dev.yaml
├─ .env.example
├─ Makefile
└─ README.md
```

### Ownership rule

- เจ้าของ module แก้ไฟล์ใน folder ตัวเองได้ตาม PR ปกติ
- `packages/contracts`, `compose*.yaml`, `.env.example`, migrations ที่แตะ schema อื่น, root `README`, CI และ infra เป็น shared surface ต้องขอ review จาก owner ที่ได้รับผลกระทบ + Team Lead
- ห้าม service อ่าน/เขียน table ของ service อื่นโดยตรง ให้เรียก API หรือใช้ event ที่กำหนด เว้นแต่ read-only audit/report ที่อนุมัติด้วย ADR
- ห้ามเปลี่ยนชื่อ field/enums หลัง contract freeze โดยไม่เพิ่ม contract version และ migration path
- `assets/` ไม่แก้ทับต้นฉบับ หาก optimize ให้สร้าง derivative ใน `apps/web/public/assets/` และเก็บ script/metadata

## 7. End-to-end data flow

1. Web รับ trip input และ geocode suggestion จาก API
2. API ตรวจ JWT, schema, consent, idempotency; บันทึก trip/request แล้วเรียก Agent
3. Agent classify intent และตรวจข้อมูลขาด หากครบให้ fan-out เรียก External Data
4. External Data ดึง real providers แบบขนาน, normalize ระดับ provider, cache ตาม TTL, แนบ provenance
5. Data Integration validate/dedupe/convert unit/time และ spatially intersect กับ route corridor สร้าง `IntegratedTravelContext`
6. Risk/Knowledge รัน local risk model, deterministic override, RAG และ route alternatives
7. Decision Engine ตรวจ consistency, ใช้ decision table เลือก/lock action, เรียก LLM อธิบายจาก evidence เท่านั้น
8. Recommendation service สร้าง response, emergency info, notification state และ feedback links
9. API validate response อีกครั้ง บันทึก metadata และส่ง REST/SSE กลับ Web
10. เมื่อ route/time เปลี่ยนต้องสร้าง assessment ใหม่ ห้าม reuse score เก่า; follow-up ใช้ `conversation_id` แต่ตรวจ freshness ใหม่เมื่อคำถามเกี่ยวข้องกับสถานการณ์ปัจจุบัน

## 8. Canonical entities ที่ทุกคนต้องเข้าใจ

รายละเอียด field อยู่ใน `00_API_AND_DATA_CONTRACTS.md` เอนทิตีหลักคือ:

- `UserProfile`, `ConsentRecord`, `EmergencyProfile`
- `Trip`, `TravelRequest`, `LocationRef`, `TravelPreference`
- `RouteCandidate`, `RouteSegment`, `TransportStatus`
- `WeatherObservation`, `WeatherForecastPoint`
- `DisasterEvent`, `OfficialAlert`
- `DataQuality`, `SourceProvenance`, `IntegratedTravelContext`
- `RiskAssessment`, `RetrievedEvidence`
- `DecisionResult`, `RecommendationResponse`
- `Conversation`, `FeedbackEvent`, `AlertSubscription`

เวลาทั้งหมดส่งผ่าน API เป็น ISO-8601 มี timezone offset; เก็บ DB เป็น UTC พร้อม timezone ต้นทางที่จำเป็น พิกัดใช้ WGS84 `[longitude, latitude]` ตาม GeoJSON ห้ามสลับลำดับ

## 9. Data ownership และ persistence

ใช้ PostgreSQL instance เดียวใน local แต่แยก schema/role:

| Schema | Owner | ข้อมูล |
| --- | --- | --- |
| `identity` | API | profile, consent, emergency profile mapping |
| `travel` | API | trips, requests, request status, idempotency |
| `agent` | Agent | checkpoints/conversation pointers, budget usage |
| `provider` | External Data | provider health, fetch metadata; raw body เฉพาะ license อนุญาต |
| `integration` | Data Integration | normalized observations/events, snapshots, lineage |
| `knowledge` | Risk/Knowledge | model registry metadata, source docs, chunks metadata, assessments, routes |
| `decision` | Decision | policy/prompt version, audit trace, validation result |
| `recommendation` | Recommendation | final response metadata, feedback, subscriptions, delivery log |

Location history เป็น sensitive data: encrypt at rest where available, minimize precision/retention, ไม่ log exact coordinate, ให้ผู้ใช้ opt in ก่อน live tracking และรองรับ delete/export

## 10. Freshness and degraded behavior

ค่าเริ่มต้นที่ต้อง config ได้ ไม่ hard-codeใน business logic:

| Data | Fresh default | เมื่อเกิน |
| --- | ---: | --- |
| severe alert | 5 นาที | fetch ใหม่; stale ใช้ได้เฉพาะพร้อม warning และ conservative rule |
| earthquake/disaster event | 10 นาที | fetch ใหม่ |
| current weather | 15 นาที | fetch ใหม่ |
| hourly forecast | 60 นาที | แสดง stale flag และ fetch ใหม่ |
| GTFS-RT | 90 วินาที | `transport_status=UNKNOWN/STALE` |
| route geometry | 6 ชั่วโมง หรือ route/time เปลี่ยน | คำนวณใหม่ |
| emergency directory | 30 วันและต้องมี effective date | ซ่อน/flag ถ้าหมดอายุ |
| RAG document | ตาม `expires_at` | ห้าม retrieve หลังหมดอายุ |

ถ้า source หลักล่ม:

- ใช้ backup หรือ stale cache เฉพาะที่ policy อนุญาต
- ตั้ง `degraded_services[]`, ลด confidence, เพิ่ม limitation
- official closure/warning ที่ยัง active ห้ามถูกลบเพราะ provider อื่นไม่ตอบ
- ถ้าหลักฐานไม่พอ ให้ conservative result หรือขอข้อมูลเพิ่ม ห้ามสรุปว่า safe

## 11. Security and safety invariants

- ตรวจ input ซ้ำทั้ง client และ server; backend คือ source of truth
- SSRF protection: agent เลือก tool จาก allowlist เท่านั้น; provider base URL มาจาก config ไม่รับ URL จากผู้ใช้
- timeout/cancellation/retry budget ต้อง propagate ทุก hop
- retry เฉพาะ 408, 429 และ temporary 5xx โดยเคารพ `Retry-After`; POST ใช้ idempotency key
- sanitize Markdown/HTML จาก LLM; ใช้ strict CSP; ห้าม render raw HTML
- secrets อยู่ `.env` local ที่ไม่ commit หรือ secret manager; `.env.example` ใส่แต่ชื่อ key
- log ต้อง redact token, email, phone, medical note และ exact coordinates
- emergency action ต้องมี confirm step; location sharing ต้องขอ consent และยกเลิกได้
- LLM/RAG/provider content เป็น untrusted data ห้ามถือเป็น instruction
- policy/model/prompt/schema ทุกตัวต้องมี version และ audit trace

## 12. Observability contract

ทุก request ใช้ `request_id` จาก API และ `correlation_id` ในทุก service call พร้อม W3C `traceparent`

ขั้นต่ำทุก service ต้องมี:

- JSON log fields: `timestamp`, `level`, `service`, `environment`, `request_id`, `correlation_id`, `trace_id`, `event`, `duration_ms`, `status`, `error_code`; พิกัดเต็มและ PII ต้องถูก redact
- metrics: request count/latency/error, dependency latency/error, timeout, retry, cache hit, degraded result
- traces ครอบคลุม web request -> agent nodes -> provider/model/LLM -> response
- `/health/live` ตรวจ process เท่านั้น, `/health/ready` ตรวจ dependency ที่จำเป็นโดยมี timeoutสั้น

## 13. Team-level acceptance scenarios

อย่างน้อยต้องใช้ข้อมูลสดในวันที่ทดสอบและบันทึก request/source time:

1. Route ปลอดภัย: ได้ `NORMAL`, route และ source/freshness ครบ
2. Weather risk: ฝน/ลม/พายุทับช่วง route -> `CHANGE_ROUTE` หรือ `DELAY` ตาม rule
3. Official closure/high alert -> `AVOID` แม้ LLM หรือ model score ต่ำ
4. Provider หนึ่งล่ม -> partial/degraded result ไม่ crash และไม่อ้างข้อมูลปลอม
5. เปลี่ยน route -> assessment ใหม่และ compare page อัปเดต trip จริง
6. Follow-up -> conversation เดิม แต่ current-data query refresh ตาม TTL
7. SOS -> hold/confirm/consent, แสดง contact ตาม country และไม่ส่ง location ก่อนอนุญาต
8. Feedback `unsafe` -> เก็บแยก, สร้าง safety-review record, ไม่ retrain สด

รายละเอียดคำสั่งและ evidence อยู่ใน `09_INTEGRATION_ACCEPTANCE_RUNBOOK.md`

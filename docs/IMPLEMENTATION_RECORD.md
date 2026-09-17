# บันทึกการสร้างระบบ Smart Travel Assistant (Implementation Record)

เอกสารนี้เขียนให้เพื่อนในทีมอ่านแล้วเข้าใจว่า **ระบบถูกสร้างขึ้นมาอย่างไร ทีละขั้น** ใครทำอะไร ส่งต่อให้ใคร
ปัญหาที่เจอระหว่างทาง ส่วนไหนสำคัญ/วิกฤตที่สุด และแต่ละคนมีจุดเด่นอะไร งานทั้งหมดถูกจำลองโดยคนคนเดียว
(AI) ที่สวมบทบาทเป็น "คน 1–8" ตาม `IMPLEMENTATION_PLANS/` ทีละคน โดยยึดกฎเดียวกับทีมจริง:
ไม่ commit ตรงเข้า `main`, ใช้ feature branch + Conventional Commits + merge แบบ PR, ไม่มี mock ตอน runtime,
ทุกค่าที่เกี่ยวกับความปลอดภัยต้องมี source/freshness/quality/version, และ LLM เป็นแค่ "ผู้อธิบาย" การตัดสินใจที่ถูกล็อกแล้ว

> รายงานฉบับเต็มของแต่ละคนอยู่ใน `docs/handoffs/M0X-*.md` (ตาม template `10_WORK_COMPLETION_REPORT_TEMPLATE.md`)
> เอกสารนี้เป็นฉบับ "เล่าเรื่อง" ที่ร้อยทุกโมดูลเข้าด้วยกัน

---

## 0. ภาพรวมระบบและลำดับการสร้าง

```text
ผู้ใช้ ──► [คน 1] web (Next.js 16)  ──► [คน 2] api (FastAPI, OIDC, SSE) ──► [คน 3] agent (LangGraph)
                                                                           │
          [คน 4] external-data ◄──── ข้อมูลจริง: Open-Meteo, USGS, GDACS, EONET, GTFS-RT, (ORS/Amadeus ถ้ามี key)
          [คน 5] data-integration ◄─ รวม/ตรวจคุณภาพ/สร้าง snapshot ตาม corridor + เวลาเดินทาง
          [คน 6] risk-knowledge  ◄── โมเดลความเสี่ยง + overrides + RAG (Qdrant) + จัดอันดับเส้นทาง
          [คน 7] decision-engine ◄── ตารางนโยบาย YAML ล็อก action → LLM อธิบาย → validator
          [คน 8] recommendation  ◄── คำตอบสุดท้าย + เบอร์ฉุกเฉินที่ตรวจสอบแล้ว + แจ้งเตือน + feedback
```

**ลำดับที่สร้างจริง** (จากล่างขึ้นบน เพื่อให้ contract และข้อมูลจริงพร้อมก่อน):
Team Lead scaffold → คน 4 → คน 5 → คน 6 → คน 7 → คน 8 → คน 3 → คน 2 → คน 1 → Integration/Acceptance

เหตุผล: agent (คน 3) ต้องรู้ input/output ของทุก tool ก่อน, api (คน 2) ต้องมี agent ให้เรียก, และ web (คน 1)
ต้องมี OpenAPI จริงของ api ให้ generate type — สร้างย้อนลำดับจะต้องเดา contract แล้วแก้ทีหลัง

---

## 1. ขั้นเตรียมโครงการ (Team Lead / ทุกคนใช้ร่วมกัน)

**ทำอะไร:** monorepo (`apps/web`, 7 `services/*`, `packages/python-common` (`sta_common`), `packages/contracts`
(`sta_contracts`)), `compose.yaml` + `compose.dev.yaml`, PostgreSQL 16 + PostGIS พร้อม role ต่อ schema
(`sta_api`, `sta_agent`, `sta_provider`, `sta_integration`, `sta_knowledge`, `sta_decision`, `sta_recommendation`),
Redis, Qdrant, Keycloak (realm import ไม่มี secret), OTel/Prometheus/Grafana, Mailpit, Makefile, GitHub Actions CI,
`.env.example` (ชื่อตัวแปรเท่านั้น) + `ops/scripts/gen-secrets.sh`

**อย่างไร:** `sta_common` ให้ทุก service ใช้ `create_app` (health live/ready, JSON logs ที่ redact PII/พิกัด,
correlation id, metrics, error envelope), `ResilientClient` (timeout/retry/Retry-After/deadline),
`InternalAuth` (bearer token ร่วมสำหรับ `/internal/v1`). `sta_contracts` เป็น Pydantic-first (ADR-0001) แล้ว
generate JSON Schema + TypeScript ให้ทุกคนใช้ type เดียวกัน

**ส่งต่อ:** ทุกคนได้ skeleton service เหมือนกัน + contract กลาง → ลด "schema ไม่ตรงกัน" ซึ่งเป็นความเสี่ยงอันดับหนึ่งของทีม 8 คน

---

## 2. คน 4 — External Data (ข้อมูลจริงจากผู้ให้บริการ)

**ทำอะไร:** adapters สำหรับ Open-Meteo (geocoding/forecast/archive), USGS FDSN, GDACS, NASA EONET, GTFS-RT
(MBTA public feed), openrouteservice (ต้องมี key → ถ้าไม่มีรายงาน `UNAVAILABLE`), Amadeus (production only → `UNAVAILABLE`);
`POST /internal/v1/context/query` รวมทุกอย่างเป็น `ExternalContext` พร้อม provider health, canary CLI ที่ยิง provider จริง
และเก็บ fixture แบบ sanitized (source/captured_at/schema_version) ไว้ใต้ tests เท่านั้น

**อย่างไร:** ตรวจ payload จริงของทุก provider ก่อนเขียน adapter, ทุก record มี `SourceProvenance` (observed/fetched/expires,
content hash), cache ใน Redis มี TTL และ negative cache, dedup ตาม provider/id/hash, route ที่ไม่มี ORS ถูกสร้างเป็น
geodesic `INFERRED` (บอกตรง ๆ ว่าไม่ใช่ถนนจริง)

**ผลลัพธ์/จุดเด่น:** ไม่มี provider ปลอมเลย; แม้ไม่มี key ระบบยังให้คำตอบที่ "ซื่อสัตย์" ว่าอะไรใช้ไม่ได้
**ปัญหาที่เจอ:** USGS 404 เพราะ base_url รวม query (แก้: แยก scheme/netloc กับ path), Open-Meteo ส่งจำนวนจุดไม่ตรงเมื่อมี
probe DELAYED (แก้: batch ตาม unique point), Windows ไม่มี tz database (เพิ่ม `tzdata`)
**ส่งต่อ:** คน 5 ได้ canonical records + `degraded_services`/`unavailable_capabilities`; คน 2 ใช้ geocode/places/disasters

---

## 3. คน 5 — Data Integration (snapshot ที่ตรวจสอบได้)

**ทำอะไร:** pipeline validate → normalize → deduplicate → enrich geospatial (PostGIS corridor รอบเส้นทาง,
ตัดตามหน้าต่างเวลาเดินทาง) → features (schema versioned `feature_schema.yaml`) → `IntegratedTravelContext` ที่ immutable
พร้อม `QualitySummary` (gate PASS/DEGRADED/BLOCK), conflict summary, lineage; idempotent ด้วย request_id + content hash

**ปัญหาที่เจอ:** เส้นทางข้าม dateline ทำให้ centroid ผิด/polygon invalid (แก้: geodesic midpoint + `split_antimeridian`
เป็น MultiPolygon และขยาย contract), coverage ของอากาศต้องคิดจากความยาวเส้นทาง (ทุก 50 กม. สูงสุด 12 จุด) ไม่ใช่จากจำนวน input,
Polars infer schema พังกับข้อมูลจริง (`infer_schema_length=None`)
**จุดเด่น:** online/offline feature parity — ชุด train ของคน 6 ถูกสร้างผ่าน API เดียวกับตอน serve
**ส่งต่อ:** คน 6 ได้ snapshot + features; คน 3 ใช้ snapshot_id เชื่อมทุกขั้น

---

## 4. คน 6 — Risk & Knowledge (โมเดล + RAG + จัดอันดับเส้นทาง)

**ทำอะไร:** dataset จาก forecast วันที่ 1 เทียบกับค่าที่สังเกตจริง (Open-Meteo archive) บนเส้นทางจริงหลายเส้น,
เทรน calibrated LogisticRegression/HGB, กำหนด threshold จาก out-of-fold, **acceptance gate** (recall ≥ 0.80, PR-AUC ≥ 0.35),
model card + manifest + checksum; safety overrides จาก official alerts/closures; RAG: FEMA Ready.gov + WHO ผ่าน
multilingual-e5-small + Qdrant (versioned collection + alias) + BM25 (RRF) กรองตาม expiry/พื้นที่/ภัย;
`POST /internal/v1/evidence/package` รวมความเสี่ยง + หลักฐาน + เส้นทางที่จัดอันดับ

**ผลลัพธ์ที่รายงานตรง ๆ:** โมเดล 1.0.0 ยัง **ไม่ผ่าน** gate (recall 0.65, PR-AUC 0.29) จึงอยู่สถานะ CANDIDATE;
ระบบเสิร์ฟด้วย rule baseline + overrides และบอกใน `degraded_services` ว่า "model unavailable (rule baseline)"
— นี่คือพฤติกรรมที่ถูกต้องตามแผน ไม่ใช่การซ่อน
**ปัญหาที่เจอ:** threshold 0.55 ให้ recall 2.6% (แก้ด้วย OOF operating point), ranking เลือกเส้นทางที่มีประกาศทางการเพราะ
override ยกระดับแต่ไม่ยกคะแนน (แก้: นับ official alert ต่อเส้นทาง + score floor), Hypothesis จับ input ที่ไม่สอดคล้อง
(HIGH แต่ p=0)
**ส่งต่อ:** คน 7 ได้ `EvidencePackage` ที่มี risk_level/score, citations, route ranking, limitations

---

## 5. คน 7 — Decision Engine (ล็อก action ก่อน LLM)

**ทำอะไร:** ตารางนโยบาย YAML 13 กฎ (validate ด้วย JSON Schema, checksum, approval record) → first-match →
`NORMAL | CHANGE_ROUTE | DELAY | AVOID`; consistency gate (request/revision/snapshot/route id/feature schema/citation หมดอายุ);
สูตร confidence v1; LLM (OpenAI Responses API, strict JSON schema, temperature 0, `store=false`) เห็นเฉพาะ
locked decision + facts + citations; post-validator ปฏิเสธ action drift, citation นอก allowlist, ตัวเลขนอก evidence,
เบอร์/URL/HTML, คำเกินจริง; retry 1 ครั้งแล้ว fallback template ไทย/อังกฤษ; audit trace ไม่มี prompt/ข้อความผู้ใช้

**จุดเด่น:** "LLM เปลี่ยน action ไม่ได้" ถูกทดสอบตรง ๆ (red-team: ผู้ใช้สั่ง IGNORE ALL RULES → action เดิม)
**ส่งต่อ:** คน 8 ได้ `DecisionResult` ที่ห้ามตีความใหม่; คน 3 หยุด run เมื่อได้ 422 `POLICY_VALIDATION_FAILED`

---

## 6. คน 8 — Recommendation, Emergency Directory, Alerts, Feedback

**ทำอะไร:** builder สร้าง `RecommendationResponse` (summary, immediate actions, routes, alerts, sources, freshness,
limitations); emergency directory จากแหล่งทางการ (TH/JP verified live; US pending เพราะ bot-block) พร้อม
verified_at/review_due; feedback governance (redact PII, pseudonymous, UNSAFE → safety review queue, ไม่ retrain สด);
alert rules + dedup + cooldown, ช่องทาง in-app/webpush/email, Arq worker ที่ publish `alert.reassessment.requested`

**ส่งต่อ:** คน 2 ใช้ทุก endpoint พร้อม `user_scope` pseudonym; คน 1 แสดง response ตามที่ให้มา

---

## 7. คน 3 — Travel Agent (orchestration แบบมีขอบเขต)

**ทำอะไร:** LangGraph StateGraph 8 nodes (finite ไม่ใช่ tool loop), allowlist 5 tools ผูก stage/contract/timeout,
budgets (12 steps, 10 tool calls, 45 s, 2 LLM calls) ที่บังคับจริง, intent rules-first (EMERGENCY shortcut ไม่เรียก provider),
NEEDS_INPUT ไม่เดา, follow-up reuse snapshot < 15 นาทีเฉพาะคำถามเชิงข้อมูล, progress stream ใน Redis ตาม SSE contract,
cancel แบบ cooperative, audit เก็บแค่ hash

**ปัญหาที่เจอ:** follow-up reuse ตก consistency gate เพราะ package ใช้ request_id ของ snapshot เดิม (แก้ให้ gate เทียบกับ
snapshot ไม่ใช่ request ปัจจุบัน — ตรงกับพฤติกรรมจริงของคน 6)
**ส่งต่อ:** คน 2 ได้ `/internal/v1/runs` + stream `sta:{env}:run:{id}:events`

---

## 8. คน 2 — Public API (ขอบเขตความเชื่อถือสาธารณะ)

**ทำอะไร:** OIDC/JWT (JWKS cache + refresh เมื่อ kid ใหม่, issuer/audience/exp/nbf, role `traveler`), ownership ด้วย
internal user_id ในทุก query, trips revision/ETag/If-Match, assessments ด้วย `Idempotency-Key` (fingerprint → replay
หรือ 409), state machine ของ request, SSE bridge จาก Redis stream (owner check, heartbeat, Last-Event-ID, cap/user),
emergency profile เข้ารหัส AES-GCM ด้วย key จาก env, facades ไปคน 4/8 พร้อม re-validate contract, rate limit,
consumer ของ `alert.reassessment.requested`, OpenAPI snapshot ที่ CI ตรวจว่าตรง implementation

**ปัญหาที่เจอ:** Windows Application Control บล็อก `pytest.exe`/`mypy.exe` (ใช้ `python -m`), JWKS refresh พลาดการ rotate ครั้งแรก,
idempotent replay สร้างข้อความซ้ำใน conversation, payload ผิด contract ถูกกลืนเป็น outage (แก้ให้เป็น 502 ชัดเจน)
**ส่งต่อ:** คน 1 ได้ `packages/contracts/openapi/public-api.yaml`

---

## 9. คน 1 — Web App (6 หน้าตาม UI)

**ทำอะไร:** Next.js 16 + HeroUI v3 + Tailwind 4 + MapLibre; Auth.js/Keycloak โดย token อยู่ใน HttpOnly cookie
และ browser คุยกับ `/api/backend/*` (proxy แนบ bearer, stream SSE ผ่านได้); 6 หน้า: dashboard, trip planner
(ยืนยัน pin, เวลาใน timezone ทริป, Idempotency-Key, SSE progress, route cards จาก label ของ server), safety map
(layers/timeline ใน URL, viewport query + abort, avoid area), assistant (sanitized markdown, quick chips, live location consent),
emergency (hold 3 s → confirm → share → connect, เบอร์จาก directory จริง, `tel:` เมื่อกด), compare/apply route
(If-Match, acknowledgement เมื่อเสี่ยง, notify subscription)

**ปัญหาที่เจอ:** Application Control บล็อก native binding ของ rolldown (Vitest 5) → ใช้ Vitest 3 + Vite 6; `next start`
ไม่รองรับ standalone → script รัน `server.js`; Next 16 ต้องการ named `proxy` export; React Compiler lint เข้มเรื่อง purity
**ส่งต่อ:** Team Lead รัน compose ทั้งชุด

---

## 10. Integration / Acceptance (Team Lead — รันจริงบน Docker ทั้งชุด)

รายงานเต็ม: `docs/acceptance/2026-09-17-8a8100c.md` + screenshot 6 หน้าใน `docs/acceptance/screenshots/`

**ทำอะไร:** build 8 image, up 13 containers (postgres/redis/qdrant/keycloak + 7 services + worker + web), migrate 7 schema,
`verify_model` (CANDIDATE → rule baseline), `index_knowledge` (56 points, recall@3 = 1.0, released), `verify_emergency_directory`
(7 verified / 1 pending), readiness ทุก service, สร้าง user ทดสอบใน realm dev, รัน E2E-01…10 ผ่าน Public API ด้วย JWT จริง
และ Playwright 16 เคส (login จริง → วางแผนทริป → SSE → คำแนะนำ → axe = 0 serious, SOS hold, มือถือ 390)

**บั๊กจริงที่เจอตอนรวมระบบ (ทั้งหมดแก้แล้ว — นี่คือเหตุผลที่ต้องมี integration phase):**

| # | อาการ | สาเหตุ | แก้ |
| --- | --- | --- | --- |
| 1 | Keycloak start ไม่ขึ้น | realm JSON ใช้ field ของ v25 (`postLogoutRedirectUris`, `KC_HOSTNAME_PORT`) | ใช้รูปแบบ v26 |
| 2 | login ได้แต่ API ตอบ 401 "token verification failed" | Keycloak 26 ไม่ใส่ `sub` ใน access token ถ้า realm import ไม่มี scope `basic` | นิยาม scope `basic/profile/email/roles` ในไฟล์ realm |
| 3 | `PROVIDER_AUTH` ทั้งที่ไม่มี key | comment ท้ายบรรทัดใน `.env` ถูกอ่านเป็นค่า (`ORS_API_KEY="# optional…"`) | ย้าย comment ขึ้นบรรทัดของตัวเอง |
| 4 | migration ทุก service ล้ม `permission denied` | `CREATE SCHEMA IF NOT EXISTS` ต้องการสิทธิ์ CREATE บน database แม้ schema มีแล้ว | DO-block ข้ามเมื่อ schema มีอยู่ (init สร้างให้ role เป็น owner) |
| 5 | image risk-knowledge 6+ GB | sentence-transformers ดึง torch CUDA | index `pytorch-cpu` → 3.2 GB |
| 6 | FEMA Ready.gov ตอบ 403 จากใน container | bot protection ต่อ egress ของ Docker | ใช้สำเนาที่ capture จากแหล่งจริงเมื่อวาน (รายงานว่า cached ไม่ใช่ fresh) |
| 7 | follow-up ล้ม `REQUEST_MISMATCH` | package ที่สร้างจาก snapshot เดิมเป็นของ request เก่า | `EvidencePackageRequest.request_id` (contract เพิ่ม field optional) gate ยังเข้มเหมือนเดิม |
| 8 | reassessment หลัง apply-route ล้ม `REVISION_MISMATCH` | agent นำ snapshot revision เก่ามาใช้ซ้ำ | reuse เฉพาะ trip + revision เดียวกัน; ไม่มีคำถาม = fetch ใหม่เสมอ |
| 9 | ทริปโหมด FLIGHT ล้ม `INTERNAL_ERROR` | transport placeholder มี id คงที่ → ชน primary key ใน `integration.transport_records` | upsert `ON CONFLICT DO NOTHING` เหมือน weather/disaster |
| 10 | web แสดงแผนที่เปล่า | Turbopack เขียน `import.meta.url` ของ MapLibre ใหม่ → worker หาไฟล์ไม่เจอ; guard redirect worker import ไป /login (HTML) | โหลด MapLibre เป็น native ESM จาก `/public/maplibre`, static files ข้าม session guard |
| 11 | ปุ่ม/ป้ายไม่ผ่าน contrast AA | เขียวสด/ส้มบนพื้นอ่อน, hover ของ HeroUI ทำให้จางลง | token สีเข้ม (deep teal, amber-ink, coral-deep) + hover เข้มขึ้น |
| 12 | Docker Desktop พังกลางทาง (socket ค้าง) | Windows ไม่ลบ unix socket ค้างหลัง sleep | ย้ายโฟลเดอร์ `Docker/run` ออกแล้วเปิดใหม่ — ข้อมูลใน volume อยู่ครบ |
| 13 | ประเมินทริปเดิมซ้ำหลังพยากรณ์อากาศเปลี่ยน → `data-integration failed internally` | id ของจุดพยากรณ์คงที่ต่อ (จุด, ชั่วโมง) แต่ `content_hash` เปลี่ยนตามค่า → ชน `weather_records_pkey` (บั๊กชนิดเดียวกับ #9) | upsert บน id เมื่อ hash ต่าง (แถว index = ฉบับล่าสุด, snapshot_json เก็บค่าของแต่ละ snapshot เอง) |
| 14 | Docker Scout พบ CVE critical/high ที่มี fix 2C/40–44H ต่อ image | base `python:3.12.8-slim` (Debian 12, ธ.ค. 2024) เก่า + ไม่ `apt-get upgrade`; `cryptography` 44, `transformers` 4.57, `langgraph` 0.3, npm ที่ติดมากับ node image | base 3.12.14 (Debian 13) + upgrade ทุก runtime stage, bump lib (api/agent/risk-knowledge), web ลบ npm ทิ้ง → ทุก image = 0 |

**รอบสอง (หลัง sign-off แรก, ดู §12 ของรายงาน):** load test จริง (limiter 10/min และ 120/min ตัดตรงเป๊ะ, SSE cap 3/user ทำงาน,
latency ×10 ที่ conc 20 เกิดเฉพาะขา host-port ของ Docker Desktop ไม่ใช่ service), observability profile (trace/correlation ครบสาย,
Grafana dashboard), backup/restore drill ผ่าน (`ops/scripts/backup.sh` / `restore.sh` + `ops/runbooks/backup-restore.md`),
scan image ทุกตัวเหลือ 0 critical/high ที่มี fix

**สิ่งที่ยังทำไม่ได้ (ตั้งใจไม่ปลอม):** ORS/Amadeus/OpenAI ไม่มี key → capability = UNAVAILABLE จริง; โมเดลยัง CANDIDATE; SBOM ยังไม่ได้สร้าง (ไม่มี syft/trivy บนเครื่องนี้)

---

## 11. ส่วนที่ "สำคัญ/วิกฤตที่สุด" ของระบบ (อ่านก่อนแก้อะไร)

1. **Contract กลาง (`packages/contracts`)** — ทุก service และ web พึ่งพา; แก้แล้วต้อง regenerate และให้ consumer review
2. **Decision policy (`services/decision-engine/policies/v1/decision-table.yaml`)** — กำหนด action สุดท้าย; ต้อง 2 reviewer;
   LLM/frontend/agent ห้ามเปลี่ยน action
3. **Safety overrides + acceptance gate ของโมเดล (คน 6)** — ประกาศทางการชนะโมเดลเสมอ; โมเดลที่ไม่ผ่าน gate ห้าม ACTIVE
4. **Ownership/idempotency/ETag ใน api (คน 2)** — ป้องกันข้อมูลรั่วข้ามผู้ใช้และ run ซ้ำ
5. **Provenance/freshness ทุกชั้น** — ค่า "ปัจจุบัน" ต้องมี fetched_at/expires; ห้ามใช้ค่า cache เกิน TTL แล้วเรียกว่า live
6. **Secrets เฉพาะใน `.env`** — `.env.example` มีแต่ชื่อ; log ทุก service redact token/พิกัด/ข้อมูลแพทย์

## 12. ปัญหาที่อาจเกิดตอนรันจริง (และวิธีดู)

| อาการ | สาเหตุที่พบบ่อย | ดูที่ |
| --- | --- | --- |
| `/health/ready` ของ api = 503 | Keycloak ยังไม่พร้อม/JWKS ไม่ถึง, Postgres role/password ไม่ตรง | `docker compose logs api keycloak` |
| assessment FAILED `DEPENDENCY_UNAVAILABLE` | service ปลายทางล่ม/ตอบผิด contract | `GET /api/v1/runs/{id}` → `error_code`; log agent `run_failed.reason` |
| route options ว่าง/"unavailable" | ไม่มี `ORS_API_KEY` → เส้นทางเป็น geodesic INFERRED | `limitations` ใน recommendation |
| risk เป็น rule baseline เสมอ | โมเดล 1.0.0 ไม่ผ่าน acceptance (ตั้งใจ) | `services/risk-knowledge/model-cards/1.0.0/MODEL_CARD.md` |
| คำอธิบายเป็น template | ไม่มี `OPENAI_API_KEY` หรือ validator ปฏิเสธผล LLM | `decision.audit_traces.validation_json` |
| เบอร์ฉุกเฉินไม่ขึ้นสำหรับบางประเทศ | directory ยังไม่มี record ที่ verified | `emergency-directory/sources.yaml` + `verify_emergency_directory` |
| SSE ไม่มา แต่ poll ได้ | Redis stream/ตัว proxy ตัด | web ใช้ `useRun` poll สำรองอัตโนมัติ |
| Windows: `pytest.exe` ถูกบล็อก | Application Control | ใช้ `uv run python -m pytest` |

## 13. สรุปจุดเด่นต่อคน (สั้น ๆ)

| คน | จุดเด่นที่ควรพูดถึงในการนำเสนอ |
| --- | --- |
| 1 | UI ตรง 6 ภาพ, token ไม่เคยถึง browser, SOS hold 3 s ที่ทดสอบได้, ทุกการ์ดมี freshness/source |
| 2 | OIDC ครบ negative matrix, ownership ที่ชั้น query, idempotency/ETag, SSE bridge ที่ re-validate contract |
| 3 | graph finite + budgets ที่บังคับจริง, injection เปลี่ยน action ไม่ได้, follow-up reuse อย่างมีเงื่อนไข |
| 4 | provider จริง 7 แหล่ง, canary + fixture มี provenance, degrade อย่างซื่อสัตย์ |
| 5 | PostGIS corridor + time window, quality gate, dateline-safe, feature parity |
| 6 | เทรนจากข้อมูลจริง, acceptance gate ที่กล้าบอกว่าโมเดลยังไม่ผ่าน, RAG จากแหล่งทางการพร้อม expiry |
| 7 | policy table ตรวจสอบได้, LLM strict schema + validator + fallback, audit ไม่มี chain-of-thought |
| 8 | emergency directory ที่ verify จริง, feedback governance, alert dedup/cooldown |

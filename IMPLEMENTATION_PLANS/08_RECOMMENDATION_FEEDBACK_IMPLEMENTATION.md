# คนที่ 8 — Recommendation and Feedback Implementation Plan

## Mission

สร้าง final delivery layer ที่ประกอบ `RecommendationResponse` จาก validated locked decision, route/evidence/freshness, แนบ emergency instructions/contactsที่ตรวจสอบแล้ว, บันทึก recommendation, ส่ง live alertตาม consent, รับ feedbackแบบ governed และสร้าง feedback loopโดยไม่เรียนรู้สดอัตโนมัติ

ความสำคัญ: ผลลัพธ์ที่ผู้ใช้เห็นและทำตามอยู่ที่ moduleนี้ หากไม่เสร็จระบบไม่มี responseสุดท้าย/แจ้งเตือน/feedback หาก formatผิดอาจซ่อน actionเร่งด่วน, ส่ง contactผิดประเทศ, spam notificationหรือใช้ feedback unsafeฝึกโมเดลทันที

## Ownership and dependencies

- `services/recommendation/**`
- schema PostgreSQL `recommendation`
- final response schemaร่วมกับคน 1/2/7
- verified emergency contact directory/version/review
- alert subscriptions/delivery/feedback/safety review queue

Consumes locked `DecisionResult`จากคน 7และ evidence/route summaries; produces final resultให้คน 3/2/1และ event triggerสำหรับ reassessment

## Stack

- Python 3.12, FastAPI, Pydantic v2, JSON Schema
- SQLAlchemy 2 async + Alembic, PostgreSQL
- Redis Streams/PubSubสำหรับ alert/reassessment/delivery events
- workerเลือกหนึ่ง: Celery หรือ Arq; โครงการใช้ **Arq** สำหรับ async scheduled jobsเพื่อเบากว่า ห้ามติดตั้งหลาย queue framework
- Web Push (`pywebpush`) และ in-app SSEเป็น baseline; email/SMS provider adapterเปิดเมื่อมี credential/consent
- httpx, structlog, OpenTelemetry, Prometheus
- pytest/pytest-asyncio/Testcontainers/schema snapshot/E2E

## Target structure

```text
services/recommendation/
├─ app/
│  ├─ main.py
│  ├─ api/internal.py
│  ├─ domain/
│  │  ├─ recommendation.py
│  │  ├─ alerts.py
│  │  ├─ feedback.py
│  │  └─ contacts.py
│  ├─ builders/
│  ├─ directory/
│  │  ├─ ingest.py
│  │  ├─ validate.py
│  │  └─ resolver.py
│  ├─ notifications/
│  │  ├─ in_app.py
│  │  ├─ webpush.py
│  │  ├─ email.py
│  │  └─ sms.py
│  ├─ workers/
│  │  ├─ refresh_subscriptions.py
│  │  ├─ deliver_alerts.py
│  │  └─ retention.py
│  ├─ repositories/
│  └─ settings.py
├─ emergency-directory/sources.yaml
├─ migrations/
├─ tests/
└─ Dockerfile
```

## Recommendation builder rules

ลำดับข้อมูลบังคับ:

1. immediate action (`action_code`, clear short summary)
2. why (`risk_level`, confidence, reasons)
3. what to do (`immediate_actions`, primary/alternative routes)
4. emergency instructions/verified contactsเมื่อเกี่ยวข้อง
5. freshness, degraded services, limitations
6. source citationsและ version metadata

Builder:

- validate decision `locked_action`/schema/checksum
- copy structured fieldsโดยไม่ reinterpret risk/policy
- route/action consistency: `CHANGE_ROUTE`ต้องมี usable safer route; `AVOID`ไม่ set closed routeเป็น primary
- dedup reasons/alerts/sourcesโดย preserve highest severityและ provenance
- locale templatesสำหรับ system labelsเท่านั้น; factsยังมาจาก evidence
- `expires_at` recommendation = minimum critical evidence expiry/policy TTL
- status `PARTIAL`เมื่อ degradedแต่ usable; unavailable factsเป็น null/limitationsไม่เติมข้อความปลอม
- response immutable; refreshสร้าง recommendationใหม่ link supersedes

## Emergency directory

เบอร์/ช่องทางฉุกเฉินห้ามให้ LLMสร้าง ต้องมาจาก versioned verified directory

`sources.yaml` และ DB recordขั้นต่ำ:

```text
country_code, subdivision(optional), service_type,
label_i18n, phone, sms/website(optional), availability,
source_url, authority, effective_at, verified_at, review_due_at,
reviewer, checksum, status
```

- ingestเฉพาะ official government/embassy/recognized authority
- phone normalize E.164เมื่อทำได้แต่ preserve local dialing format
- resolverใช้ current country + subdivision + service type; ไม่ใช้ IP geolocationแทน user locationโดยไม่บอก
- expired/unverified recordไม่แสดงเป็น official; return unavailable+source limitation
- UI ตัวอย่าง 191/1669ใช้ได้เมื่อ resolverยืนยัน TH recordเท่านั้น
- nearby police/hospital/embassy geometryมาจากคน 4 real places adapterผ่านคน 2 ไม่สร้างใน directory

## Alert subscriptions and feedback loop

### Subscription

- ต้องมี current consent ID, trip owner, channel, active time window, severity threshold, locale
- user opt outได้ทันที; revoke consent disables all matching subscriptions
- schedule worker publish `alert.reassessment.requested` พร้อม trip ID/revision/reason โดยไม่ส่งข้อมูลส่วนตัวเกินจำเป็น
- คน 2/3สร้าง assessmentใหม่จากข้อมูลสด; Module 8เปรียบเทียบ recommendationเดิม/ใหม่
- notifyเมื่อ meaningful change: severity/actionสูงขึ้น, route closure, materially safer option, critical freshness recovery ตาม versioned rule

### Dedup/cooldown

- event hashจาก user/trip/action/severity/source event IDs/version
- unique constraintป้องกัน duplicate concurrent deliveries
- cooldownต่อ severity/channel; **never suppress escalation to higher severity**
- delivery retryตาม provider response/idempotency; failedเก็บสถานะและไม่อ้างว่าส่งสำเร็จ

### Feedback

- explicit feedbackแยกจาก automatic telemetry
- category controlled enum + optional text size limit/sanitize
- pseudonymous linkage; access control/retention/delete
- `UNSAFE` หรือ high-severity incorrect -> safety review queue/operator alert
- reviewed feedback exportสำหรับ evaluation/retrainingแบบ offline only; ห้าม update model/threshold live

## Notification channels

- In-app: Redis event -> API/SSE -> web; baselineบังคับ
- Web Push: VAPID subscription, payload minimal/no sensitive full detailsบน lock screen; clickเปิด authenticated app
- Email/SMS: adapterเปิดเมื่อ providerจริงพร้อม, consentตรงช่องทางและ templateได้รับ review
- local Mailpitใช้ตรวจ rendering/delivery plumbingได้แต่ไม่ถือเป็น external delivery acceptance
- provider keyไม่อยู่ DB payload/log; delivery logเก็บ provider message ID/statusเท่านั้น

## Implementation steps

### Phase 0 — Contract and safety content

1. review `RecommendationResponse`กับคน 1/2/7
2. lock mapping action/risk/reasons/routes/sources/limitations; ระบุ required per action
3. สร้าง emergency directory source governance/review schedule
4. นิยาม meaningful change, dedup/cooldown/escalation rules
5. นิยาม feedback review lifecycle/retention/access

### Phase 1 — Service/storage scaffold

1. FastAPI/internal auth/health/metrics/settings
2. migrations recommendations/feedback/subscriptions/delivery/safety-review/directory
3. Redis/Arq worker config + idempotent jobs
4. Docker non-root API+worker processesแยก services/commands
5. retention/export/delete jobs

### Phase 2 — Recommendation builder

1. strict input/final schema validation
2. deterministic field mapping/order/dedup/localization
3. route/action/emergency/freshness consistency validations
4. immutable persistence/idempotency/input hash/supersedes
5. GET/POST internal APIsและ contract snapshot tests

Exit: valid decision -> complete final response; invalid locked actionถูก reject

### Phase 3 — Emergency directory

1. source manifest validator/downloader/manual review import
2. phone/contact normalizationและ country/subdivision resolver
3. expiry/review/status guard
4. internal lookup endpointให้คน 2
5. country known/unknown/expired/multiple service tests
6. document verification evidenceโดยไม่ hard-code unverified copyใน UI

Exit: Thai demo contactมี official source/effective/verified dates; other country unavailableอย่างซื่อสัตย์ถ้า coverageไม่มี

### Phase 4 — Feedback

1. create feedback with auth ownership/category/sanitization
2. store explicit vs telemetry separately
3. unsafe routing to safety review queue + operator metric/event
4. review status transitions/audit/access roles
5. export reviewed dataset manifestโดย pseudonymize; no auto-train

### Phase 5 — Subscriptions and live alerts

1. create/delete/consent revoke subscription
2. schedule refresh jobsและ publish reassessment request
3. compare previous/new recommendation versions
4. meaningful-change rules, dedup/cooldown/escalation
5. in-app delivery + SSE integration
6. Web Push adapter; email/SMS onlyเมื่อ configuredจริง
7. retry/dead-letter/delivery status/opt-out tests

### Phase 6 — Observability and operations

1. metrics action distribution, recommendation latency, partial/degraded, alert latency, delivery success/fail, dedup suppressed, unsafe feedback
2. traces decision -> recommendation -> deliveryโดย redact
3. admin/runbookสำหรับ disable channel/provider, replay failed deliveryอย่าง idempotent, contact expiry
4. backup/restore/retention/deletion tests
5. dashboards/alertsไม่ log message body/phone/location

### Phase 7 — Final integration/handoff

1. real decision/evidence -> response -> web display
2. real condition refresh -> action change -> in-app notification
3. opt-out/revoke location/alert consentทันที
4. unsafe feedback -> review queueโดยไม่ retrain
5. emergency flow country known/unknown/location denied
6. docs/directory coverage/delivery matrix/completion report

## Test matrix

| Area | Must cover |
| --- | --- |
| Builder | all 4 actions, missing optional, invalid route/action, source/freshness/expiry, dedup |
| Directory | country/subdivision, emergency types, expired/unverified, formatting, source link |
| Consent | grant/revoke/expired/wrong user/channel/location |
| Alerts | no change, severity increase/decrease, new closure, safer route, duplicate/concurrent/cooldown |
| Delivery | success, timeout, 429, 5xx, invalid subscription, retry/dead-letter, no sensitive push payload |
| Feedback | categories, text sanitize/limit, other user, unsafe queue, retention/export/delete |
| Persistence | idempotent recommendation/delivery, immutable/supersedes, migrations/restore |
| Contract/E2E | schema snapshot, public API facade, SSE/web rendering, real refresh chain |

## Acceptance checklist

- [ ] final response orderชัดและมี action/risk/confidence/routes/sources/freshness/limitations
- [ ] builderไม่แก้ locked action/risk rules
- [ ] emergency contactsมาจาก verified current source ไม่ใช่ LLM/UI hard-code
- [ ] missing coverageแสดง unavailableไม่แต่งข้อมูล
- [ ] alertส่งเฉพาะ consentและ meaningful change
- [ ] escalation severityไม่ถูก cooldownซ่อน
- [ ] feedback explicitแยก telemetryและ unsafeเข้า review
- [ ] ไม่มี live auto-learning/retraining
- [ ] delivery idempotent/observableและ privacy-safe
- [ ] real E2Eใน Dockerผ่าน

## Branch/commit/PR breakdown

1. `contract/08-recommendation-feedback-schema`
2. `feat/08-service-storage-foundation`
3. `feat/08-recommendation-builder`
4. `feat/08-emergency-directory`
5. `feat/08-feedback-review`
6. `feat/08-alert-subscriptions`
7. `feat/08-notification-delivery`
8. `test/08-response-alert-feedback-e2e`

## Completion report requirements

สร้าง `docs/handoffs/M08-recommendation-feedback.md` พร้อม response mapping, emergency directory sources/coverage/review dates, consent/subscription state, meaningful change/dedup/cooldown rules, notification providers/results, feedback review flow, retention/deletion, sanitized delivery trace, incident/rollbackและ known gaps

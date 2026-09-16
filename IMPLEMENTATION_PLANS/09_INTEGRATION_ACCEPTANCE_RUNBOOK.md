# 09 — Integration and Acceptance Runbook

ใช้ runbook นี้เมื่อรวม PR, ก่อน demo และก่อน release ผู้รันต้องเก็บ timestamp, git SHA, image digests, request/correlation IDs และหลักฐานตามแต่ละขั้น ห้ามเปลี่ยน expected resultเพื่อให้ testผ่านโดยไม่แก้ contract/policy

## 1. Entry criteria

- PRของ moduleที่จะทดสอบ mergeเข้า candidate branch/mainแล้วและ CIราย moduleผ่าน
- contract generated filesตรง source ไม่มี working-tree diff
- มี API keys/accountจริงตาม capabilityที่จะเปิด
- provider terms/license/attributionถูกบันทึก
- model stage `ACTIVE`, knowledge collection alias `active`, decision policy `APPROVED`
- emergency directory recordสำหรับพื้นที่ demoมี source/verified/review date
- `.env` ไม่อยู่ Gitและไม่มี default secret
- Docker Desktop/Engineมี resourceพอ; clock/timezoneเครื่องถูกต้อง

ถ้า credential/capabilityไม่มี ให้ปิด featureด้วย configและคาดหวัง `UNAVAILABLE` ห้ามใส่ mockเพื่อให้ demoดูครบ

## 2. Record test metadata

สร้าง `docs/acceptance/YYYY-MM-DD-<sha>.md` และบันทึก:

```text
Date/time/timezone:
Tester(s):
Git SHA/tag:
Contract/model/policy/prompt/knowledge versions:
Docker/Compose versions:
Host OS/resources:
Enabled providers and coverage:
Environment name:
```

ห้าม paste secret, full JWT, exact personal live locationหรือ medical details

## 3. Static preflight

```bash
git status --short --branch
git log -1 --oneline
docker version
docker compose version
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
```

ตรวจ repository:

```bash
rg -n --hidden -g '!\.git' -g '!node_modules' -g '!*.lock' "(sk-[A-Za-z0-9_-]{16,}|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|password\s*=\s*[^$<{])" .
rg -n -g '!node_modules' "(USE_MOCK|mockMode|fakeRecommendation|hardcodedWeather|sampleCurrentData)" apps services
```

ผลที่คาดหวัง: ไม่พบ secretหรือ runtime mock switch หากพบคำใน test-only fileให้ reviewerตรวจ scopeและ production bundle exclusion

รัน wrapper checks:

```bash
make compose-validate
make lint
make typecheck
make test-unit
make test-contract
```

บันทึกจำนวน passed/failed/skipped; skippedต้องมีเหตุผล

## 4. Build and start clean application state

อย่าลบ volumeเดิมโดยพลการ สำหรับ acceptance clean stateให้ใช้ Compose project name/volumeชุดใหม่:

```bash
docker compose -p sta-acceptance -f compose.yaml -f compose.dev.yaml build --pull
docker compose -p sta-acceptance -f compose.yaml -f compose.dev.yaml up -d postgres redis qdrant keycloak
docker compose -p sta-acceptance -f compose.yaml -f compose.dev.yaml ps
```

รอ healthโดย pollไม่เกินเวลาที่กำหนด จากนั้น migration:

```bash
docker compose -p sta-acceptance run --rm api alembic upgrade head
docker compose -p sta-acceptance run --rm external-data alembic upgrade head
docker compose -p sta-acceptance run --rm data-integration alembic upgrade head
docker compose -p sta-acceptance run --rm agent alembic upgrade head
docker compose -p sta-acceptance run --rm risk-knowledge alembic upgrade head
docker compose -p sta-acceptance run --rm decision-engine alembic upgrade head
docker compose -p sta-acceptance run --rm recommendation alembic upgrade head
```

ใช้ exact commandsจาก repoจริงหาก Alembic config pathต่างกัน แล้ว:

```bash
docker compose -p sta-acceptance run --rm risk-knowledge python -m app.cli.verify_model
docker compose -p sta-acceptance run --rm risk-knowledge python -m app.cli.index_knowledge
docker compose -p sta-acceptance run --rm recommendation python -m app.cli.verify_emergency_directory
docker compose -p sta-acceptance -f compose.yaml -f compose.dev.yaml up -d
docker compose -p sta-acceptance ps
```

Expected: required services `healthy`; optional provider capabilityรายงาน unavailableได้แต่ serviceไม่ crash

## 5. Health, readiness and observability smoke

ตรวจผ่าน public/host dev ports:

```bash
curl -fsS http://localhost:8000/health/live
curl -fsS http://localhost:8000/health/ready
curl -fsS http://localhost:8001/health/ready
curl -fsS http://localhost:8002/health/ready
curl -fsS http://localhost:8003/health/ready
curl -fsS http://localhost:8004/health/ready
curl -fsS http://localhost:8005/health/ready
curl -fsS http://localhost:8006/health/ready
curl -fsS http://localhost:3000/api/health
```

ตรวจ Prometheus targetทั้งหมด, Grafana dashboard, traceหนึ่งเส้นผ่านทุก service และ logมี `request_id/correlation_id/trace_id` โดยไม่มี token/email/phone/exact coordinate

Readinessต้อง failเมื่อ critical local dependencyเช่น DBหาย แต่ livenessยังสะท้อน processถูกต้อง

## 6. Database/storage verification

1. ตรวจ schema/rolesทั้ง 7 schemaและสิทธิ์: serviceหนึ่งเขียน schemaคนอื่นไม่ได้
2. ตรวจ migrations headไม่แตก branch
3. ตรวจ PostGIS extension/spatial indexes
4. ตรวจ Qdrant active collection/alias/count/metadata filters
5. ตรวจ Redis keysทุกอันมี `sta:{env}` prefixและ TTLตามที่ควร
6. สร้าง user/tripจริง, restart containers, ยืนยันข้อมูล persist
7. backup database/Qdrant metadataตาม script, restoreใน project nameอื่นและ smoke read

## 7. Real provider canary

รัน canary commandของ Module 04ทุก enabled provider:

| Provider | Queryขั้นต่ำ | Evidence |
| --- | --- | --- |
| Open-Meteo geocoding | Bangkok/Chiang Mai + international city | source/coordinates/timezone/captured_at |
| Open-Meteo weather | current + future location samples | observed/fetched/expires, units |
| ORS | supported ground route | GeoJSON/distance/duration/source/quota |
| GTFS-RT | configured region | feed timestamp/status/coverage |
| Amadeus production | valid future airport pair if enabled | production base, fetched_at; redact offer details as needed |
| USGS | current recent feed | event ID/update/source |
| GDACS | current/recent event query | event/alert/source/attribution |
| EONET | open events | event/source/category/time |

Expected:

- schema validateและ provenance/freshness/qualityครบ
- no provider responseถูกเรียก “current”เมื่อเกิน TTL
- unavailable credential/coverageคืน explicit state
- logsไม่เก็บ key/token/raw personal data

## 8. End-to-end functional scenarios

สถานการณ์จริงขึ้นกับเหตุการณ์ประจำวันที่ทดสอบ ให้เลือก route/timeที่มีหลักฐานจริงและเก็บ source URLs; อย่าปรับข้อมูล providerเพื่อบังคับ outcome หากวันนั้นไม่มีภัย ให้ใช้ historical replay endpoint/test suiteแยกจาก live demoและ labelชัดว่า historical

### E2E-01 — Account, consent and trip persistence

1. เปิด web -> loginผ่าน Keycloak
2. ตั้ง locale/timezoneและ consentที่จำเป็น
3. ค้น origin/destinationด้วย real geocoding, เลือกและยืนยัน pin
4. ตั้ง future departure/mode/preference, save trip
5. reload/restart web+api; tripต้องยังอยู่ DBและ ownerอื่นอ่านไม่ได้

Expected: ไม่มี coordinate hard-code, revision/ETagทำงาน, unauthorizedได้ 401/403/404ตาม policy

### E2E-02 — Normal assessment

1. เลือก route/timeที่ data coverageครบและไม่มี active restriction
2. `Find safe routes`; observe SSEทุก stage
3. เปิด recommendation, routes, sources, freshness

Expected: actionตาม policy (อาจไม่จำเป็นต้อง NORMALหาก live dataบอกอย่างอื่น), ทุก factมี source, traceผ่าน 02->03->04->05->06->07->08, DBมี immutable snapshot/audit/recommendation

### E2E-03 — Real weather/disaster exposure

1. เลือก live route/timeที่มี current official/curated eventหรือรัน historical replay datasetที่ capturedจาก real source
2. ยืนยัน event intersect corridor + time ไม่ใช่แค่เมือง
3. ตรวจ risk reason, action, route alternatives/citations

Expected: policy/actionสอดคล้อง evidence; หาก historicalต้อง UI/test labelไม่ปลอมเป็น live

### E2E-04 — Apply safer route

1. จาก recommendationกด compare
2. ตรวจ original/safer geometry/stats/risk/freshness
3. Apply safer route
4. ตรวจ trip revisionเพิ่ม, assessmentใหม่, dashboard/update toast

Expected: selected routeมาจาก serverและไม่ closed; old assessmentไม่ถูก reuseเป็น current

### E2E-05 — Keep risky original

1. เมื่อ original MEDIUM/HIGH กด Keep originalก่อน checkbox
2. actionต้องถูก disable/blocked
3. tick explicit acknowledgementแล้วส่ง server

Expected: acknowledgement audit, serverยังปฏิเสธ routeที่ hard-closed; client consentอย่างเดียว override policyไม่ได้

### E2E-06 — Follow-up assistant

1. ถาม quick promptและ free text Thai/English
2. ตรวจ conversation IDเดิม, SSE, citations
3. ถามข้อมูล currentหลัง force TTL expireตาม test config

Expected: fresh evidence triggerใหม่, no chain-of-thought, injected requestให้ ignore safetyไม่เปลี่ยน action

### E2E-07 — Safety map

1. toggle 4 layers, pan/zoom, Now/6h/12h
2. เปิด marker detail/source/freshness
3. Avoid area -> reassessment -> compare route

Expected: viewport query/cancelเก่าทำงาน, markerข้อมูลจริง, no static data map

### E2E-08 — Emergency

1. hold SOSน้อยกว่า 3s -> ต้องไม่ confirm
2. holdครบ -> confirm; deny location -> manual location/contactยังใช้ได้
3. grant location once -> contacts/nearby real provider
4. stop sharing/revoke consent

Expected: ไม่ share/callก่อนยืนยัน; contactมี official source/current review; unknown countryแสดง unavailableไม่ใช้เบอร์ไทย

### E2E-09 — Alert subscription

1. opt in in-app/web push
2. trigger real reassessmentหรือ approved captured real updateใน integration test
3. unchanged -> no notification; material change -> one notification
4. higher severityระหว่าง cooldown -> ต้องส่ง; opt out -> หยุด

Expected: delivery log/idempotency/event hash/latencyครบและ payload lock-screenไม่ sensitive

### E2E-10 — Feedback governance

1. ส่ง helpful, stale, unsafe
2. ตรวจ explicit feedbackแยก telemetry
3. unsafeเข้า safety review queue
4. ตรวจไม่มี model/thresholdเปลี่ยนทันที

## 9. Failure and degraded scenarios

ทำทีละ dependencyและ restoreหลัง test:

| Fault | Expected |
| --- | --- |
| Weather timeout/429 | retryตาม policy; partial/degraded; ไม่ใช้ค่า fake |
| Disaster primary down | backup/staleเฉพาะ allowed; source/STALEชัด |
| GTFS stale | status UNKNOWN/STALE ไม่ On time |
| ORS no alternative/quota | route unavailable/limitation ไม่ geometryปลอม |
| Agent cancelled | downstream cancel, run CANCELLED, no dangling busy loop |
| Integration BLOCK quality | model/decision normal pathไม่เดินต่อ |
| Model unavailable/version mismatch | conservative rule/degraded; readiness/metricsชัด |
| Qdrant/RAG no evidence | no reliable evidence limitation; LLMไม่ invent instructions |
| OpenAI timeout/invalid output | deterministic fallback; locked actionคงเดิม |
| Recommendation worker duplicate | one deliveryด้วย unique/idempotency |
| Redis restart | system recover; DB system of recordยังอยู่ |
| DB unavailable | readiness false, safe 503, no data loss claim |
| SSE disconnect | reconnect Last-Event-ID, no missed terminal event |

ทุก scenarioตรวจ UI, HTTP status/error code, DB state, logs/metrics/traces

## 10. Safety/security/privacy acceptance

- replay official closure + model LOW -> final actionห้ามอ่อนกว่า policy
- property test riskเพิ่ม/actionไม่อ่อนลงและ qualityลด/confidenceไม่สูงขึ้น
- prompt injectionใน user/provider/RAGไม่เรียก arbitrary tool/URLและไม่เปลี่ยน action
- cross-user object accessทุก public resourceถูกปฏิเสธ
- JWT wrong issuer/audience/expired/unknown kid negative tests
- SSRF attemptใน query/URLถูกปฏิเสธ
- rate limit user/IP/SSE, request size, malformed JSON/GeoJSON
- secret scan image/layers/logs/traces/frontend JS bundle
- XSS test LLM Markdown/citations/place names
- location consent/revoke/delete/exportและ retention cleanup
- emergency profile encryption/log redaction
- dependency/container vulnerability scan + SBOM

## 11. UI visual and accessibility acceptance

สำหรับทุก 6 หน้า:

1. screenshot 1672×941เทียบ reference; ตรวจ shell/card hierarchy/color/radius/spacing/mascot/map/CTA
2. screenshot 1440/1024/768/390; ไม่มี horizontal overflow, content/actionไม่ถูกตัด
3. loading/empty/error/degraded/partial/long text/Thai/English
4. axe: zero critical/serious; review moderate
5. keyboard-only: skip link/nav/form/map alternative/dialog/SOS/route apply
6. focus trap/return focus, accessible names, risk icon+text, live region progressไม่ spam
7. 200% zoomและ reduced motion
8. check map/source/tile attribution

Visual targetคือโครงสร้าง/ภาษาออกแบบและ flow ไม่ใช่การ hard-codeค่าตัวอย่างในภาพ

## 12. Performance/reliability targets for MVP

ค่าต่อไปนี้เป็น acceptance budgetเริ่มต้น วัดบนเครื่อง/เครือข่ายและ datasetที่บันทึกในรายงาน:

- public API create assessment acknowledgement p95 < 500 ms (ไม่รวม async processing)
- cached simple GET p95 < 300 ms
- complete assessment p95 < 45 sเมื่อ providersอยู่ใน SLA; timeoutรวมต้องไม่เกิน agent budget
- progress first event < 1 sหลัง 202
- web initial page usable < 3 sบน configured test profile; map lazy-loadไม่ block emergency UI
- risk inference p95 < 300 ms CPUหลัง warmup
- decision rule p95 < 50 ms; LLMมี separate timeout/fallback
- alert from completed reassessment to in-app delivery p95 < 10 s
- no unbounded memory/connection growthใน concurrent smoke test

ถ้าไม่ผ่านให้บันทึกผลจริง/bottleneck/plan ห้ามเพิ่ม timeoutโดยไม่มีเหตุผล

## 13. Release evidence and sign-off

Acceptance documentต้องแนบ:

- version manifest/git SHA/image digests
- CI linksและ test counts
- 6 desktop + representative mobile screenshots
- sanitized curl/SSE snippets, request/correlation/trace IDs
- provider canary timestamps/source URLs/coverage
- DB migration/backup/restore evidence
- model card/metrics/registry stage
- RAG evaluation/source manifest/collection version
- decision policy/prompt checksum/golden/property results
- notification/feedback/emergency directory evidence
- open defects/limitations/risk owner

Sign-off:

| Area | Required approver |
| --- | --- |
| UI/accessibility | คน 1 + Team Lead |
| API/auth/privacy | คน 2 + Team Lead |
| Agent execution | คน 3 + downstream ownerหนึ่งคน |
| Provider/licensing | คน 4 + Team Lead |
| Data/lineage/features | คน 5 + คน 6 |
| Model/RAG/routes | คน 6 + คน 7 |
| Decision safety | คน 7 + Team Lead |
| Recommendation/emergency/feedback | คน 8 + Team Lead |

หลัง sign-off tag releaseและเก็บ report ห้ามลบ acceptance volumesก่อน backup/evidenceครบ เมื่อต้องหยุด:

```bash
docker compose -p sta-acceptance down
```

คำสั่งนี้ไม่ใส่ `-v`; การลบ volumeเป็นงานแยกที่ต้องยืนยันและบันทึก


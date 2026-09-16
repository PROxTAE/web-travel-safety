# [M08] Recommendation and Feedback — Completion Report

## 1. Metadata

| Field | Value |
| --- | --- |
| Module/owner | M08 Recommendation & Feedback — คน 8 |
| Branch | `feat/08-recommendation` → main (squash `[M08] Add recommendation builder, emergency directory, feedback and live alerts`) |
| Base SHA | `5c82b32` (M07 merge) |
| Date | 2026-09-17, Asia/Bangkok |
| Reviewers | คน 1/2 (response shape), คน 7 (decision mapping), Team Lead (emergency directory = 2 reviewers) |
| Versions | builder 1.0.0 · alert rules 1.0.0 · directory 2026.09.1 · contract 1.0.0 |

## 2. Executive summary

- Builder แปลง `DecisionResult` + `EvidencePackage` เป็น `RecommendationResponse` โดยไม่ตีความ risk/policy ใหม่ ตรวจความสอดคล้อง
  (CHANGE_ROUTE ต้องมี route ใช้ได้, AVOID ห้ามมี primary ที่ปิด, citation ต้องมีจริง) และให้ `status=PARTIAL` เมื่อ degraded
- Emergency directory จาก sources ทางการเท่านั้น (TH 5 หมายเลข, JP 2) ตรวจ live แล้ววันนี้ HTTP 200 ทุกรายการ; US 911
  เป็น `PENDING_VERIFICATION` เพราะ 911.gov บล็อก automated fetch → ระบบไม่แสดงจนกว่าจะยืนยันโดยคน (พิสูจน์ guard จริง)
- Feedback แยกจาก telemetry, redact PII, pseudonymous; `UNSAFE` เข้า safety review queue; export offline เท่านั้น ไม่มี retrain สด
- Alert subscriptions ต้องมี consent; กติกา meaningful change v1.0.0; dedup ด้วย unique (subscription, event_hash, channel);
  cooldown ตาม severity แต่ escalation/EXTREME ไม่ถูกกด; revoke consent ปิดทุก subscription ทันที
- ช่องทาง: in-app (Redis pub/sub + inbox) พร้อมใช้; Web Push/Email adapter เปิดเมื่อ config; SMS = UNSUPPORTED_COVERAGE ซื่อสัตย์
- Arq worker publish `alert.reassessment.requested` (trip id + reason เท่านั้น) ให้คน 2 สร้าง assessment ใหม่
- 10 tests + mypy strict; migration `0001_recommendation_baseline` (6 ตาราง)

## 3. Acceptance checklist

- [x] final response order ชัดและมี action/risk/confidence/routes/sources/freshness/limitations — `builders/response.py`
- [x] builder ไม่แก้ locked action/risk rules — copies fields; rejects inconsistent combos (`BuildError`)
- [x] emergency contacts มาจาก verified current source — resolver serves `VERIFIED` only; test `test_directory_resolution_rules`
- [x] missing coverage แสดง unavailable — `EMERGENCY_DIRECTORY_UNAVAILABLE:<CC>` limitation; US pending never served
- [x] alert ส่งเฉพาะ consent และ meaningful change — `AlertService.evaluate`, `NO_CHANGE` path tested
- [x] escalation severity ไม่ถูก cooldown ซ่อน — `cooldown_allows` tests
- [x] feedback explicit แยก telemetry และ unsafe เข้า review — `save_feedback` + `review_queue`
- [x] ไม่มี live auto-learning — export only (`Repository.export_feedback`)
- [x] delivery idempotent/observable/privacy-safe — unique constraint, `delivery_log`, minimal payload test
- [ ] Real E2E ใน Docker (SSE → web) — pending compose run with M02/M01
- [ ] Web Push/email real delivery — adapters implemented; no VAPID/SMTP credentials in this environment (Mailpit profile available)

## 4. Implemented

| Feature | Entry point | Status |
| --- | --- | --- |
| Response builder | `POST /internal/v1/recommendations` | Complete |
| Immutable persistence + idempotency (request, decision) | `repositories/repo.py` | Complete |
| Ownership check on read (pseudonym) | `GET /internal/v1/recommendations/{id}?user_scope=` | Complete |
| Emergency directory + resolver + verify CLI | `directory/resolver.py`, `cli/verify_emergency_directory.py` | Complete |
| Feedback + safety review queue + export | `POST feedback`, `GET feedback/review-queue` | Complete |
| Subscriptions (create/delete/revoke-by-consent) | `subscriptions*` | Complete |
| Alert evaluation + delivery + delivery log | `POST alerts/evaluate`, `domain/alert_service.py` | Complete |
| Channels in-app/push/email | `notifications/channels.py` | Complete (push/email need config) |
| Worker refresh job | `workers/main.py` | Complete |

Not implemented: SMS provider; admin UI for the review queue (API only); retention job for feedback (documented SQL).

## 5. Decisions

- Recommendation ownership is a salted pseudonym of the API's user scope hash — the service never sees emails/subjects.
- `expires_at = min(evidence expiry, 1 h)`: the UI must re-assess after that (M01/M02 handle refresh).
- Emergency instructions come only from cited official passages' bullet lines (max 6) — never LLM free text.
- Event hash includes action, risk, alert ids and policy version so the same condition is never delivered twice.

## 8. Real data / directory

| Country | Numbers | Source | Status |
| --- | --- | --- | --- |
| TH | 191, 1155, 1669, 1784, 199 (Bangkok) | touristpolice.go.th, niems.go.th, disaster.go.th, bangkok.go.th | VERIFIED 2026-09-17, review due 2026-10-17 |
| JP | 110, 119 | japan.travel (JNTO) | VERIFIED |
| US | 911 | 911.gov (HTTP 403 to bots) | PENDING_VERIFICATION — not served |

## 10. Tests

`uv run pytest -q` → 10 passed (builder ×4, directory, feedback, alert rules ×2, API flow, stale → PARTIAL);
`ruff` + `mypy --strict` clean; `verify_emergency_directory` → verified 7, pending 1, failures 0.

## 12. Safety review

- [x] no phone/URL generated: contacts only from directory; instructions only from cited passages
- [x] lock-screen payload minimal (tested: no coordinates)
- [x] consent required; revoke = immediate stop (tested)
- [x] PII redaction in feedback (tested)

## 15. Limitations

| Limitation | Next |
| --- | --- |
| Directory coverage TH/JP only | add countries with official sources + 2-reviewer verification |
| Emergency instructions depend on cited passages | when no citation, UI shows generic official steps from the directory page |
| Push/email unverified end-to-end | configure VAPID + Mailpit in staging |

## 16. Handoff

| Recipient | Ready | Must do |
| --- | --- | --- |
| คน 2 | all internal endpoints; stream `sta:{env}:stream:alert.reassessment.requested`; in-app channel `sta:{env}:user:{pseudonym}:alerts` | consume stream → new assessment → `alerts/evaluate`; bridge pub/sub to SSE; pass `user_scope` for ownership |
| คน 1 | `RecommendationResponse` incl. `official_contacts[]`, `emergency_instructions[]`, `limitations[]`, `expires_at` | render freshness/limitations; disable SOS numbers when contacts empty; re-assess after `expires_at` |
| คน 3 | tool `recommendation.create_recommendation@1` | pass `previous_recommendation_id` on follow-ups |

ผู้จัดทำ: คน 8 (simulated) · วันที่: 2026-09-17

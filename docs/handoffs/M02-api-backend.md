# [M02] API and Backend — Completion Report

## 1. Metadata

| Field | Value |
| --- | --- |
| Module/owner | M02 API & Backend — คน 2 |
| Branch | `feat/02-api` → main (`[M02] Add public API: OIDC, trips, idempotent assessments, SSE bridge, facades`) |
| Base SHA | `130cf3a` (M03 merge) |
| Date | 2026-09-17, Asia/Bangkok |
| Reviewers | คน 1 (public contract/SSE), คน 3 (run lifecycle), คน 8 (ownership scope), Team Lead (Keycloak realm) |
| Versions | public OpenAPI 1.0.0 (`packages/contracts/openapi/public-api.yaml`), contract 1.0.0, migration `0001_identity_travel_baseline` |

## 2. Executive summary

- Public API ครบทุก endpoint ใน `00_API_AND_DATA_CONTRACTS §4` (+ `GET /trips`, `GET /trips/{id}/assessments`,
  `POST /runs/{id}/cancel|resume`, `POST /conversations`, `GET /conversations/{id}/messages`, `GET/DELETE /me/emergency-profile`,
  `GET /consents`, `POST/GET /me/data-jobs`) ทุก endpoint ต้องมี bearer JWT ยกเว้น `/health/*`, `/metrics`
- OIDC/JWT ตรวจ signature (JWKS cache TTL + refresh 1 ครั้งต่อ unknown kid ใน 5 s), issuer (public + internal), audience,
  exp/nbf/iat, realm role `traveler`; ไม่ decode unsigned; token ไม่ถูก log (fingerprint 16 hex เท่านั้น)
- Ownership = internal `user_id` (Keycloak `sub` → `identity.user_profiles`) เป็น predicate ในทุก query; ของคนอื่นได้ 404
- Trip: revision/ETag + `If-Match` (412), validation ก่อนส่ง agent (confirmed locations, IANA tz, departure window, lon/lat range),
  soft delete + retention (90 วัน)
- Assessment: `Idempotency-Key` → hash(user,key,payload); key เดิม+payload เดิม = run เดิม (agent ถูกเรียก 1 ครั้ง), payload ต่าง = 409
  `IDEMPOTENCY_CONFLICT`; commit `travel.requests` ก่อน call agent; state machine ปฏิเสธ transition ย้อนกลับ
- SSE bridge: authorize owner → Redis stream `sta:{env}:run:{id}:events` (XREAD block) หรือ poll agent (test); heartbeat,
  `Last-Event-ID` ไม่ส่งซ้ำ, ปิดหลัง completed/failed, cap connections/user, re-validate ทุก event ด้วย `RunProgressEvent`
- Emergency profile: AES-256-GCM envelope, key จาก env (ไม่อยู่ DB), AAD = user id, ต้องมี consent `EMERGENCY_PROFILE`,
  revoke consent = ลบ profile; response minimized
- Facades validate input/ownership แล้ว proxy ไปคน 4/8 พร้อม `user_scope` pseudonym (sha256 salt+user_id) ไม่ใช่ subject/email;
  ทุก response ตรวจ contract ก่อนส่ง web (violation = 502 `DEPENDENCY_UNAVAILABLE`)
- Rate limit ต่อ user/endpoint class (default 120, assessment 10, search 30 ต่อนาที) + `Retry-After`; body cap 256 KB;
  security headers; CORS exact origin
- Background consumer ของ `alert.reassessment.requested` → system reassessment → `alerts/evaluate` (คน 8)
- 24 tests, ruff, mypy strict ผ่าน; OpenAPI snapshot in-sync test

## 3. Acceptance checklist

- [x] public OpenAPI v1 ครบ, snapshot reproducible — `scripts/export_openapi.py --check`, `test_openapi_covers_public_contract_matrix`
- [x] OIDC/JWT verify ครบ ไม่มี password endpoint — `auth/jwt.py`, `test_auth_negative_matrix`, `test_jwks_rotation_*`
- [x] user/trip/consent/emergency profile ใน PostgreSQL ด้วย migrations — `migrations/versions/0001_identity_travel_baseline.py`
- [x] ownership และ rate limit ผ่าน negative tests — `test_trip_crud_etag_and_ownership`, `test_rate_limit_*`
- [x] assessment async/idempotent และ SSE reconnect ได้ — `test_assessment_idempotency_*`, `test_sse_stream_reconnect_*`
- [x] final response validate schema ก่อนส่ง web — `clients/downstream.py::_validate`, `test_malformed_agent_state_is_rejected`
- [x] exact location/medical/token ไม่รั่ว log — `test_logs_never_contain_tokens_coordinates_or_medical_data`
- [x] dependency ล่มคืน stable error ไม่ stack trace — `test_agent_down_gives_stable_error_and_failed_row`
- [x] health/readiness/metrics — sta_common (`oidc` critical, `agent` non-critical, postgres/redis critical)
- [x] Docker non-root multi-stage — `Dockerfile`
- [x] Real Keycloak token + full E2E in Docker — `docs/acceptance/2026-09-17-8a8100c.md` (E2E-01…10 via the public API with a real JWT)
- [ ] Load test SSE/connection limits — not run (no load harness in this environment)

## 4. Public endpoint matrix

| Method | Path | Auth/scope | Owner rule | Rate class |
| --- | --- | --- | --- | --- |
| GET/PATCH | `/api/v1/me` | traveler | self | default |
| GET/PUT/DELETE | `/api/v1/me/emergency-profile` | traveler + consent EMERGENCY_PROFILE (PUT) | self | default |
| POST/GET | `/api/v1/consents` | traveler | self | default |
| POST/GET | `/api/v1/me/data-jobs[/{id}]` | traveler | self | default |
| GET | `/api/v1/locations/search` | traveler | — | search |
| POST/GET | `/api/v1/trips` | traveler | user_id | default |
| GET/PATCH/DELETE | `/api/v1/trips/{id}` | traveler, PATCH needs `If-Match` | user_id | default |
| POST | `/api/v1/trips/{id}/assessments` | traveler, `Idempotency-Key` | user_id | assessment |
| GET | `/api/v1/trips/{id}/assessments` | traveler | user_id | default |
| POST | `/api/v1/trips/{id}/apply-route` | traveler, `If-Match` | user_id + recommendation.trip_id | assessment |
| GET | `/api/v1/runs/{id}` | traveler | requests.user_id | default |
| GET | `/api/v1/runs/{id}/events` (SSE) | traveler | requests.user_id, ≤ 3 streams/user | default |
| POST | `/api/v1/runs/{id}/cancel` / `/resume` | traveler | requests.user_id | default / assessment |
| GET | `/api/v1/recommendations/{id}` | traveler | user_scope pseudonym + trip owner | default |
| GET | `/api/v1/safety/events` | traveler | — (bbox ≤ 10°) | search |
| GET/POST | `/api/v1/conversations`, `/{id}/messages` | traveler | user_id | default / assessment |
| POST | `/api/v1/feedback` | traveler | recommendation owner | default |
| POST/DELETE | `/api/v1/alert-subscriptions[/{id}]` | traveler + consent ALERT_NOTIFICATION | user_scope | default |
| GET | `/api/v1/emergency/contacts` | traveler | — | default |
| GET | `/api/v1/emergency/nearby` | traveler + consent LOCATION_ONCE | — (coords rounded 4 dp) | search |

## 5. Decisions

- **Resume = new run**: after `NEEDS_INPUT` the client fixes the trip (PATCH) then `POST /runs/{id}/resume`; the new
  request links via `supersedes_request_id`/conversation; old audit stays intact.
- **`recommendation` ownership double-lock**: pseudonym check at คน 8 plus trip ownership here.
- **Contract violations are 502, not 503**: retryable dependency outages fall back to the last persisted row state on
  polls; an off-contract payload is surfaced immediately because retrying will not fix it.
- **Nearby lookups round coordinates to 4 dp (~11 m)**: enough for POI search, never the exact fix; the consent id is
  audited, the coordinates are not.
- **Rate limiter fails open** when Redis is down (logged) — availability of the safety product over strictness; the
  agent budget still caps cost.

## 6. Idempotency / state transitions

```text
POST assessments ──► key? ──yes──► lookup sha256(user:key) ──found──► fingerprint equal? ──yes──► 202 same RunRef
                                     │                                  └──no──► 409 IDEMPOTENCY_CONFLICT
                                     └──not found──► INSERT travel.requests(CREATED) ──► agent POST /runs
                                                       ├─ ok  ──► QUEUED (RunRef)
                                                       └─ err ──► FAILED(error_code) ──► 503/504/4xx envelope
CREATED → QUEUED → RUNNING → {NEEDS_INPUT, COMPLETED, PARTIAL, FAILED, CANCELLED}
NEEDS_INPUT → CANCELLED (resume creates a new request)      terminal states never change
```

## 7. Downstream budgets

| Dependency | Timeout | Retries | Notes |
| --- | --- | --- | --- |
| agent submit | 5 s | 0 (non-idempotent) | 202 or fail fast |
| agent poll/cancel | 4 s | 1 (idempotent GET) | poll falls back to persisted row on outage |
| external-data | 8 s | 1 | geocode/disasters/places |
| recommendation | 6 s | 1 (GET only) | feedback/subscribe non-idempotent |
| OIDC JWKS | 4 s | 1 | cached 600 s |

## 8. Keycloak (no secrets)

Realm `smart-travel` (`infra/keycloak/smart-travel-realm.json`): realm roles `traveler`, `safety-reviewer`, `team-lead`;
client `smart-travel-web` (confidential, Auth.js) with default scope `smart-travel-api` that adds audience
`smart-travel-api`; API accepts issuers `OIDC_ISSUER` (browser) and `OIDC_INTERNAL_ISSUER` (docker network) and reads
JWKS from `OIDC_JWKS_URL` or `<internal issuer>/protocol/openid-connect/certs`.

## 9. Sample sanitized trace

```text
POST /api/v1/trips/8f…/assessments  Idempotency-Key: k-1
  X-Correlation-ID: 3efbc949-…  →  INSERT travel.requests(id=be75…, status=CREATED)
  → agent POST /internal/v1/runs (X-Correlation-ID propagated, bearer SERVICE_AUTH_TOKEN) → 202 QUEUED
  log: {"event":"http_request","route":"/api/v1/trips/{trip_id}/assessments","status":202,"correlation_id":"3efbc949-…"}
GET /api/v1/runs/be75…/events  → id:1 run.accepted → id:2 run.progress FETCHING_EXTERNAL_DATA → id:3 run.completed
```

## 10. Tests

`uv run python -m pytest -q` → 24 passed; `ruff check`, `mypy --strict` clean (28 source files).

## 13. Problems

| Problem | Resolution |
| --- | --- |
| Windows Application Control blocked `.venv/Scripts/pytest.exe` and `mypy.exe` | run via `uv run python -m pytest` / `python -m mypy` (documented in Makefile) |
| JWKS refresh gate skipped the first rotation (fetched < 5 s earlier) | separate anti-storm timer for unknown-kid refreshes |
| Idempotent replay added a duplicate user message | `StartResult.replayed` flag; message only on first submission |
| Malformed agent payload was swallowed as a retryable outage | contract violations carry `details.reason=contract_violation` → 502 surfaced |
| Keycloak 26 access tokens had no `sub` (realm import lacked the built-in `basic` scope) → "token verification failed" | realm import defines `basic/profile/email/roles`; verifier requires `sub` (kept strict) |
| `cors_allowed_origins: list[str]` made pydantic-settings JSON-decode the env value | comma-separated string + `cors_origins` property |
| Stream consumer `XREADGROUP block=5000` > redis socket timeout → TimeoutError loop | block 2 s |
| Web and API needed different issuer URLs (browser vs docker network) | API accepts both issuers; JWKS read from the internal one |

## 15. Limitations

| Limitation | Impact | Next |
| --- | --- | --- |
| Data export covers identity/travel only | recommendation/feedback export must be requested from คน 8 by `user_scope` | add cross-service export job in integration phase |
| Purge job for soft-deleted rows not scheduled | rows stay until an operator runs the purge | Arq/cron job in ops |
| In-app alert channel not bridged to SSE | web must poll `/runs` after `alert.reassessment.requested`; push/email work | expose `/api/v1/alerts/stream` once คน 8 publishes the pseudonym mapping |
| Rate limiter is a fixed window | burst at window edges up to 2× | token bucket if load test shows abuse |

## 16. Handoff

| Recipient | Ready | Must do |
| --- | --- | --- |
| คน 1 (Web) | `packages/contracts/openapi/public-api.yaml`, SSE event shapes, ETag/If-Match, Idempotency-Key, 404-for-foreign semantics | send `Authorization: Bearer <Keycloak access token>`; retry only on `retryable=true`; honour `Retry-After` |
| คน 3 | `TravelRequest` with `user_scope_hash`, `conversation_id`, `supersedes_request_id` | keep `RunState`/events contract-valid |
| คน 8 | `user_scope` = `sha256(USER_SCOPE_SALT:user_id)[:40]` in every call | ownership checks by pseudonym |
| Team Lead | realm import, `USER_SCOPE_SALT`, `EMERGENCY_PROFILE_ENCRYPTION_KEY` in `.env` | rotate keys via `EMERGENCY_PROFILE_KEY_VERSION` |

ผู้จัดทำ: คน 2 (simulated) · วันที่: 2026-09-17

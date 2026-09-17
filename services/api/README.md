# api (คน 2)

The public trust boundary. Everything the browser can reach goes through here: OIDC/JWT verification, ownership,
validation, rate limits, idempotent assessment runs, an SSE bridge to the agent's progress stream, and validated
facades over the internal services. The API never computes risk, never calls a provider directly (geocode/places/
disasters go through external-data) and never edits a decision from คน 7/8.

```text
browser (Auth.js, Keycloak token) ──► /api/v1/*  (bearer JWT: signature/iss/aud/exp/nbf + realm role "traveler")
   │  ownership = internal user_id (Keycloak sub -> identity.user_profiles), enforced in every repository query
   ├─ /me, /me/emergency-profile (AES-256-GCM envelope, consent-gated), /consents, /me/data-jobs (export/delete)
   ├─ /locations/search ──────────────► external-data  /internal/v1/geocode/search
   ├─ /trips (ETag=revision, If-Match), /trips/{id}/assessments (Idempotency-Key) ──► agent /internal/v1/runs
   ├─ /runs/{id} (poll, state machine), /runs/{id}/events (SSE from Redis stream), cancel, resume
   ├─ /recommendations/{id}, /feedback, /alert-subscriptions ──► recommendation (pseudonymous user_scope)
   ├─ /trips/{id}/apply-route (new revision + new assessment, AVOID needs acknowledgement)
   ├─ /safety/events (bbox ≤ 10°) ────► external-data /internal/v1/disasters/query
   ├─ /emergency/contacts ─────────────► recommendation verified directory
   └─ /emergency/nearby (LOCATION_ONCE consent, 4-dp coordinates) ► external-data /internal/v1/places/nearby
background: consumes sta:{env}:stream:alert.reassessment.requested -> system reassessment -> alerts/evaluate
```

## Rules enforced here

| Rule | Where |
| --- | --- |
| No unsigned/unknown-alg tokens; JWKS cached (TTL) with one bounded refresh per unknown `kid` | `auth/jwt.py` |
| Wrong role → 403, wrong token → 401, other user's resource → 404 (no existence leak) | `auth/deps.py`, repositories |
| Trip validation: confirmed `LocationRef`, IANA timezone, departure window, lon/lat range | `domain/rules.py` |
| Optimistic concurrency: `ETag: W/"<revision>"`, `PATCH`/`apply-route` require `If-Match` → 412 | `api/v1/trips.py` |
| Idempotency: `sha256(user, key, payload)`; same key+payload replays, different payload → 409 `IDEMPOTENCY_CONFLICT` | `application/assessments.py` |
| Request state machine (`CREATED→QUEUED→RUNNING→…`), regressions ignored + logged | `domain/rules.py` |
| Request row committed **before** the agent call; agent 4xx/5xx → row `FAILED` + stable envelope | `application/assessments.py` |
| SSE: owner check first, per-user connection cap, heartbeat, `Last-Event-ID`, closes on terminal, contract re-validation | `application/sse.py` |
| Downstream responses re-validated against `sta_contracts`; violation → 502 `DEPENDENCY_UNAVAILABLE` | `clients/downstream.py` |
| Rate limits per user/endpoint class (Redis fixed window, `Retry-After`) | `middleware/rate_limit.py` |
| Emergency profile encrypted with a key from the environment, bound to the owner (AAD), response minimized | `domain/crypto.py`, `api/v1/me.py` |
| Logs: no tokens (fingerprint only), no coordinates, no medical text, no user questions | sta_common redaction + tests |
| Security headers, exact-origin CORS, 256 KB body cap | `main.py` |

## Persistence (schemas `identity`, `travel`, role `sta_api`)

`identity.user_profiles`, `identity.consents` (versioned, revocable), `identity.emergency_profiles` (ciphertext +
key version), `identity.audit_log` (ids only), `identity.data_jobs`; `travel.trips` (revision, soft delete),
`travel.requests` (idempotency hash + fingerprint, status, error code), `travel.conversations`,
`travel.conversation_messages`. Migration: `migrations/versions/0001_identity_travel_baseline.py`
(`uv run alembic upgrade head`).

## Configuration

`OIDC_ISSUER`, `OIDC_INTERNAL_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL` (optional), `OIDC_REQUIRED_ROLE` (traveler),
`AGENT_SERVICE_URL`, `EXTERNAL_DATA_SERVICE_URL`, `RECOMMENDATION_SERVICE_URL`, `EMERGENCY_PROFILE_ENCRYPTION_KEY`,
`USER_SCOPE_SALT`, `CORS_ALLOWED_ORIGINS`, `TRUSTED_PROXY_COUNT`, `RATE_LIMIT_*_PER_MINUTE`,
`MAX_SSE_CONNECTIONS_PER_USER`, `POSTGRES_API_PASSWORD`, `REDIS_URL`, `SERVICE_AUTH_TOKEN`.

## Contract

`scripts/export_openapi.py` writes `packages/contracts/openapi/public-api.yaml`; `--check` (and the test suite) fails
when the snapshot drifts from the implementation.

## Tests

`uv run python -m pytest -q` → 24 passed: auth negative matrix (expired, wrong iss/aud, wrong key, alg=none, missing
role), JWKS rotation, ownership, encrypted emergency profile, data jobs, trip validation/ETag/soft delete,
idempotency (replay + conflict), run state sync, agent outage, malformed agent payload, SSE (order, ids, reconnect,
heartbeat, connection cap, terminal close, no coordinates), cancel/state machine, needs-input → resume, apply-route,
locations, safety bbox, emergency consent + coordinate coarsening, feedback/subscriptions/consent cascade, follow-up,
rate limit, log redaction, body cap, health, OpenAPI snapshot + endpoint matrix.

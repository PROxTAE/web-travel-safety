# recommendation (คน 8)

Final delivery layer: builds the `RecommendationResponse` the web app renders, attaches **verified** emergency
contacts, persists immutable recommendations, runs governed feedback, and delivers consent-based live alerts.

| Area | Where | Rules |
| --- | --- | --- |
| Builder | `app/builders/response.py` | order action → why → routes → emergency → freshness/degraded/limitations → sources/versions; `CHANGE_ROUTE` needs a usable selected route, `AVOID` never shows a closed primary, citations must exist; `status=PARTIAL` when degraded/stale; `expires_at = min(evidence expiry, 1 h)`; immutable, `supersedes_recommendation_id` |
| Emergency directory | `emergency-directory/sources.yaml`, `app/directory/resolver.py` | official/UN sources only, `VERIFIED` records only, review-overdue guard, subdivision scoping, i18n labels, E.164 when applicable; `python -m app.cli.verify_emergency_directory` re-checks sources |
| Feedback | `app/domain/feedback.py` | control chars removed, PII redacted, ≤1000 chars, pseudonymous owner, `UNSAFE/INCORRECT/ROUTE_ISSUE` → safety review queue; export is offline-only (no live learning) |
| Alerts | `app/domain/alerts.py`, `alert_service.py` | meaningful-change rules v1.0.0 (action/risk escalation, new official alert, safer option, freshness recovery), event-hash dedup via unique constraint, per-severity cooldown, escalation never suppressed, severity threshold per subscription |
| Channels | `app/notifications/channels.py` | in-app (Redis pub/sub + 50-item inbox) baseline; Web Push (VAPID) and email (SMTP/Mailpit) only when configured; minimal payload (no coordinates/medical) |
| Worker | `app/workers/main.py` (Arq) | every 15 min publishes `alert.reassessment.requested` per trip with active subscriptions to `sta:{env}:stream:…`; API consumes it |

API (`/internal/v1`, service token): `POST/GET recommendations`, `POST feedback`, `GET feedback/review-queue`,
`POST/DELETE subscriptions`, `POST subscriptions/revoke-consent`, `POST alerts/evaluate`, `GET emergency/contacts`.

```bash
uv run pytest -q                                     # 10 tests (builder, directory, feedback, alert rules, API flow)
uv run python -m app.cli.verify_emergency_directory  # live source reachability + review dates
docker compose up -d recommendation recommendation-worker
```

Directory status (2026-09-17): TH 191/1155/1669/1784/199 and JP 110/119 VERIFIED (sources HTTP 200);
US 911 PENDING_VERIFICATION (911.gov blocks automated fetch) — never served until a human confirms.

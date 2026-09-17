# Smart Travel Assistant

Agentic travel-safety system (`DL-07: Agentic AI System II`). A traveler enters a trip; the system pulls
live weather, routing, transit and disaster data from real providers, aligns it to the route corridor and
travel time, scores risk with a local model plus deterministic safety overrides, retrieves official
emergency guidance with citations, locks one of four actions (`NORMAL | CHANGE_ROUTE | DELAY | AVOID`)
with a versioned policy, and only then lets an LLM explain the result.

Team plans live in [`IMPLEMENTATION_PLANS/`](IMPLEMENTATION_PLANS/README.md); the visual target is
[`assets/ui-screens/`](assets/README.md). Per-module completion reports are in [`docs/handoffs/`](docs/handoffs/).

## Architecture

```text
Browser ─▶ apps/web (Next.js 16, HeroUI 3, MapLibre)
              │  REST + SSE, OIDC cookie session
              ▼
        services/api (FastAPI, JWT/JWKS, trips, idempotency, SSE bridge)   ← public trust boundary
              │ /internal/v1 (service token)
              ▼
        services/agent (LangGraph finite graph, Postgres checkpointer, Redis progress)
              ├─▶ services/external-data   Open-Meteo · ORS · USGS · GDACS · EONET · GTFS-RT · Amadeus
              ├─▶ services/data-integration  PostGIS corridor · dedup · conflicts · features · snapshot
              ├─▶ services/risk-knowledge    sklearn risk model · overrides · RAG (Qdrant+BM25) · routes
              ├─▶ services/decision-engine   YAML decision table · locked action · OpenAI explanation · validator
              └─▶ services/recommendation    final response · emergency directory · alerts · feedback

PostgreSQL 16 + PostGIS · Redis · Qdrant · Keycloak · OpenTelemetry · Prometheus · Grafana · Mailpit
```

| Service | Port | Owner | Handoff |
| --- | ---: | --- | --- |
| web | 3000 | คน 1 | [M01](docs/handoffs/M01-web-app.md) |
| api | 8000 | คน 2 | [M02](docs/handoffs/M02-api-backend.md) |
| agent | 8001 | คน 3 | [M03](docs/handoffs/M03-travel-agent.md) |
| external-data | 8002 | คน 4 | [M04](docs/handoffs/M04-external-data.md) |
| data-integration | 8003 | คน 5 | [M05](docs/handoffs/M05-data-integration.md) |
| risk-knowledge | 8004 | คน 6 | [M06](docs/handoffs/M06-risk-knowledge.md) |
| decision-engine | 8005 | คน 7 | [M07](docs/handoffs/M07-decision-llm.md) |
| recommendation | 8006 | คน 8 | [M08](docs/handoffs/M08-recommendation-feedback.md) |

## Quick start (Docker)

```bash
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
bash ops/scripts/gen-secrets.sh   # fills every required secret; provider keys stay optional
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
docker compose -f compose.yaml -f compose.dev.yaml build
docker compose -f compose.yaml -f compose.dev.yaml up -d postgres redis qdrant keycloak
make migrate
docker compose run --rm risk-knowledge python -m app.cli.verify_model            # CANDIDATE model => rule baseline
docker compose run --rm risk-knowledge python -m app.cli.index_knowledge         # Qdrant collection + alias
docker compose run --rm recommendation python -m app.cli.verify_emergency_directory
docker compose -f compose.yaml -f compose.dev.yaml up -d
docker compose ps
```

Open <http://localhost:3000>. Create a traveler in Keycloak (<http://localhost:8080>, realm `smart-travel`, realm role
`traveler`; the realm logs in by e-mail). The last full run with evidence is in
[`docs/acceptance/2026-09-17-8a8100c.md`](docs/acceptance/2026-09-17-8a8100c.md); the story of how the system was
built, person by person, is in [`docs/IMPLEMENTATION_RECORD.md`](docs/IMPLEMENTATION_RECORD.md).

Optional capabilities degrade honestly when a credential is missing: no `ORS_API_KEY` → road routing and
nearby POIs report `UNAVAILABLE`; no `OPENAI_API_KEY` → deterministic explanation template; no Amadeus
production credentials → flight status `UNAVAILABLE`.

Operations: `ops/scripts/backup.sh` (pg_dump + Qdrant snapshot + manifest) and `ops/scripts/restore.sh <dir> --teardown`
(restore into the throwaway `sta-restore` project and smoke-read it) — see `ops/runbooks/backup-restore.md`;
`make scan-images` runs Docker Scout on every image (the CI gate is trivy, CRITICAL, fixable).

## Local development without Docker

Each Python package/service is a `uv` project (Python 3.12):

```bash
cd services/external-data && uv sync --all-extras && uv run pytest -q
cd apps/web && pnpm install && pnpm dev
```

`make lint typecheck test-unit test-contract` runs the same checks as CI. On Windows with Application Control, run
tools as modules (`uv run python -m pytest`, `python -m mypy`) and Playwright with `E2E_BROWSER_CHANNEL=chrome`.

## Rules that are enforced, not just written

- No direct commits to `main`; every change is a short-lived `<type>/<module>-<slug>` branch + PR.
- No runtime mocks: CI greps for mock switches; fixtures are only allowed under `tests/` with provenance.
- Every safety-relevant value carries `source`, `observed_at/fetched_at/expires_at`, `quality` and a version.
- The LLM never chooses or changes the action; it only explains a locked decision and is post-validated.
- Official closures/warnings can never be weakened by the model, the LLM, or a downstream builder.

See [`00_GIT_DOCKER_DELIVERY_RULES.md`](IMPLEMENTATION_PLANS/00_GIT_DOCKER_DELIVERY_RULES.md) and the
[acceptance runbook](IMPLEMENTATION_PLANS/09_INTEGRATION_ACCEPTANCE_RUNBOOK.md).

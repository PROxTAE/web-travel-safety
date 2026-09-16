# external-data (คน 4)

Provider adapter service. Calls real providers, normalizes to the shared canonical contract, attaches
provenance/freshness/quality, and never decides risk.

| Capability | Provider | Endpoint | Credential | Status without credential |
| --- | --- | --- | --- | --- |
| Geocoding | Open-Meteo Geocoding | `GET /v1/search` | none | — |
| Weather (hourly along route + current) | Open-Meteo Forecast | `GET /v1/forecast` (multi-location) | none | — |
| Road routing + alternatives + avoid polygons | openrouteservice | `POST /v2/directions/{profile}/geojson` | `ORS_API_KEY` | `UNAVAILABLE`; geometry falls back to a flagged great-circle approximation |
| Nearby police/hospital/embassy POIs | openrouteservice POIs | `POST /pois` (categories resolved via `{"request":"list"}`) | `ORS_API_KEY` | `UNSUPPORTED_COVERAGE` |
| Earthquakes | USGS FDSN | `GET /fdsnws/event/1/query` | none | — |
| Multi-hazard | GDACS | `GET /api/events/geteventlist/SEARCH` | none | — |
| NRT natural events | NASA EONET v3 | `GET /events` | none | — |
| Transit realtime | GTFS-RT feeds in `config/providers.yaml` (MBTA registered) | protobuf | per feed | `OUTSIDE_COVERAGE` |
| Flights | Amadeus production | OAuth + `GET /v2/schedule/flights` | client id/secret | `UNAVAILABLE` |

## Run

```bash
uv sync --all-extras
uv run pytest -q                       # 25 deterministic tests (real sanitized fixtures)
APP_ENV=test uv run python -m app.cli.canary            # live canary (quota-bearing)
APP_ENV=test uv run python -m app.cli.canary --capture tests/fixtures/real-sanitized
uv run uvicorn app.main:app --port 8002
```

Docker: `docker compose build external-data && docker compose run --rm external-data alembic upgrade head`.

## Internal API (`/internal/v1`, bearer `SERVICE_AUTH_TOKEN`)

`POST geocode/search`, `POST weather/query`, `POST routes/query`, `POST transport/query`,
`POST disasters/query`, `POST places/nearby`, `POST context/query`, `GET providers/health`.
`context/query` fans out weather/routes/transport/disasters with bounded concurrency and a 20 s deadline;
failures land in `degraded_services[]` / `unavailable_capabilities[]`.

## Reliability

Per-provider circuit breaker (5 failures → open 60 s → half-open probe), retry only on 408/429/5xx with
`Retry-After`, Redis cache keyed by provider+schema+query hash with TTLs from the shared freshness table,
stampede lock, short negative cache. Raw provider bodies are never persisted; `provider.fetch_log` keeps hashes.

# sta-common

Shared runtime primitives for the seven Python services. Scope is deliberately narrow
(00_SHARED_PROJECT_CONTEXT §6: "shared observability/error only"):

| Module | Purpose |
| --- | --- |
| `settings` | `BaseServiceSettings` (env, ports, contract version, service token, timeouts) |
| `logging` | structlog JSON with `request_id/correlation_id/trace_id` and automatic PII redaction |
| `redaction` | token/email/phone/medical redaction; coordinates coarsened to ~1 km |
| `context` | contextvar request context + outbound header propagation |
| `middleware` | request IDs, access log, Prometheus metrics, body-size limit |
| `errors` | `AppError`, stable `ErrorCode`, standard error envelope handlers |
| `envelope` | success/list envelopes with `meta` |
| `health` | `/health/live`, `/health/ready` with bounded dependency checks |
| `metrics` | shared Prometheus series names used by Grafana dashboards |
| `http` | `ResilientClient`: timeouts, Retry-After aware retry on 408/429/5xx, deadline, header propagation |
| `internal_auth` | constant-time shared-token check for `/internal/v1` |
| `db` / `cache` | SQLAlchemy async engine with schema search_path; Redis key prefix + TTL-only writes |
| `timeutil` | UTC/ISO helpers |
| `app` | `create_app()` factory wiring all of the above |

```bash
uv sync --all-extras
uv run pytest -q
```

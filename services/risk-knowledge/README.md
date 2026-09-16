# risk-knowledge (คน 6)

Three capabilities in one service, all versioned and auditable:

| Part | What | Where |
| --- | --- | --- |
| A — Local risk model | calibrated sklearn classifier trained on **real historical data** (day-1 forecasts → observed hazards), deterministic rule baseline when no ACTIVE artifact, safety overrides that can only raise risk | `app/risk/`, `training/`, `config/{thresholds,overrides,model_acceptance}.yaml` |
| B — Disaster RAG | approved official sources (`knowledge/sources.yaml`), section-aware chunking, multilingual e5 embeddings in versioned Qdrant collections + BM25, RRF fusion, expiry/geography/hazard filters, resolvable citations | `app/knowledge/`, `app/cli/index_knowledge.py` |
| C — Route evaluation | hard closure constraints, versioned cost, honest labels (ORIGINAL/RECOMMENDED/FASTEST/LOWEST_RISK), deterministic ties, trade-off text without LLM | `app/routes/ranking.py` |

API (`/internal/v1`, service token): `POST risk/assess`, `POST knowledge/retrieve`, `POST routes/evaluate`,
`POST evidence/package`, `GET models/current`, `GET knowledge/status`.

## Training (real data)

```bash
# 1) data-integration in-memory (or the compose service) provides the exact online feature pipeline
cd ../data-integration && APP_ENV=test uv run uvicorn app.main:app --port 18003 &
# 2) build dataset from Open-Meteo Previous Runs (features) + Archive (labels) + GDACS + USGS
cd ../risk-knowledge && uv run python -m training.build_dataset --day-step 2
# 3) train candidates, compare with the rule baseline, write model card, promote if acceptance passes
uv run python -m training.train --version 1.0.0 --promote --approver team-lead
uv run python -m app.cli.verify_model
```

Docker: `docker compose --profile training run --rm risk-training` (uses `DATA_INTEGRATION_SERVICE_URL`).

## Knowledge

```bash
uv run python -m app.cli.index_knowledge             # ingest → build collection → golden eval → release alias
uv run python -m app.cli.index_knowledge --rollback sta_knowledge_v2026_09_1
```

Tests: `uv run pytest -q` (22: overrides, Hypothesis monotonic-safety properties, ranking, chunking, filters, API).

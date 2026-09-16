# syntax=docker/dockerfile:1.7
FROM python:3.12.8-slim AS builder
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /bin/uv
WORKDIR /build
COPY packages/python-common /build/packages/python-common
COPY packages/contracts /build/packages/contracts
COPY services/risk-knowledge/pyproject.toml services/risk-knowledge/uv.lock /build/services/risk-knowledge/
WORKDIR /build/services/risk-knowledge
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --no-install-project
COPY services/risk-knowledge /build/services/risk-knowledge
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable
# Pre-bake the embedding model so runtime never downloads (pinned by name; HF cache lives in the image layer).
ENV PATH="/opt/venv/bin:$PATH" HF_HOME=/opt/hf
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small', device='cpu')"

FROM python:3.12.8-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" TZ=UTC HF_HOME=/opt/hf HF_HUB_OFFLINE=1 \
    FEATURE_SCHEMA_PATH=/app/config/feature_schema.yaml RISK_MODEL_ARTIFACT_DIR=/app/artifacts KNOWLEDGE_CACHE_DIR=/app/knowledge/cache
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=app:app /opt/hf /opt/hf
COPY --chown=app:app services/risk-knowledge/app /app/app
COPY --chown=app:app services/risk-knowledge/training /app/training
COPY --chown=app:app services/risk-knowledge/config /app/config
COPY --chown=app:app services/risk-knowledge/knowledge/sources.yaml /app/knowledge/sources.yaml
COPY --chown=app:app services/risk-knowledge/migrations /app/migrations
COPY --chown=app:app services/risk-knowledge/alembic.ini /app/alembic.ini
COPY --chown=app:app packages/python-common/sta_common/healthcheck_script.py /app/healthcheck.py
RUN mkdir -p /app/artifacts /app/knowledge/cache /app/data && chown -R app:app /app/artifacts /app/knowledge /app/data
USER app
EXPOSE 8004
HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=12 CMD ["python", "/app/healthcheck.py"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004", "--proxy-headers"]

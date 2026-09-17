# syntax=docker/dockerfile:1.7
# Build context = repository root (see compose.yaml) so shared packages can be installed.
FROM python:3.12.14-slim AS builder
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /bin/uv
WORKDIR /build
COPY packages/python-common /build/packages/python-common
COPY packages/contracts /build/packages/contracts
COPY services/agent/pyproject.toml services/agent/uv.lock /build/services/agent/
WORKDIR /build/services/agent
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --no-install-project
COPY services/agent /build/services/agent
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" TZ=UTC
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/agent/app /app/app
COPY --chown=app:app services/agent/migrations /app/migrations
COPY --chown=app:app services/agent/alembic.ini /app/alembic.ini
COPY --chown=app:app packages/python-common/sta_common/healthcheck_script.py /app/healthcheck.py
USER app
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=12 CMD ["python", "/app/healthcheck.py"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers"]

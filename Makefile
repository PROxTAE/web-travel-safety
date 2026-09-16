# Convenience wrappers. Direct docker/uv/pnpm commands must keep working without make.
SHELL := bash
COMPOSE := docker compose -f compose.yaml -f compose.dev.yaml
PY_SERVICES := api agent external-data data-integration risk-knowledge decision-engine recommendation
PY_PACKAGES := packages/python-common packages/contracts

.PHONY: help bootstrap compose-validate build up down ps logs migrate \
        lint typecheck test-unit test-contract test-integration test-e2e \
        contracts-generate contracts-check secret-scan

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Install all local toolchains (uv sync per service, pnpm install)
	@for d in $(PY_PACKAGES) $(addprefix services/,$(PY_SERVICES)); do echo "== $$d"; (cd $$d && uv sync --frozen --all-extras) || exit 1; done
	pnpm install --frozen-lockfile

compose-validate: ## Validate compose topology
	$(COMPOSE) config --quiet
	docker compose -f compose.yaml --profile observability --profile notifications --profile training config --quiet

build: ## Build all images
	$(COMPOSE) build --pull

up: ## Start core + app
	$(COMPOSE) up -d

down: ## Stop (does NOT remove volumes)
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200 $(S)

migrate: ## Apply every service migration head
	@for s in $(PY_SERVICES); do echo "== alembic upgrade head ($$s)"; $(COMPOSE) run --rm $$s alembic upgrade head || exit 1; done

lint: ## ruff + eslint
	@for d in $(PY_PACKAGES) $(addprefix services/,$(PY_SERVICES)); do echo "== $$d"; (cd $$d && uv run ruff check . && uv run ruff format --check .) || exit 1; done
	pnpm --filter web lint

typecheck: ## mypy + tsc
	@for d in $(addprefix services/,$(PY_SERVICES)); do echo "== $$d"; (cd $$d && uv run mypy app) || exit 1; done
	pnpm --filter web typecheck

test-unit: ## deterministic unit tests (no network, no docker)
	@for d in $(PY_PACKAGES) $(addprefix services/,$(PY_SERVICES)); do echo "== $$d"; (cd $$d && uv run pytest -q -m "not integration and not canary") || exit 1; done
	pnpm --filter web test

test-contract: ## schema/example/generated-client contract tests
	cd packages/contracts && uv run pytest -q tests
	cd tests/contract && uv run pytest -q

test-integration: ## requires running compose stack
	cd tests/integration && uv run pytest -q -m integration

test-e2e: ## Playwright against running stack
	pnpm --filter web test:e2e

contracts-generate: ## regenerate JSON Schema + TypeScript from the Pydantic source of truth
	cd packages/contracts && uv run python scripts/generate.py

contracts-check: contracts-generate ## fail if generation changes the tree
	git diff --exit-code -- packages/contracts/jsonschema packages/contracts/generated

secret-scan: ## crude local secret / runtime-mock scan (CI runs gitleaks too)
	@! rg -n --hidden -g '!.git' -g '!node_modules' -g '!*.lock' -g '!Makefile' "(sk-[A-Za-z0-9_-]{16,}|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY)" . || (echo "secret-like string found" && exit 1)
	@! rg -n -g '!node_modules' -g '!*.md' "(USE_MOCK|mockMode|fakeRecommendation|hardcodedWeather|sampleCurrentData)" apps services || (echo "runtime mock switch found" && exit 1)

# Agnos Proxy - developer ergonomics.
# Thin wrappers over the commands documented in CONTRIBUTING.md so `make <tab>`
# is discoverable. `make` with no target prints this help.

.DEFAULT_GOAL := help

.PHONY: help up down logs dev test test-integration lint typecheck build-dashboard sanity

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## Start the local infra stack (docker compose up -d)
	docker compose up -d

down: ## Stop the local infra stack (docker compose down)
	docker compose down

logs: ## Follow local stack logs (docker compose logs -f)
	docker compose logs -f

dev: ## Bring up the full local dev stack (infra + dashboard + gateway)
	./scripts/start_local.sh

test: ## Run the fast unit suite (no live/integration, $0 on echo)
	poetry run pytest -m "not live and not integration"

test-integration: ## Run the integration BVT suite (needs a running gateway)
	poetry run pytest -m integration

lint: ## Lint Python (ruff, the CI gate)
	poetry run ruff check .

typecheck: ## Type-check (scoped mypy + frontend tsc --noEmit)
	poetry run mypy gateway/secrets gateway/bifrost gateway/governance gateway/db gateway/engines
	cd frontend && npm run typecheck

build-dashboard: ## Build the React dashboard (frontend/dist)
	cd frontend && npm run build

sanity: lint typecheck test build-dashboard ## Run all CI-style gates locally (lint + types + tests + dashboard build)
	@echo "sanity: all checks passed"

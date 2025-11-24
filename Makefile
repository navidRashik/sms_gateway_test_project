.PHONY: help dev dev-build down clean logs logs-gateway logs-providers test install

help: ## Show this help message
	@echo "SMS Gateway Monorepo - Available Commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

dev: ## Start all services
	docker-compose up

dev-build: ## Rebuild and start all services
	docker-compose up --build

down: ## Stop all services
	docker-compose down

clean: ## Stop services and remove volumes
	docker-compose down -v
	@echo "Cleaned up containers and volumes"

logs: ## View logs from all services
	docker-compose logs -f

logs-gateway: ## View gateway service logs
	docker-compose logs -f gateway

logs-providers: ## View provider service logs
	docker-compose logs -f provider1 provider2 provider3

logs-workers: ## View worker service logs
	docker-compose logs -f worker scheduler

test: ## Run gateway tests
	cd apps/gateway && uv run pytest

test-gateway: ## Run gateway tests with coverage
	cd apps/gateway && uv run pytest --cov=src --cov-report=html

install: ## Install all dependencies
	@echo "Installing gateway dependencies..."
	cd apps/gateway && uv sync
	@echo "Installing provider dependencies..."
	cd apps/providers && npm install
	@echo "Done!"

install-gateway: ## Install gateway dependencies only
	cd apps/gateway && uv sync

install-providers: ## Install provider dependencies only
	cd apps/providers && npm install

shell-gateway: ## Open shell in gateway container
	docker-compose exec gateway bash

shell-provider: ## Open shell in provider1 container
	docker-compose exec provider1 sh

db-migrate: ## Run database migrations
	cd apps/gateway && uv run alembic upgrade head

db-rollback: ## Rollback last migration
	cd apps/gateway && uv run alembic downgrade -1

redis-cli: ## Open Redis CLI
	docker-compose exec redis redis-cli

format: ## Format code (gateway)
	cd apps/gateway && uv run black src/ tests/
	cd apps/gateway && uv run isort src/ tests/

lint: ## Lint code (gateway)
	cd apps/gateway && uv run flake8 src/ tests/

check: format lint test ## Format, lint, and test

restart: ## Restart all services
	docker-compose restart

restart-gateway: ## Restart gateway service
	docker-compose restart gateway

restart-providers: ## Restart provider services
	docker-compose restart provider1 provider2 provider3

ps: ## Show running services
	docker-compose ps

stats: ## Show container resource usage
	docker stats

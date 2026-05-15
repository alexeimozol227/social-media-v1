.PHONY: help install install-backend install-web dev dev-backend dev-web migrate test test-backend test-web lint lint-backend lint-web typecheck typecheck-backend typecheck-web build build-web docker-up docker-down docker-logs

help:
	@echo "social-media-v1 — make targets"
	@echo ""
	@echo "  make install         Install backend (uv) + frontend (pnpm) dependencies"
	@echo "  make docker-up       Start Postgres + Redis + MailHog"
	@echo "  make docker-down     Stop the dev stack"
	@echo "  make migrate         Run Alembic migrations against Postgres"
	@echo "  make dev             docker-up + uvicorn + next dev (foreground)"
	@echo "  make dev-backend     Run only the FastAPI server"
	@echo "  make dev-web         Run only the Next.js dev server"
	@echo "  make test            Run pytest (backend) + frontend tests"
	@echo "  make lint            Run ruff (backend) + biome (web)"
	@echo "  make typecheck       Run mypy (backend) + tsc (web)"
	@echo "  make build           Build the web app"

install: install-backend install-web

install-backend:
	cd apps/backend && uv sync

install-web:
	cd apps/web && pnpm install

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

migrate:
	cd apps/backend && uv run alembic upgrade head

dev-backend:
	cd apps/backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	cd apps/web && pnpm dev

dev:
	$(MAKE) docker-up
	@echo ""
	@echo "Postgres at localhost:5432 / Redis at localhost:6379 / MailHog UI at http://localhost:8025"
	@echo ""
	@echo "Now run in two terminals:"
	@echo "  make dev-backend"
	@echo "  make dev-web"

test: test-backend
test-backend:
	cd apps/backend && uv run pytest -v
test-web:
	cd apps/web && pnpm typecheck

lint: lint-backend lint-web
lint-backend:
	cd apps/backend && uv run ruff check . && uv run ruff format --check . && uv run python tools/lint_set_local.py
	python3 scripts/check_timestamptz.py
lint-web:
	cd apps/web && pnpm lint && pnpm run i18n:audit

typecheck: typecheck-backend typecheck-web
typecheck-backend:
	cd apps/backend && uv run mypy app/
typecheck-web:
	cd apps/web && pnpm typecheck

build: build-web
build-web:
	cd apps/web && pnpm build

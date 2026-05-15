# social-media-v1

AI Operating System for Social Networks — платформа, где автономные AI-агенты ведут социальные каналы за пользователя.

## Документация

Вся проектная документация находится в [`docs/`](./docs/):

| # | Файл | Описание |
| --- | --- | --- |
| 01 | [01-product-vision.md](./docs/01-product-vision.md) | Продуктовое видение, 15 агентов (8 на MVP), Brand Memory, инварианты I1–I17 |
| 02 | [02-target-audience.md](./docs/02-target-audience.md) | ICP, персоны (Анна / Денис / Мария), JTBD, UC-1..UC-12 |
| 03 | [03-feature-scope.md](./docs/03-feature-scope.md) | MoSCoW MVP, EPIC A–M, 9 acceptance criteria |
| 04 | [04-architecture.md](./docs/04-architecture.md) | Архитектура, принципы П1–П13, decision records D26–D70 |
| 05 | [05-tech-stack.md](./docs/05-tech-stack.md) | Технологический стек: FastAPI, Postgres, Redis, Celery, Next.js |
| 06 | [06-roadmap.md](./docs/06-roadmap.md) | Roadmap: Фазы 0–6 (MVP) + post-MVP (v1.0 → v3.0) |
| 07 | [07-monetization.md](./docs/07-monetization.md) | Тарифы (Solo / Pro / Network), unit-economics |

## Монорепо

```
.
├── apps/
│   ├── backend/        FastAPI (Python 3.12, SQLAlchemy 2.0 async, Alembic, Postgres + Redis)
│   └── web/            Next.js 15 (React 19, Tailwind 4, next-intl)
├── docker-compose.yml  Postgres 16 + pgvector, PgBouncer (transaction pool), Redis 7, MailHog
├── Makefile            make install / dev / migrate / test / lint / typecheck / build
└── docs/               Проектная документация
```

## Быстрый старт

Требования: **Python 3.12+**, **Node.js 20+**, **uv**, **pnpm**, **Docker + Compose**.

```bash
# 1. Установить зависимости backend + web.
make install

# 2. Поднять Postgres + Redis + MailHog.
make docker-up

# 3. Скопировать .env и применить миграции.
cp apps/backend/.env.example apps/backend/.env
cp apps/web/.env.example apps/web/.env.local
make migrate

# 4. В двух терминалах:
make dev-backend     # http://localhost:8000 (FastAPI)
make dev-web         # http://localhost:3000 (Next.js)
```

| Сервис | URL |
| --- | --- |
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| OpenAPI / Swagger | http://localhost:8000/docs |
| MailHog UI | http://localhost:8025 |

## Тесты, линт, типы

```bash
make test         # pytest (backend)
make lint         # ruff + biome + SET LOCAL CI-линтер
make typecheck    # mypy --strict + tsc --noEmit
make build        # next build
```

## Текущий статус разработки

* **PR #1 (merged)** — проектная документация.
* **PR #2 (merged)** — Фаза 0 + Фаза 1 Спринт 1 «skeleton»: монорепо, лендинг, самописная auth (register / login / refresh / logout / me) с JWT (15 мин) + refresh-семьями в HttpOnly cookie + replay-детекцией, фундаментальный tenancy-слой (workspaces / workspace_members / brands), RLS-контекст через `SET LOCAL`, typed `AppError`, pytest auth + RLS, GitHub Actions CI.
* **PR #3–#10 (merged)** — email verification, MFA (TOTP), password reset, audit_events (partitioned + retention pg_cron), skill-инфраструктура (D68/D69/D70), event bus + WebSocket, Unleash feature flags + idempotency middleware, OpenTelemetry, multi-currency billing skeleton.
* **PR #11 (этот PR)** — RLS policies на бизнес-таблицы (workspaces / workspace_members / brands / refresh_tokens / idempotency_keys / invoices) + `app_user` роль с least-privilege grants + PgBouncer (`pool_mode=transaction`) в docker-compose + Postgres-only integration suite в CI (`backend-postgres`).

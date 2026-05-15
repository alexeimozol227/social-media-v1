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
| — | [plans/](./docs/plans/) | Планы по фазам / спринтам (один файл — один PR/спринт) |
| — | [reports/](./docs/reports/) | Простыми словами «что уже работает» по каждой фазе |

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

Требования: **Python 3.12+**, **Node.js 22.6+** (нужен встроенный TS-stripping для `i18n_audit.ts`), **uv**, **pnpm**, **Docker + Compose**.

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

### Pre-commit hooks

`pre-commit` гоняет на каждом коммите ровно те же чеки, что и CI:
ruff (lint + format), biome, `lint_set_local`, `check_timestamptz`,
`check_system_prompt_lang`, `validate_skills`, `i18n_audit`.

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## Текущий статус разработки

* **PR #1 (merged)** — проектная документация.
* **PR #2 (merged)** — Фаза 0 + Фаза 1 Спринт 1 «skeleton»: монорепо, лендинг, самописная auth (register / login / refresh / logout / me) с JWT (15 мин) + refresh-семьями в HttpOnly cookie + replay-детекцией, фундаментальный tenancy-слой (workspaces / workspace_members / brands), RLS-контекст через `SET LOCAL`, typed `AppError`, pytest auth + RLS, GitHub Actions CI.
* **PR #3–#10 (merged)** — email verification, MFA (TOTP), password reset, audit_events (partitioned + retention pg_cron), skill-инфраструктура (D68/D69/D70), event bus + WebSocket, Unleash feature flags + idempotency middleware, OpenTelemetry, multi-currency billing skeleton.
* **PR #11 (merged)** — RLS policies на бизнес-таблицы (workspaces / workspace_members / brands / refresh_tokens / idempotency_keys / invoices) + `app_user` роль с least-privilege grants + PgBouncer (`pool_mode=transaction`) в docker-compose + Postgres-only integration suite в CI (`backend-postgres`).
* **PR #12 (этот PR)** — три пункта из docs/06 §5 Sprint 1:
  (1) Redis membership cache (`user:{id}:memberships`, TTL 5 мин) +
      WS-push `auth.refresh_required` при изменении ролей (D64),
      `/v1/auth/me` отдаёт memberships из кэша вместо БД;
  (2) `tenant_limit_overrides` таблица + ORM + read-through resolver
      (`app/services/billing/quotas.py`), миграция 0009 с RLS-policy;
  (3) CI-чек `scripts/check_timestamptz.py` (TIMESTAMP без timezone) +
      `apps/web/scripts/i18n_audit.ts` (ru ⇔ en parity + Cyrillic hardcode)
      + `.pre-commit-config.yaml` (ruff + biome + i18n + SET LOCAL +
      timestamptz + system-prompt-lang + validate_skills).

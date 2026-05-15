# План: Фаза 0 + первый PR Спринта 1

## Контекст

`06-roadmap.md §5 Фаза 1 Спринт 1` — это очень большой объём (auth + tenancy + RLS + Skill-инфра + i18n + event bus + WebSocket + Unleash + idempotency + OTel + retention + audit_events + ...). Я предлагаю разбить Спринт 1 на несколько PR, чтобы первый PR был обозримый и быстро смерджился. В первом PR — фундамент, без которого ничего не запустить; всё остальное — в следующих PR этого же спринта.

## Что в PR #2 (этот PR — "skeleton")

### Фаза 0
- Монорепо: `apps/backend/` (FastAPI) + `apps/web/` (Next.js)
- Docker Compose dev: Postgres 16 + pgvector + Redis 7 + MailHog
- `Makefile` (`make install`, `make dev`, `make migrate`, `make test`, `make lint`)
- Корневой `README.md` — как поднять проект локально
- `.env.example` — все нужные переменные
- Pre-commit hooks (ruff + biome)

### Фаза 1 Спринт 1 — backend (ядро)
Адаптируем напрямую из `~/reference/api`:
- **SQLAlchemy 2.0 async + asyncpg + Alembic** (стек копируем 1-в-1)
- **Модели** (минимум для auth + tenancy):
  - `users` — UUID PK, email (unique), `hashed_password`, `full_name`, `avatar_url`, `status` (active/blocked/deleted), `email_verified_at`, `tos_accepted_at`, `locale` (default `ru-RU`), `timezone` (default `Europe/Minsk`), `preferred_currency` (default `RUB`), `platform_role` (default `user`), `token_version`
  - `workspaces` — UUID PK, `owner_id`, `name`, `slug`, `type` (`solo`/`agency`/`network`), `preferred_currency`
  - `workspace_memberships` — `workspace_id` + `user_id` (composite PK), `role` (`owner`/`admin`/`editor`/`viewer`/`reviewer`/`analyst`), `brand_ids[]`
  - `refresh_tokens` — UUID PK, `user_id`, `token_hash` (SHA-256), `family_id`, `parent_id`, `replaced_by`, `expires_at`, `revoked_at`, `user_agent`, `ip`
  - `brands` — UUID PK, `workspace_id`, `name`, `content_language` (default `ru`), `timezone`, soft-delete
- **Самописная auth (D28 / D36):**
  - `POST /v1/auth/register` — регистрация (создаёт User + default Workspace + default Brand + Membership owner в одной транзакции)
  - `POST /v1/auth/login` — bcrypt verify, login-lockout через Redis (10 попыток / 30 мин)
  - `POST /v1/auth/refresh` — ротация refresh-семьи с replay-детекцией (revoke всей семьи)
  - `POST /v1/auth/logout` — revoke текущей семьи + очистка cookies
  - `GET /v1/auth/me` — текущий пользователь + active_workspace
  - JWT: 15 мин access, claims `sub` (user_id), `active_workspace_id`, `platform_role`, `exp`, `iat`, `jti`, `tv` (token_version), `type='access'`. **Memberships не кладём** (D64 — будут в Redis с TTL 5 мин, добавим в следующем PR)
  - Refresh: 30 дней, HttpOnly cookie `refresh_token` (Path=/v1/auth), SameSite=Lax, Secure в проде
  - Password hashing: `passlib[bcrypt]` (1-в-1 из reference; миграция на Argon2id отдельным PR при необходимости)
- **RLS контекст (D65):** dependency `set_rls_context()` выполняет `SET LOCAL app.current_user_id / app.current_tenant_id / app.platform_role` на каждый авторизованный запрос. CI-линтер `tools/lint_set_local.py` запрещает `SET app.*` без `LOCAL`. **Сами RLS-policies для бизнес-таблиц** — следующим PR (вместе с `brands`/`channels` контентом)
- **Typed API errors (D62):** базовый `AppError` + `ErrorCode` enum (минимум: `INVALID_CREDENTIALS`, `EMAIL_ALREADY_EXISTS`, `INVALID_REFRESH_TOKEN`, `REFRESH_TOKEN_REPLAYED`, `LOGIN_LOCKED`, `TOS_NOT_ACCEPTED`, `UNAUTHENTICATED`) + FastAPI exception handler → `{error_code, message, suggested_action}`
- **Sentry + structlog** — базовая инициализация
- **Pydantic-settings** — все ключи из `.env`

### Фаза 1 Спринт 1 — frontend (минимум)
- **Next.js 15** + Tailwind 4 + shadcn/ui начальный setup, TS strict
- Route-группы `(public)/`, `(auth)/`, `(app)/`
- `apps/web/messages/ru.json` + `apps/web/messages/en.json` (пустой для 404/500), `next-intl@^3` подключён
- **Страницы (минимум для тестов работы backend):**
  - `/` — лендинг: логотип (текстовый) + кнопка «Начать» → `/login`
  - `/login` — форма email + password
  - `/register` — форма email + password + full_name + чекбокс ToS
  - `/dashboard` — приватная страница: «Привет, {email}, workspace: {name}»; кнопка «Выйти»
- TanStack Query v5 для запросов; Zustand для UI-стора
- Никакой полировки — только формы и базовый flow

### Тесты
- **Backend** (pytest + pytest-asyncio + httpx + aiosqlite):
  - `test_register.py` — успех, duplicate email (409), пропущенный ToS (422)
  - `test_login.py` — успех, неверный пароль (401), login-lockout после 10 попыток (423)
  - `test_refresh.py` — ротация, replay detection (403 + revoke family), expired (401)
  - `test_logout.py` — revoke family, повторный refresh = 401
  - `test_me.py` — без токена 401, с токеном 200
  - `test_workspaces.py` — sign-up создаёт default workspace + membership owner
  - `test_rls_context.py` — `SET LOCAL` срабатывает на dependency level

### CI
- GitHub Actions: lint (`ruff` + `biome`) + typecheck (`mypy strict` + `tsc`) + test (`pytest` + `vitest`) + Postgres service контейнер

### Документация
- `README.md` (корневой): как поднять проект, схема монорепо
- `apps/backend/README.md` — backend-specific
- `apps/web/README.md` — web-specific

## Что НЕ в PR #2 (выносим в следующие PR этого же Спринта 1)

| Тема | PR |
|---|---|
| Email verification flow + UniSender Go клиент | PR #3 |
| Forgot password / reset password | PR #3 |
| MFA TOTP (enroll / verify / disable / recovery) | PR #4 |
| `audit_events` + pg_partman + retention pg_cron jobs (active=false) | PR #5 |
| Skill infrastructure (`apps/backend/skills/`, `SkillManifest`, `SkillRegistry`, `SkillCompiler`) | PR #6 |
| Event Bus skeleton (Redis Pub/Sub + Pydantic discriminated unions, первое событие `user.registered`) | PR #7 |
| WebSocket skeleton + `useRealtime` хук | PR #7 |
| Unleash client + первый флаг `enable_auto_publish` | PR #8 |
| Idempotency middleware + таблица `idempotency_keys` | PR #8 |
| OpenTelemetry FastAPI + SQLAlchemy + Celery | PR #9 |
| Multi-currency billing skeleton (`plans`, `plan_prices`, `invoices`) | PR #10 |
| RLS policies для всех бизнес-таблиц + PgBouncer-конфиг | PR #11 |

## Технические решения

| Решение | Источник |
|---|---|
| Bcrypt вместо Argon2id на первом проходе | Reference уже использует bcrypt; миграция Argon2id — отдельной задачей |
| `passlib[bcrypt]` + `python-jose[cryptography]` | 1-в-1 из reference |
| Refresh family с SHA-256 hash + replay detection | 1-в-1 из reference (`PR-T7` в reference) |
| `nh_*` cookie prefixes из reference → переименовываю в `sm_access` / `sm_refresh` / `sm_csrf` для нашего проекта (social-media) | Адаптация |
| `/api/v1/auth/...` → `/v1/auth/...` | По нашему `05 §3.1` |
| TOS — обязательное поле на регистрации | Из reference + нашего `04 §22` |
| `users.locale='ru-RU'`, `users.timezone='Europe/Minsk'` defaults | Из нашего `04 §18` |
| Default workspace создаётся в той же транзакции, что и User | 1-в-1 из reference |
| Login lockout через Redis (10/30мин) | 1-в-1 из reference |

## Чего НЕ копируем из reference

- Google OAuth (`auth_google.py`, `oauth_state.py`) — не в нашем MVP
- TOTP/2FA (отдельный PR #4)
- Password reset/forgot (отдельный PR #3)
- hCaptcha (в нашем MVP не упомянут как обязательный)
- Referrals (`services/referrals.py`)
- Telegram model + service (есть свой Telegram-агент в нашем плане, делаем со Спринта 2)
- 40+ доменных моделей (admin, billing, broadcasts, channels, collections, competitors, content_plan, dashboard, notifications, posts, social, studio, voice_profiles, ...) — у нас своя архитектура из `04`

## Метрики приёма PR #2

- `make dev` запускает backend + web + Postgres + Redis + MailHog
- `make migrate` применяет миграции
- `make test` — все тесты зелёные
- Vertical slice: на `/` нажать «Начать» → `/login` → войти / зарегистрироваться → `/dashboard` показывает email и workspace
- CI зелёный (lint + typecheck + test)
- 0 Sentry-ошибок на signup → login flow

---

Если согласен — приступаю. Если что-то нужно подвинуть (например, добавить email verification в первый PR, или наоборот сильнее урезать) — скажи.

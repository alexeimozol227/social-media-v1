# План: Фаза 1 Спринт 2 — первый PR (Channels foundation)

## Контекст

`06-roadmap.md §5 Фаза 1 Спринт 2` (недели 4–6) — это снова крупный объём: `Global Channel Registry` (D20 из `03`), Telegram Bot API через `aiogram 3.x` (D40 из `05 §5`), user-bot на `Pyrogram` для публичных каналов конкурентов, партиционирование `channel_post_embeddings` через `pg_partman` + HNSW (D61 в `04 §18.6`), парсинг истории канала через Bot API, webhook на новые посты + событие `channel.post_received` в event bus (D32 / D41), плюс минимальный workspace-UI (переключатель брендов + хедер `X-Active-Brand-Id`).

Фундамент Фазы 0 + Спринта 1 уже на месте (см. `reports/phase0-phase1-sprint1-report.md`): монорепо, FastAPI + SQLAlchemy 2.0 async + Alembic, самописная авторизация (JWT 15 мин + refresh-семьи + email verification + password reset + MFA TOTP), RLS-контекст и RLS-policies на tenant-таблицах, PgBouncer transaction pooling, `audit_events` + pg_partman + retention pg_cron skeleton (`active=false`), Skill-инфраструктура (`SkillManifest` / `SkillRegistry` / `SkillCompiler` + CI-проверки), Event Bus (Redis Pub/Sub + Pydantic discriminated unions, событие `user.registered`), WebSocket + `useRealtime` хук, Unleash client + флаг `enable_auto_publish`, Idempotency middleware + таблица `idempotency_keys`, OpenTelemetry для FastAPI / SQLAlchemy / Celery, billing skeleton (`plans` / `plan_prices` / `invoices` / `tenant_limit_overrides`), `Membership` cache в Redis + WS-push `auth.refresh_required`.

Я предлагаю разбить Спринт 2 на несколько PR — первый PR будет «**Channels foundation**»: схема каналов, dependency на активный бренд, минимальная обвязка `aiogram 3.x`, проверка прав бота через Bot API `getChatMember` / `getChatAdministrators` и базовый API подключения / отвязки канала. Всё остальное (history-backfill, webhooks → event bus, embeddings + HNSW, user-bot на Pyrogram, доска вдохновения L1-заготовка) — следующими PR этого же Спринта 2.

## Что в PR #14 (этот PR — «channels-foundation»)

### Бэкенд — модели и миграции

- **`channels`** (Global Channel Registry, D20 в `03`):
  - `id` UUID PK
  - `platform` enum (`telegram`) — на MVP только TG
  - `external_id` BIGINT (TG `chat.id`) — UNIQUE с `platform`
  - `username` VARCHAR(64) NULL (TG `@handle`)
  - `title` VARCHAR(255) NULL
  - `description` TEXT NULL
  - `subscribers_count` INT NULL
  - `is_public` BOOLEAN — `True`, если можно читать без бот-админки (для будущих конкурентов через user-bot)
  - `first_seen_at` / `last_seen_at` TIMESTAMPTZ
  - **НЕ tenant-scoped** — это общий регистр, шарится между всеми workspace'ами (один канал = одна запись)
- **`workspace_channels`** (привязка канала к бренду внутри workspace'а):
  - `id` UUID PK
  - `workspace_id` FK → `workspaces.id` (RLS-scope)
  - `brand_id` FK → `brands.id`
  - `channel_id` FK → `channels.id`
  - `role` enum (`owned` — наш бот админ; `competitor` — публичный, через user-bot, будет в PR #18)
  - `bot_admin_rights` JSONB — снимок прав (`can_post_messages`, `can_edit_messages`, `can_delete_messages`)
  - `connected_at` TIMESTAMPTZ, `disconnected_at` TIMESTAMPTZ NULL (soft-detach)
  - UNIQUE `(workspace_id, brand_id, channel_id)`
  - На таблицу применяется RLS-policy `workspace_isolation` (по `workspace_id = app.current_tenant_id`)
- **`channel_posts`** (партиционирование заведём в PR #17 вместе с embeddings; здесь — обычная таблица с месячными секциями через `pg_partman`):
  - `id` UUID PK
  - `channel_id` FK → `channels.id` — НЕ tenant-scoped (общий регистр)
  - `tg_message_id` BIGINT — UNIQUE с `channel_id`
  - `text` TEXT NULL
  - `entities` JSONB NULL — TG message-entities для восстановления MarkdownV2
  - `has_media` BOOLEAN, `media_summary` JSONB NULL
  - `views_count` / `reactions_count` / `forwards_count` INT NULL
  - `posted_at` TIMESTAMPTZ — партиционируется помесячно через `pg_partman.create_parent(...)`
  - `created_at` TIMESTAMPTZ
- **pg_cron retention job `retention_channel_posts_cold_archive`** заводится **в одной миграции с `channel_posts`**, со статусом `active=false` (`SELECT cron.alter_job(jobid, active := false)`). Активация — Спринт 8, вместе с остальными retention jobs (D57 в `04 §18.5`)
- **Миграции:** одна Alembic-миграция `0013_channels_registry.py` со всеми тремя таблицами + RLS-policy на `workspace_channels` + пустой `cron.schedule(...)` для cold-archive

### Бэкенд — активный бренд + dependency

- **`active_brand_id`** в JWT claims (`04 §17.2`, расширяем strict-claims из D64):
  - При login / refresh — если у юзера один бренд, кладём его `brand_id` в `active_brand_id`. Если несколько — берём «дефолтный» (`brands.is_default=true`)
  - Header `X-Active-Brand-Id` на запросах от фронта позволяет временно переключиться без перевыпуска JWT (нужен для multi-brand UI в Спринте 9)
- **FastAPI dependency `get_active_brand()`:**
  - Резолвит `brand_id` в порядке: `X-Active-Brand-Id` header → `active_brand_id` из JWT
  - Проверяет, что `brand.workspace_id = app.current_tenant_id` (через RLS — запрос вернёт `None`, если бренд не наш) → 403 `BRAND_NOT_IN_WORKSPACE`
  - Возвращает `Brand` ORM-объект
- **`brands.is_default` BOOLEAN** — добавляется миграцией; constraint: ровно один `is_default=true` на workspace (`EXCLUDE` или partial unique index)

### Бэкенд — Telegram Bot API через aiogram 3.x

- **`apps/backend/adapters/social/telegram_bot.py`** — тонкая обёртка над `aiogram 3.x`:
  - `class TelegramBotClient` с методами `get_chat(chat_id)`, `get_chat_member(chat_id, user_id)`, `get_chat_administrators(chat_id)` — этого хватает для PR #14
  - `polling=False` — пока не запускаем диспетчер, только REST-вызовы к Bot API. Webhook + dispatcher — в PR #16
  - Bot token из `pydantic-settings` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`) + раздельный токен для dev (`TELEGRAM_BOT_TOKEN_DEV`) — P0.10 в `06 §4`
  - `MockTelegramBotClient` для unit / integration тестов (`tests/fixtures/telegram/`) — фикстуры для `getChat`, `getChatMember`, `getChatAdministrators`

### Бэкенд — API эндпоинты

Все эндпоинты — с префиксом `/v1/`, kebab-case, plural nouns (`05 §3.1`):

- `POST /v1/brands/{brand_id}/channels` — подключить TG-канал (наш бот = админ):
  - Body: `{"platform": "telegram", "identifier": "@my_channel" | -100123456789}`
  - Шаги:
    1. Резолвим канал через `bot.get_chat(identifier)` → получаем `chat.id`, `title`, `username`, `description`
    2. Проверяем `getChatMember(chat_id, bot.id)` → требуем `status='administrator'` + `can_post_messages=True`
    3. UPSERT в `channels` (по `(platform, external_id)`)
    4. Создаём row в `workspace_channels` с `role='owned'`, снимком `bot_admin_rights`
    5. Пишем `audit_event` (`severity='info'`, `event_type='channel.connected'`)
    6. Возвращаем `ChannelConnectedResponse`
  - Ошибки (D62 typed errors): `CHANNEL_NOT_FOUND` (404), `BOT_NOT_ADMIN` (409 + `suggested_action`), `BOT_MISSING_POST_PERMISSION` (409), `CHANNEL_ALREADY_CONNECTED` (409)
- `GET /v1/brands/{brand_id}/channels` — список подключённых каналов бренда (с пагинацией `limit`/`offset`)
- `DELETE /v1/brands/{brand_id}/channels/{channel_id}` — soft-detach (ставит `disconnected_at`); сама запись `channels` остаётся в регистре
- `POST /v1/brands/{brand_id}/channels/{channel_id}/verify` — повторная проверка `getChatMember` (если юзер случайно убрал бота из админов) → обновляет `bot_admin_rights` snapshot

### Фронтенд — минимум для тестов

- **Header brand switcher** (`apps/web/components/brand-switcher.tsx`):
  - Zustand-store `useActiveBrandStore` (D43 / `05 §6.3`) — `activeBrandId` персистится в `localStorage`
  - При выборе бренда → пишет в store + добавляет header `X-Active-Brand-Id` в TanStack Query default fetcher
  - UI: dropdown с названиями брендов из `/v1/users/me/brands` (новый эндпоинт-хелпер)
- **`/dashboard/channels`** — список каналов активного бренда:
  - Таблица с колонками: title, @username, status (active / detached), connected_at, кнопка «Detach»
  - Пустое состояние: кнопка «Подключить канал» → открывает wizard-модал
- **Wizard «Подключить канал»** (`apps/web/components/connect-channel-wizard.tsx`):
  - Шаг 1: инструкция (3 строки + код-блок) — «1) Откройте свой TG-канал → Настройки → Администраторы → Добавить администратора. 2) Найдите `@<bot_username>` и дайте право «Публикация сообщений». 3) Введите ниже `@username` канала или его TG-id (если канал приватный)»
  - Шаг 2: input + «Проверить»
  - Шаг 3: результат (ok / ошибка с CTA)
- Никакой полировки: только формы, таблица, модал. Tailwind + shadcn/ui без кастомных стилей. Полный flow — для проверки backend'а, а не визуала.

### i18n / event bus

- Все новые user-facing строки — только через `useTranslations()` (`04 §18.1`). Ключи добавляются в `apps/web/messages/ru.json`; зеркальные пустые ключи — в `en.json` (CI `i18n_audit.ts` ловит расхождения)
- Backend ошибки маппятся через `useApiError` хук на toast с `suggested_action`
- **Новые события event-bus** (схемы Pydantic discriminated unions, `apps/backend/events/schemas.py`):
  - `channel.connected` — publish после `POST /v1/brands/{id}/channels` (для будущего WS-toast на дашборде, в PR #14 пока без подписчиков)
  - `channel.detached`
  - Тело: `{event_id, event_type, workspace_id, brand_id, channel_id, agent_source='api', timestamp, idempotency_key}` (см. `04 §10`)

### Тесты

- **Backend** (pytest + pytest-asyncio + httpx + respx):
  - `test_channels_connect.py` — успех (mock `getChat` + `getChatMember`); `BOT_NOT_ADMIN` (409); `BOT_MISSING_POST_PERMISSION` (409); `CHANNEL_ALREADY_CONNECTED` (409); not-found channel (404)
  - `test_channels_list.py` — пустой список; пагинация; фильтр по бренду; RLS: юзер другого workspace'а не видит
  - `test_channels_detach.py` — soft-detach ставит `disconnected_at`; повторный detach 409
  - `test_active_brand_dep.py` — резолв через JWT claim; override через `X-Active-Brand-Id`; чужой бренд → 403 `BRAND_NOT_IN_WORKSPACE`
  - `test_telegram_bot_client.py` — `MockTelegramBotClient` корректно мокает все 3 метода; ошибки Bot API (rate-limit, invalid token) маппятся в `AppError`
- **Frontend** (Vitest + Testing Library):
  - `brand-switcher.test.tsx` — переключение пишет в store, header добавляется в fetcher
  - `connect-channel-wizard.test.tsx` — шаги переключаются; ошибка показывается с `suggested_action`

### CI

- Те же чеки, что в Спринте 1: `lint` (ruff + ruff-format + biome) + `typecheck` (mypy strict + tsc) + `test` (pytest + Vitest) + `tools/lint_set_local.py` + `scripts/check_timestamptz.py` + `scripts/i18n_audit.ts` + `scripts/check_system_prompt_lang.py` + `validate_skills` + `skill-token-budget`
- Новые env-переменные документируются в `apps/backend/.env.example`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN_DEV`, `TELEGRAM_BOT_USERNAME`

### Документация

- `apps/backend/README.md` — раздел «Telegram integration (Bot API)»: как получить токен у `@BotFather`, как подменять на `MockTelegramBotClient` в тестах
- `apps/web/README.md` — раздел «Channels UI»: что показывает `/dashboard/channels` и как ходит wizard

## Что НЕ в PR #14 (выносим в следующие PR этого же Спринта 2)

| Тема | PR |
|---|---|
| Backfill истории канала через Bot API (последние 100–500 постов) + Celery-задача + дедуп `(channel_id, tg_message_id)` | PR #15 |
| `aiogram 3.x` Dispatcher + webhook-эндпоинт `/v1/integrations/telegram/webhook` + ингест входящих channel-posts → publish `channel.post_received` в event bus | PR #16 |
| `channel_post_embeddings` через `pg_partman` партиционирование + HNSW-индексы + `pg_cron` safety-check 25-го числа (Sentry alert, если HNSW отсутствует в новой партиции) — D61 в `04 §18.6` | PR #17 |
| Embedding pipeline (`text-embedding-3-small` через `LLMProvider`-абстракцию-заготовку) + Celery-задача `embed_channel_post` — заготовка под Спринт 3, без production-нагрузки | PR #17 |
| user-bot на `Pyrogram`: пул аккаунтов с `api_id` + `api_hash`, зашифрованные сессии в БД (`pyrogram_sessions`), ротация, healthcheck — `05 §5.2`, D40 | PR #18 |
| `workspace_channels.role='competitor'` + API `POST /v1/brands/{id}/competitors` (добавление публичного канала на чтение) — заготовка под Inspiration Board L1 (Спринт 9) | PR #18 |
| Workspace settings UI: страница `/settings/brands` для CRUD брендов + смена `is_default` + квоты по тарифу | PR #19 |
| Дашборд бренда `/dashboard` v0 с превью «5 последних постов канала» (использует данные history-backfill из PR #15) | PR #19 |

## Технические решения

| Решение | Источник / Обоснование |
|---|---|
| `aiogram 3.x` — Bot API клиент с polling=False в PR #14 | `05 §5.1`. В PR #14 нужны только REST-вызовы для верификации админских прав; Dispatcher / webhook — в PR #16 |
| Global Channel Registry — общая таблица `channels` без `workspace_id`, RLS только на `workspace_channels` | D20 в `03`, `04 §11.3`. Дедупликация: один и тот же канал, подключённый разными workspace'ами, парсится один раз |
| `channel_posts` партиционируем помесячно с самого начала через `pg_partman` | `04 §18.6`. Дешевле сделать сразу, чем мигрировать миллионы строк потом |
| `pg_cron retention_channel_posts_cold_archive` со статусом `active=false` с самого начала | D57 в `04 §18.5`. Активация — Спринт 8 единой миграцией для всех 5 retention jobs |
| `X-Active-Brand-Id` header + `active_brand_id` в JWT claim | `04 §17.2`, расширение strict-claims из D64. Позволяет переключать бренд из UI без полного re-login |
| `bot_admin_rights` JSONB snapshot в `workspace_channels` | Чтобы видеть в админке «когда у бота отобрали права на публикацию» без лишних запросов к Bot API |
| Soft-detach (`disconnected_at`) вместо hard-delete | `audit_events` + возможность реактивировать канал, не теряя историю |
| `MockTelegramBotClient` через `respx` / fixture-набор | `05 §13`. Не требуем реальный `TELEGRAM_BOT_TOKEN` для локальной разработки и CI |
| `aiogram[fast]` + `aiogram-typeddict` или встроенные Pydantic-схемы для типизации Bot API ответов | mypy strict — нужны типы. Берём готовое из aiogram 3 |
| `brands.is_default` boolean + EXCLUDE constraint «не более одного дефолтного на workspace» | Решает «куда падает регистрация нового канала, если брендов > 1» без введения нового концепта |

## Чего НЕ делаем в Спринте 2 вообще

- **Channel post embeddings в production-нагрузке** — заготовка `channel_post_embeddings_template` + HNSW индексы + Celery-задача `embed_channel_post` лежит в PR #17 как infra; реально включаем embedding-пайплайн в Спринте 3 одновременно с `LLMProvider` и `BrandMemory` (D33 в `04`)
- **Чтение приватных каналов** через user-bot — Pyrogram читает **только публичные каналы конкурентов** (`role='competitor'`). Приватный канал клиента читается только через Bot API, потому что наш бот в нём админ
- **TG-бот approve flow (inline-кнопки)** — это `06 §7 Спринт 9` через aiogram FSM. В Спринте 2 у нас только REST-обращения к Bot API
- **`OnboardingAgent` авто-экстракция Brand Memory из истории канала** — `06 §5 Спринт 3`; в Спринте 2 только сохраняем `channel_posts`, агенты с ними не работают
- **Расширение audit-таблицы под Telegram-события** — пишем в существующую `audit_events` без новой схемы; конкретные `event_type` (`channel.connected`, `channel.detached`) — это значения, не колонки
- **Pay-per-channel UI / прогресс-бар каналов** — лимит «TG-каналов на бренд» по тарифу проверяем через `tenant_limit_overrides` + `plans`, но UI-полировки нет; банер про апгрейд — после Спринта 10 (billing v0)

## Метрики приёма PR #14

- `make migrate` — все Alembic-миграции применяются на чистой БД
- `make dev` запускает backend + web + Postgres + Redis + MailHog — `MockTelegramBotClient` отдаёт фикстуры без реального токена
- `make test` — все backend и frontend тесты зелёные; покрытие критических путей `channels-connect` / `channels-detach` / `active-brand-dep` ≥ 80%
- `make lint` / `make typecheck` зелёные — ruff strict, mypy strict, biome, tsc strict
- 0 Sentry-ошибок на flow «зарегистрироваться → войти → подключить канал (через mock) → увидеть его в `/dashboard/channels` → отвязать»
- Vertical slice работает целиком: юзер `/login` → `/dashboard/channels` → wizard → ввёл `@test_channel` → mock-бот вернул «admin + can_post» → канал появился в таблице → нажал «Detach» → канал ушёл в статус `disconnected`
- RLS подтверждён интеграционным тестом: юзер workspace'а A не видит каналы workspace'а B через `GET /v1/brands/{id}/channels` (404 / пустой список)
- Все новые backend errors (`BOT_NOT_ADMIN`, `BOT_MISSING_POST_PERMISSION`, `CHANNEL_ALREADY_CONNECTED`, `BRAND_NOT_IN_WORKSPACE`) корректно мапятся в JSON и подхватываются `useApiError` хуком на фронте

---

Если согласен — приступаю. Если что-то нужно подвинуть (например, перенести `aiogram` Dispatcher / webhook ингест из PR #16 прямо в PR #14, или ужать дополнительно — например, без `bot_admin_rights` snapshot) — скажи.

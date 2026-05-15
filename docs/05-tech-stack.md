# Tech Stack: какие технологии берём и почему

> **Документ:** `05-tech-stack.md`
> **Статус:** v1.5
> **Owner:** Мозоль Алексей
> **Дата последнего обновления:** 2026-05-14
> **Зачем нужен:** Зафиксировать конкретный набор языков, фреймворков и библиотек для MVP — чтобы в процессе разработки не «ходить по магазинам», не выбирать каждый раз заново и не складывать в проект сразу пять конкурирующих библиотек. Каждый выбор привязан к архитектурным принципам П1–П13 из `04-architecture.md` и инвариантам I1–I17 из `01-product-vision.md`.

---

## ⭐ Зафиксированные решения

> Нумерация продолжает сквозную линию документов: `01` (D1–D7), `02` (D8–D11), `03` (D12–D25), `04` (D26–D34, D56–D70). В `05` фиксируются **D35–D43**.

| # | Решение | Значение |
| --- | --- | --- |
| **D35** | **Очередь задач** | **Celery 5** + Celery Beat (встроенный cron). Брокер — Redis 7. Финализирует D26 из `04`. Async-задачи — через `celery-aio-pool` либо обёртку `asyncio.run` |
| **D36** ⭐ | **Авторизация — конкретика** | **Самописная авторизация на FastAPI** (реализация D28 из `04`): JWT access 15 мин + refresh-токен в HttpOnly cookie 30 дней + email/password + MFA. Без `FastAPI-Users`, `Supabase Auth`, `Auth.js` и подобных — нужен полный контроль над user-моделью, RLS-контекстом и refresh-логикой |
| **D37** | **Frontend деплой** | Next.js 15 в Docker на том же сервере, что и бэкенд. Nginx — обратный прокси + раздаёт статику. Vercel / Cloudflare Pages не используем |
| **D38** | **AI-фреймворк** | **LangChain 0.3** — для вызова инструментов (tool-calling) и структурированных ответов. **LlamaIndex 0.12** — точечно для семантического поиска по истории канала (RAG). Всё остальное — прямые вызовы LLM API через абстракцию `LLMProvider` (`PolzaProvider`) |
| **D39** | **Email** | **UniSender Go** (транзакционные письма, free tier 1000 писем/мес → дальше ~₽0.30/письмо) |
| **D40** | **Поставщик картинок** | Абстракция в `apps/backend/adapters/image/`. На MVP — **Flux-2 Pro** (5 ₽/изображение) и **Nano Banana 2** (4.8 ₽/изображение), обе через polza.ai. Routing по типу контента: Flux-2 Pro для качественного визуала, Nano Banana — для «лёгких» иллюстраций |
| **D41** | **Шина событий** | **Redis Pub/Sub** (тот же единственный Redis на MVP) + Pydantic v2 discriminated unions. Контракты — в `apps/backend/events/schemas.py`. Реализует D32 из `04` |
| **D42** | **Feature flags и kill-switches** | **Unleash** (self-hosted, OSS) — для авто-публикации, авто-ответов, выкатки агентов и A/B-тестов промптов. Реализует П10 из `04` |
| **D43** | **Real-time канал в UI** | **WebSocket** (FastAPI native) — основной; **SSE** — резерв для прокси/корпоративных сетей. Клиент — `@tanstack/react-query` + хук `useWebSocket`. Polling в UI запрещён (П9) |


---

## 1. Сводная таблица стека

| Слой | Что используем |
| --- | --- |
| **Backend язык** | Python 3.12 |
| **Backend фреймворк** | FastAPI 0.115+ |
| **ORM** | SQLAlchemy 2.0 (async) + Alembic (миграции) |
| **БД** | PostgreSQL 16 + расширения: `pgvector`, RLS, `pg_trgm`, **`pg_partman`**, **`pg_cron`** |
| **Connection pooler** ⭐ | **PgBouncer** (`pool_mode = transaction`) — `SET LOCAL` для RLS-контекста (D65 из `04`) |
| **Кэш / Pub-Sub** | Redis 7 (+ membership cache D64, media cache D60, skill overrides cache D70) |
| **Очередь задач** | **Celery 5 + Celery Beat** (брокер — Redis) |
| **Авторизация** ⭐ | **Самописная** на FastAPI: JWT + refresh-cookie, MFA, без сторонних библиотек |
| **Шина событий** | Redis Pub/Sub + Pydantic discriminated unions |
| **Feature flags** | Unleash (self-hosted) |
| **AI-фреймворк** | LangChain 0.3 + LlamaIndex 0.12 (точечно) |
| **LLM-провайдер** | polza.ai (через абстракцию `LLMProvider`) |
| **Картинки** | Flux-2 Pro / Nano Banana 2 через polza.ai (через абстракцию `ImageProvider`) |
| **Telegram SDK** | aiogram 3.x (Bot API) |
| **Чтение публичных каналов** ⭐ | **user-bot на Pyrogram** с `api_id`+`api_hash` и ротацией аккаунтов — только для чтения публичных каналов, где наш бот не админ. HTML-парсер каналов не используем |
| **Real-time** | WebSocket (FastAPI) + SSE-фолбэк |
| **Frontend язык** | TypeScript 5.x (strict mode) |
| **Frontend фреймворк** | Next.js 15 (App Router) + React 19 |
| **UI-библиотека** | shadcn/ui + Tailwind CSS 4 |
| **Состояние** | TanStack Query v5 (серверное) + Zustand (локальное) |
| **Виртуализация длинных списков** | **`@tanstack/react-virtual` v3** — для admin LLM-calls, Inspiration Board, календаря, очереди публикаций, `agent_runs viewer` |
| **Формы** | react-hook-form + zod |
| **Редактор постов (Markdown V2)** | Tiptap 2 (с кастомным экспортом в TG MarkdownV2) |
| **Графики** | Recharts |
| **Схемы / валидация** | Pydantic 2 (backend) + zod (frontend), синхронизация через `pydantic-to-zod` |
| **API-контракт** | OpenAPI 3.1 (генерируется FastAPI) → `openapi-typescript` клиент. Все эндпоинты — с префиксом **`/v1/`** (см. §2.3) |
| **Object Storage** | S3-совместимое хранилище; бэкап → Backblaze B2 (конкретный провайдер выберем при покупке хостинга) |
| **CDN / статика** | nginx на бэкенд-сервере + S3 public bucket с `Cache-Control: public, max-age=31536000, immutable`. Cloudflare / Vercel — не используем |
| **Email** | UniSender Go (транзакционные) |
| **Платежи** | ЮKassa SDK + bepaid.by REST API через абстракцию `PaymentProvider` |
| **Логи / ошибки** | Sentry + structlog → stdout (12-Factor) |
| **Метрики / трейсы** | OpenTelemetry → Grafana Tempo (трейсы) + Prometheus + Grafana |
| **Продуктовая аналитика** | PostHog (self-hosted) |
| **CI / CD** | GitHub Actions |
| **Контейнеры** | Docker + Docker Compose |
| **Provisioning серверов** | Ansible (MVP) |
| **Менеджер пакетов Python** | uv |
| **Менеджер пакетов JS** | pnpm |

---

## 2. Бэкенд и хранилища

### 2.1 Структура репозитория — монорепо

Корень репо организован как монорепо с двумя приложениями:

```
repo-root/
├── apps/
│   ├── backend/                # Python / FastAPI — весь бэкенд
│   └── web/                    # Next.js — фронт + админка в одном приложении
├── packages/                   # опц., если потребуется общий код (типы, eslint-config)
├── scripts/                    # сервисные скрипты CI / dev
├── docker-compose.yml          # прод-композ
├── docker-compose.dev.yml      # локалка
├── Makefile                    # make install / migrate / dev / test / lint / format
├── pnpm-workspace.yaml         # JS-воркспейсы
└── pyproject.toml              # uv-проект (apps/backend)
```

### 2.1.1 `apps/backend/` — структура модульного монолита

```
apps/backend/
├── core/                       # инфра-код: config, db, auth, event_bus, deps
├── modules/                    # бизнес-модули
│   ├── users/
│   ├── workspaces/
│   ├── brands/
│   ├── channels/
│   ├── posts/
│   ├── moderation/
│   ├── billing/
│   └── brand_memory/
├── agents/                     # AI-агенты (Content, Publisher, Analyst, ...)
├── skills/                     # skill-based архитектура (D68–D70)
│   ├── _registry.py            # SkillRegistry, SkillCompiler, парсер DSL
│   ├── content-agent-base/SKILL.md
│   ├── sales-hooks-and-cta/SKILL.md
│   ├── tg-markdown-v2/SKILL.md
│   └── ... (5–7 базовых skills на MVP)
├── adapters/                   # внешние сервисы через единый интерфейс
│   ├── llm/                    # polza.ai
│   ├── image/                  # Flux-2 Pro, Nano Banana
│   ├── payment/                # ЮKassa, bepaid
│   ├── email/                  # UniSender Go
│   └── telegram/               # aiogram (Bot API) + user-bot на Pyrogram
├── workers/                    # Celery-таски
├── events/                     # схемы событий (Pydantic discriminated unions)
└── api/
    └── v1/                    # ⭐ все HTTP-эндпоинты под префиксом /v1/
        ├── auth.py
        ├── users.py
        ├── workspaces.py
        ├── brands.py
        ├── channels.py
        ├── posts.py
        ├── moderation.py
        ├── billing.py
        ├── brand_memory.py
        └── admin/              # /v1/admin/* — внутренние эндпоинты для роли admin/support
```

### 2.1.2 `apps/web/` — Next.js: и сайт, и админка

Одно фронтенд-приложение Next.js покрывает три аудитории: публичный сайт / лендинги, авторизованный личный кабинет и внутреннюю админку. Разделение — на уровне route-групп App Router.

```
apps/web/
├── app/                        # Next.js App Router
│   ├── (public)/               # лендинги, доки, публичные страницы — без авторизации
│   ├── (auth)/                 # login, signup, reset, verify-email, mfa
│   ├── (app)/                  # авторизованная клиентская зона
│   │   ├── dashboard/
│   │   ├── brands/             # переключатель брендов (D12 multi-brand)
│   │   ├── channels/
│   │   ├── posts/              # черновики, запланированные, опубликованные
│   │   ├── analytics/
│   │   ├── brand-memory/       # редактор Core + Overlays
│   │   ├── moderation/         # очередь pre-publish review
│   │   ├── settings/
│   │   └── billing/
│   └── (admin)/                # внутренняя админ-панель (платформенные роли admin / support)
│       ├── users/
│       ├── workspaces/
│       ├── llm-calls/
│       ├── agent-runs/
│       ├── feature-flags/      # Unleash прокси-UI
│       └── ...
├── components/
│   ├── ui/                     # shadcn/ui компоненты
│   ├── editor/                 # Tiptap + TG MarkdownV2
│   ├── calendar/
│   ├── analytics/
│   └── realtime/               # WebSocket-провайдер + хуки
├── lib/
│   ├── api-client.ts           # автогенерён из OpenAPI через openapi-typescript (под /v1/)
│   ├── auth.ts                 # клиент самописной авторизации (refresh-cookie aware)
│   ├── ws.ts                   # WebSocket-клиент с авто-reconnect
│   └── utils.ts
└── hooks/
```

> **Доступ к `(admin)/`** проверяется на уровне middleware фронта + бэкенд-эндпоинты `/v1/admin/*` принимают только JWT с `platform_role ∈ {admin, support}` (см. матрицу доступа в `04` §18.2).

### 2.2 Python 3.12 + FastAPI

**Почему Python:** лучший AI-стек (LangChain, LlamaIndex, vendor SDK), async-friendly, простой и для бэка, и для AI-кода в одном репо.

**Почему FastAPI 0.115+:**

- Auto-OpenAPI → синхронизация контракта с фронтом (`openapi-typescript`)
- Pydantic-валидация из коробки
- Async-first, отлично работает в I/O-bound сценариях (а у нас почти всё — внешние API)
- Native WebSocket (D43)
- Dependency injection — удобно для tenancy middleware и БД-сессий

**Альтернативы и почему отклонены:** Django (тяжёлый для async-heavy продукта), Litestar (молод), Flask (нет встроенного async и валидации).

### 2.3 REST API: конвенции и версионирование ⭐

**Эти правила применяются ко ВСЕМ HTTP-эндпоинтам бэкенда**, не только к авторизации. CI-линтер `tools/lint_api_routes.py` проверяет соответствие на pre-commit.

#### 2.3.1 Версионирование — префикс `/v1/`

Все эндпоинты живут под `/v1/...`. При несовместимых изменениях API заводим `/v2/...` параллельно и держим `/v1` ещё минимум одну версию.

```
apps/backend/api/v1/  →  все роутеры регистрируются с prefix="/v1"
```

Исключения (без `/v1`):

- `GET /health` и `GET /readyz` — для оркестратора / load balancer
- `GET /metrics` — Prometheus scrape (под basic auth)
- `POST /webhooks/<provider>` — Telegram / ЮKassa / bepaid / UniSender Go callbacks (определяется поставщиком)
- `WS /v1/ws` — WebSocket для realtime (тоже под `/v1`, но не REST)

#### 2.3.2 Нейминг путей — `kebab-case`, существительные во множественном числе

| Что | Правильно | Неправильно |
| --- | --- | --- |
| Ресурс | `/v1/posts` | `/v1/post`, `/v1/getPosts`, `/v1/get_posts` |
| Несколько слов | `/v1/brand-memory`, `/v1/feature-flags` | `/v1/brandMemory`, `/v1/brand_memory` |
| Действие | `/v1/auth/forgot-password` | `/v1/auth/forgotPassword`, `/v1/auth/forgotpassword` |

#### 2.3.3 CRUD-шаблон

Каждый ресурс описывается одной и той же шестёркой эндпоинтов:

```
GET    /v1/<resources>            — список (фильтры, пагинация)
POST   /v1/<resources>            — создать
GET    /v1/<resources>/{id}       — получить один
PATCH  /v1/<resources>/{id}       — частичное обновление
PUT    /v1/<resources>/{id}       — полное обновление (используем редко)
DELETE /v1/<resources>/{id}       — удалить (soft-delete по умолчанию)
```

#### 2.3.4 Вложенные ресурсы — максимум 2 уровня

```
GET    /v1/posts/{id}/comments
POST   /v1/posts/{id}/comments
DELETE /v1/posts/{id}/comments/{comment_id}
```

Глубже не уходим — становится нечитаемо. Если нужен доступ к глубокому ресурсу — даём отдельный top-level (`/v1/comments/{id}`) и фильтрацию по родителю query-параметром (`/v1/comments?post_id=...`).

#### 2.3.5 Нестандартные действия — глагол в конце

Для действий, которые не ложатся в CRUD (publish, archive, cancel, ban, switch), используем `POST` + явный глагол в конце пути:

```
POST   /v1/posts/{id}/publish
POST   /v1/posts/{id}/archive
POST   /v1/posts/{id}/duplicate
POST   /v1/users/{id}/ban
POST   /v1/workspaces/{id}/switch
POST   /v1/billing/subscriptions/{id}/cancel
```

#### 2.3.6 Примеры по модулям MVP

**Авторизация и пользователи:**

```
POST   /v1/auth/register
POST   /v1/auth/login
POST   /v1/auth/logout
POST   /v1/auth/refresh
POST   /v1/auth/forgot-password
POST   /v1/auth/reset-password
POST   /v1/auth/verify
POST   /v1/auth/mfa/setup
POST   /v1/auth/mfa/verify
GET    /v1/users/me
PATCH  /v1/users/me
DELETE /v1/users/me
```

**Workspaces и членство:**

```
GET    /v1/workspaces
POST   /v1/workspaces
GET    /v1/workspaces/{id}
PATCH  /v1/workspaces/{id}
DELETE /v1/workspaces/{id}
POST   /v1/workspaces/{id}/switch         # сделать активным
GET    /v1/workspaces/{id}/members
POST   /v1/workspaces/{id}/members        # пригласить
DELETE /v1/workspaces/{id}/members/{user_id}
```

**Бренды, каналы, посты, аналитика:**

```
GET    /v1/brands
POST   /v1/brands
GET    /v1/brands/{id}
PATCH  /v1/brands/{id}
DELETE /v1/brands/{id}

GET    /v1/brands/{id}/channels
POST   /v1/brands/{id}/channels
DELETE /v1/channels/{id}

GET    /v1/posts                          # фильтры: ?brand_id=&status=&from=&to=
POST   /v1/posts                          # создать черновик
GET    /v1/posts/{id}
PATCH  /v1/posts/{id}
DELETE /v1/posts/{id}
POST   /v1/posts/{id}/publish
POST   /v1/posts/{id}/archive
POST   /v1/posts/{id}/duplicate
POST   /v1/posts/{id}/approve             # для approve-flow
POST   /v1/posts/{id}/reject

GET    /v1/posts/{id}/comments
POST   /v1/posts/{id}/comments
DELETE /v1/posts/{id}/comments/{comment_id}

GET    /v1/analytics/posts                # агрегация по постам
GET    /v1/analytics/channels/{id}        # дашборд канала
GET    /v1/analytics/brands/{id}/weekly   # отчёт Analyst Agent
```

**Brand Memory:**

```
GET    /v1/brands/{id}/brand-memory
PATCH  /v1/brands/{id}/brand-memory       # обновить Core Profile
GET    /v1/brands/{id}/brand-memory/overlays/{network}
PATCH  /v1/brands/{id}/brand-memory/overlays/{network}
POST   /v1/brands/{id}/brand-memory/rebuild
```

**Биллинг:**

```
GET    /v1/billing/plans                  # список доступных тарифов
GET    /v1/billing/subscription           # текущая подписка пользователя
POST   /v1/billing/subscription           # оформить / сменить
POST   /v1/billing/subscription/cancel
GET    /v1/billing/invoices
GET    /v1/billing/usage                  # лимиты тарифа (F8b) — что пользователю показываем
```

**Внутренняя админка (`/v1/admin/*`, требуется роль admin / support):**

```
GET    /v1/admin/users
GET    /v1/admin/workspaces
GET    /v1/admin/llm-calls                # cost dashboard, F8 (internal)
GET    /v1/admin/agent-runs
GET    /v1/admin/feature-flags
POST   /v1/admin/feature-flags/{key}/toggle
POST   /v1/admin/kill-switches/{name}/trigger
```

#### 2.3.7 Pagination, фильтры, ошибки

- **Пагинация:** `?cursor=<opaque>&limit=50` (cursor-based, не offset). Ответ: `{ items: [...], next_cursor: "...", has_more: true }`.
- **Фильтры** — query-параметры со snake_case: `?brand_id=&status=draft&from=2026-01-01`.
- **Сортировка:** `?sort=-created_at,name` (минус = desc).
- **Ошибки** — RFC 7807 Problem Details: `{ type, title, status, detail, instance, trace_id }`. Без «магических» полей `code`/`error`.
- **Идемпотентность** (П13): мутирующие эндпоинты принимают заголовок `Idempotency-Key`.

### 2.4 PostgreSQL 16

Одна БД для всего: реляционные данные + векторы + аналитика. Это П2 из `04` — «одна БД, пока влезает».

**Расширения:**

- **`pgvector`** — embedding-поиск (брендовая память, история постов, конкуренты). D29 из `04`
- **RLS (Row-Level Security)** — изоляция данных по `workspace_id`. Каждая таблица с данными клиента имеет `workspace_id` + RLS-политику. D27 из `04`
- **`pg_trgm`** — fuzzy-search по постам и каналам (полнотекстовый поиск + триграммный индекс)
- **`pg_partman`** ⭐ (на MVP) — автоматическое помесячное партиционирование таблиц `channel_post_embeddings`, `llm_calls`, `agent_runs`. Создаём через `partman.create_parent` с `p_template_table` — все индексы (включая **HNSW** для embeddings) копируются на каждую новую партицию автоматически (D61 из `04`)
- **`pg_cron`** ⭐ (на MVP) — встроенный планировщик для retention-задач (D57). Что запускает:
  - `retention_daily` (3:00) — truncate `chain_of_thought` старше 30 дней, hard-delete устаревших строк
  - `partman_safety_check` (25-го числа) — проверяет, что в новой партиции есть HNSW-индекс, иначе шлёт алерт в Sentry
  - `cost_budget_check` (каждые 30 мин) — внутренняя проверка расходов LLM по бренду относительно технического порога (D66 из `04`). **Это внутренний CostGuardian для админа**, не пользовательский лимит тарифа

### 2.5 Redis 7

- **Очереди:** Celery (broker + result backend)
- **Кэш:** Brand Memory hot-cache (TTL 60 сек), ответы LLM на одинаковые промпты (TTL 1 час), refresh-сессии, rate-limit счётчики
- **Pub/Sub:** шина событий между агентами (D41) и real-time уведомления в UI
- **Идемпотентные ключи** (П13): TTL 24 часа на эндпоинты с `Idempotency-Key` — повторный вызов с тем же ключом не публикует пост дважды
- **Membership cache** ⭐ (D64 из `04`): `user:{user_id}:memberships → [{workspace_id, role, brand_ids}]`, TTL 5 мин. JWT хранит **только** `user_id` / `platform_role` / `active_workspace_id` / `exp` / `jti` — список workspace'ов достаётся из Redis при каждом запросе. При смене ролей: `DEL user:{uid}:memberships` + WS-push клиенту `auth.refresh_required`
- **Media cache по бренду** ⭐ (D60 из `04`): `media:cache:{brand_id}:{sha256(prompt|size|lora|visual_version)} → s3_url`, TTL 30 дней. Шеринг между брендами **запрещён** (каждый бренд платит за свои картинки)
- **Skill overrides cache** ⭐ (D70 из `04`): `brand:{id}:skills:overrides → [{name, version, is_override, overrides_skill, body_hash}]`, TTL 1 час. Сбрасывается при сохранении brand-custom skill + WS-push клиенту. Глобальные skills — in-memory dict при старте FastAPI (immutable), Redis нужен только для per-brand переопределений

### 2.5.1 PgBouncer (connection pooling) ⭐ (D65 из `04`)

> Без пула: FastAPI worker × 20 потоков × 50 воркспейсов = тысячи соединений к Postgres, который рекомендует ≤ 200. PgBouncer держит ~50 реальных backend-соединений и обслуживает ими тысячи клиентских.

**Конфигурация:**

```ini
# /etc/pgbouncer/pgbouncer.ini
[databases]
app = host=db.local port=5432 dbname=app

[pgbouncer]
pool_mode = transaction      ; КРИТИЧНО: не session
max_client_conn = 2000
default_pool_size = 50
reserve_pool_size = 10
server_idle_timeout = 600
```

**Почему `transaction`, а не `session`:**

- Session-pooling «прибивает» соединение к клиенту на всё время сессии — это съедает соединения и хороним саму идею пула
- RLS-контекст через `SET LOCAL ...` нам подходит идеально: переменная гасится при `COMMIT/ROLLBACK` автоматически
- FastAPI dependency-обёртка (см. 04 §18.7) запускает каждый HTTP-запрос в одной транзакции с `SET LOCAL app.current_user_id = ...; SET LOCAL app.current_tenant_id = ...;` — соединение возвращается в пул **без остатков контекста**

**Что запрещено на уровне кода (CI-линтер `tools/lint_set_local.py`):**

- `SET app.*` **без** `LOCAL` — нельзя
- Длинные транзакции (> 5 сек) — алерт в Sentry
- Cross-request shared SQLAlchemy session — запрещено

**Запасной пул для долгих операций:** отдельный `pgbouncer-session` (`pool_mode = session`) — только для админ-импортов, тяжёлой аналитики и миграций.

### 2.6 Celery 5 + Celery Beat (D35)

**Что выбираем:** Celery 5 с Redis в качестве брокера + Celery Beat для периодических задач (cron).

**Почему:**

- Зрелый, проверенный в продакшене
- Цепочки задач (`chains`), параллельные группы с агрегацией (`chords`), retries с экспоненциальным бэкоффом, rate-limit'ы из коробки
- **Celery Beat** — встроенный cron-планировщик (для еженедельных отчётов, ежедневного парсинга, обновления метрик)
- Веб-UI для мониторинга: **Flower**
- Работает с разными брокерами (Redis сейчас → RabbitMQ при росте)
- Большое сообщество и stack overflow

**Минимальная конфигурация:**

```python
# apps/backend/workers/celery_app.py
from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

celery_app = Celery(
    "smm_platform",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.content_worker",
        "app.workers.publisher_worker",
        "app.workers.media_worker",
        "app.workers.moderation_worker",
        "app.workers.analyst_worker",
        "app.workers.parser_worker",
        "app.workers.brand_memory_worker",
    ],
)

celery_app.conf.update(
    task_acks_late=True,                  # at-least-once семантика
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,          # справедливое распределение между воркерами
    task_default_retry_delay=30,
    task_default_max_retries=5,
)

celery_app.conf.beat_schedule = {
    "daily-competitor-parsing": {
        "task": "app.workers.parser_worker.parse_all_competitors",
        "schedule": crontab(hour=4, minute=0),
    },
    "weekly-analyst-reports": {
        "task": "app.workers.analyst_worker.generate_weekly_reports",
        "schedule": crontab(day_of_week=1, hour=9, minute=0),
    },
    "hourly-metrics-fetch": {
        "task": "app.workers.parser_worker.refresh_metrics",
        "schedule": crontab(minute=0),
    },
    "nightly-brand-memory-update": {
        "task": "app.workers.brand_memory_worker.refresh_overlays",
        "schedule": crontab(hour=3, minute=30),
    },
}
```

**Async-таски:** Celery 5+ поддерживает `async def` через **`celery-aio-pool`** (стандарт проекта) либо обёртку `asyncio.run`.

**Идемпотентность тасок (П13):** каждая Celery-таска принимает `idempotency_key: str` и сначала проверяет таблицу `task_idempotency`. Повторный retry с тем же ключом не создаёт дубликата (например, не публикует пост дважды).

**Когда придётся апгрейдиться:** для долгих stateful-workflow (например, многодневные A/B-тесты с координацией между шагами) — переход на **Temporal**. Это уже не на MVP.

### 2.7 Шина событий (D41)

**Стек:** Redis Pub/Sub + Pydantic v2 discriminated unions.

**Контракты в `apps/backend/events/schemas.py`:**

```python
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime

class BaseEvent(BaseModel):
    event_id: UUID
    workspace_id: UUID
    brand_id: UUID
    occurred_at: datetime
    trace_id: str  # для OpenTelemetry

class PostDraftRequested(BaseEvent):
    type: Literal["post.draft_requested"] = "post.draft_requested"
    topic: str
    requested_by: UUID

class PostDraftGenerated(BaseEvent):
    type: Literal["post.draft_generated"] = "post.draft_generated"
    post_id: UUID
    agent: str
    model: str
    cost_usd: float          # внутренний учёт, в клиентский UI не уходит

class PostModerationFailed(BaseEvent):
    type: Literal["post.moderation_failed"] = "post.moderation_failed"
    post_id: UUID
    reason: str
    severity: Literal["block", "review"]

class PostPublished(BaseEvent):
    type: Literal["post.published"] = "post.published"
    post_id: UUID
    channel_id: UUID
    external_message_id: str

AgentEvent = Annotated[
    Union[PostDraftRequested, PostDraftGenerated, PostModerationFailed, PostPublished],
    Field(discriminator="type"),
]
```

> **Жёсткое правило (П6, D34):** в межагентных сообщениях запрещены `dict[str, Any]`, `Any`, нетипизированный JSON. Любая новая коммуникация = новый класс в `schemas.py` + миграция подписчиков.

**Dispatcher:**

```python
# apps/backend/core/event_bus.py
async def publish(event: AgentEvent) -> None:
    payload = event.model_dump_json()
    await redis.publish(f"events:{event.type}", payload)
    await audit_log.write(event)  # П5: event sourcing для AI

async def subscribe(types: list[str], handler):
    pubsub = redis.pubsub()
    await pubsub.subscribe(*(f"events:{t}" for t in types))
    async for message in pubsub.listen():
        event = AgentEvent.model_validate_json(message["data"])
        await handler(event)
```

**Когда мигрируем на Kafka:** при > 100 событий/сек на одном топике, или когда нужна история событий (replay) дольше 24 часов. На MVP — нет.

---

## 3. AI-фреймворк

### 3.1 Стратегия: тонкий слой LangChain + прямые вызовы API (D38)

**Подход:**

- **LangChain 0.3** используем для:
  - Вызова инструментов (tool / function calling) — единый интерфейс по разным вендорам
  - Структурированных ответов (`with_structured_output` + Pydantic)
- **LlamaIndex 0.12** — для:
  - Семантического индекса истории канала (RAG)
  - Embedding-пайплайнов
- **Прямые вызовы LLM API** через `LLMProvider` — там, где LangChain создаёт лишнюю прослойку (стриминг, простые completion'ы, embeddings)

### 3.2 LLMProvider абстракция (реализация D22 из `03`)

Единственная реализация на MVP — **PolzaProvider** (всё через polza.ai с OpenAI-совместимым интерфейсом).

```python
# apps/backend/adapters/llm/base.py
from typing import Protocol, AsyncIterator
from pydantic import BaseModel

class LLMResult(BaseModel):
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float           # внутренний учёт стоимости — в клиентский UI не уходит
    raw: dict                 # для аудит-лога

class LLMProvider(Protocol):
    async def complete(
        self, prompt: str, model: str, *,
        tools: list[dict] | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResult: ...

    async def embed(
        self, text: str | list[str], model: str
    ) -> list[list[float]]: ...

    async def stream(
        self, prompt: str, model: str
    ) -> AsyncIterator[str]: ...
```

```python
# apps/backend/adapters/llm/polza.py
class PolzaProvider:
    def __init__(self, api_key: str, base_url: str = "https://api.polza.ai/v1"):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    # ... OpenAI-совместимая реализация
```

> Поскольку polza.ai даёт OpenAI-совместимый интерфейс, код пишется как для OpenAI и при необходимости переключается на любой совместимый провайдер.

**Учёт стоимости (П12) — только во внутренней админке.** Все вызовы `complete / embed / stream` оборачиваются декоратором, который пишет в `agent_runs` строку `(workspace_id, brand_id, agent, model, prompt_tokens, completion_tokens, cost_usd, prompt_hash)`. Дашборд расходов виден **только в `/admin/*`**. **В клиентском UI расходомер не показывается** — у пользователя подписочная модель, без pay-per-use. Пользователь видит только лимиты своего тарифа в постах / AI-генерациях (фича F8b из `03`), а не токены и не рубли.

### 3.3 Модели по агентам

На MVP — 8 агентов (соответствует `04` §2). Media-агент работает «под капотом» как часть пайплайна Content и отдельным агентом не считается.

| Агент | Модель (через polza.ai) | Почему именно эта | Ориентир по цене (внутренний) |
| --- | --- | --- | --- |
| **Content** | **Claude Sonnet 4.6** | Лучшее качество длинных текстов, MarkdownV2-разметка, стилизация под тон голоса | ~$0.010–0.020 / пост |
| **Publisher** | GPT-4o-mini | Простые решения (когда публиковать, какое превью) — большой intelligence не нужен | ~$0.0005 / запуск |
| **Analyst** (базовый) | **Gemini 2.5 Pro** | Сильная аналитика длинных контекстов (вся история канала за неделю) | ~$0.008 / отчёт |
| **Orchestrator** | GPT-4o-mini | Маршрутизация и координация цепочек | ~$0.0002 / шаг |
| **Brand Memory** (базовый) | GPT-4o-mini | Экстракция паттернов из 50–200 постов, обновление JSON-профиля | ~$0.005 / refresh |
| **Onboarding** | **Claude Haiku 4.5** | Быстрый cold-start wizard + первичная экстракция Brand Memory (UX < 15 мин) | ~$0.003 / онбординг |
| **Moderation** (минимальный) | GPT-4o-mini | Быстрый LLM-судья поверх rule-based фильтра | ~$0.0003 / пост |
| **Notification** (базовый) | GPT-4o-mini | Дайджесты и формулировки уведомлений | ~$0.0001 / уведомление |
| **Media** *(под капотом Content)* | **Flux-2 Pro** / Nano Banana 2 | Качество визуала + цена 5 ₽/картинка (D13 из `03`) | 4.8–5 ₽ / изображение |
| **Embeddings (для RAG)** | text-embedding-3-small | Дёшево, хватает для брендового RAG | ~$0.0001 / 1K токенов |

> Цены — внутренний ориентир для технического планирования (видит только админ). В клиентском UI это не показывается.

> Конкретные имена моделей могут меняться с выходом новых версий — фиксируем семейство (Sonnet, Gemini Pro, Haiku, GPT-4o-mini, Flux-2 Pro), а не конкретный билд. Решение об апгрейде модели — через **A/B на агенте** с метрикой acceptance rate.

**Агенты после MVP (для справки, см. `04` §2):**

| Агент | Модель |
| --- | --- |
| Strategist | Gemini 2.5 Pro |
| Research | Gemini 2.5 Pro |
| Optimizer | Gemini 2.5 Pro |
| Monitor | GPT-4o-mini |
| Engagement | Claude Haiku 4.5 |
| Repurpose | Claude Haiku 4.5 |

### 3.4 Промпт-инфраструктура — skill-based (D68–D70) ⭐

**Архитектурный сдвиг:** монолитные Jinja-промпты заменяются на **skill-based-архитектуру** (см. `04` §19). Каждый агент использует `SkillCompiler.compile()` вместо `render_template('agent.jinja')`.

- **Skills** — модульные «знания» агента: `apps/backend/skills/<name>/SKILL.md`, YAML-заголовок + Markdown-тело
- **SkillRegistry** загружает все `SKILL.md` при старте FastAPI, валидирует через Pydantic-схему `SkillManifest`
- **SkillCompiler** на каждый LLM-вызов выбирает релевантные skills по условию `when_to_use` (DSL) и склеивает в system-промпт (progressive disclosure)
- **`agent_runs.skills_used` JSONB** + GIN-индекс — для bisect при регрессиях (вместо строки `prompt_version`)
- A/B-тесты — через версии skills (`sales-hooks-and-cta v2.1` vs `v2.2`) + Unleash-флаг для канареечного релиза
- **OutputContract** (Pydantic-схема ответа) — теперь часть skill `content-agent-base`, переиспользуется всеми content-агентами

**Новые зависимости в `pyproject.toml`:**

```toml
pydantic-yaml      = "^1.4"   # парсинг YAML frontmatter в SKILL.md
google-re2         = "^1.1"   # ReDoS-безопасный regex для DSL-оператора matches
python-frontmatter = "^1.1"   # разделение YAML/Markdown в SKILL.md
tiktoken           = "^0.7"   # оценка token budget для skills
```

**Новые CI-проверки:**

- `scripts/validate_skills.py` — Pydantic-валидация всех `apps/backend/skills/*/SKILL.md` на pre-commit
- `tests/skills/test_dsl.py` — unit-тесты DSL-операторов
- `tests/skills/test_static_analysis.py` — детекция «мёртвых» skills (skill не активируется ни в одном из 20 типичных контекстов → fail)
- `tests/skills/test_token_budget.py` — каждый skill ≤ `token_budget`, указанного в манифесте (фактический подсчёт через tiktoken)

### 3.5 ImageProvider (D40) — Media работает под капотом Content

**Абстракция:**

```python
# apps/backend/adapters/image/base.py
from typing import Protocol, Literal
from pydantic import BaseModel

class ImagePrompt(BaseModel):
    text: str
    aspect_ratio: Literal["1:1", "4:5", "16:9", "9:16"] = "1:1"
    style_hints: list[str] = []           # из Brand Memory → visual_guidelines
    negative_prompt: str | None = None

class ImageResult(BaseModel):
    url: str                               # ссылка на S3
    model: str
    cost_rub: float                        # внутренний учёт
    width: int
    height: int

class ImageProvider(Protocol):
    name: str
    async def generate(self, prompt: ImagePrompt) -> ImageResult: ...
```

**Реализации:**

- `Flux2ProProvider` — через polza.ai, дефолт для постов в новостных и экспертных каналах
- `NanoBananaProvider` — через polza.ai, для «лёгких» иллюстраций / соло-блогеров

**Логика выбора (внутри пайплайна Content):**

- По умолчанию — Flux-2 Pro (лучшее качество)
- При большом объёме генераций в рамках выбранного тарифа — Nano Banana 2 (для удержания себестоимости)
- Пользователь может вручную переключить в `Settings → Brand → Visual Guidelines`

### 3.6 Brand Memory: реализация (D33 из `04`)

Двухслойная архитектура — **Core Profile** (общая память бренда) + **Network Overlays** (адаптация под соцсеть). Привязана к **бренду** (не к каналу и не к workspace) — это позволяет клиенту вести несколько брендов параллельно с разными стилями.

**Хранение:**

| Слой | Где | Формат |
| --- | --- | --- |
| **Core Profile** — аудитория, тон голоса, табу, миссия | `brand_memory_core` (JSONB в Postgres) | Pydantic-схема `BrandCoreProfile` |
| **Network Overlay** — формат, длина, MarkdownV2-нюансы, времена публикации | `brand_memory_overlays` (JSONB + FK на channel) | `BrandNetworkOverlay` |
| **Эталонные посты** (few-shot примеры) | `brand_memory_examples` (FK на brand, embedding) | text + pgvector |
| **Семантический индекс истории канала** | `channel_memory_chunks` | pgvector |

**Доступ — только через сервис (П11 — Single Source of Truth):**

```python
# apps/backend/modules/brand_memory/service.py
class BrandMemoryService:
    async def get_for_agent(
        self, brand_id: UUID, network: Literal["tg", "ig", "yt"] | None = None
    ) -> AssembledBrandMemory: ...
```

Никаких локальных копий тона голоса в коде агентов не допускается.

**Что попадает в промпт:**

```jinja
Ты пишешь пост для канала "{{ channel.name }}".

# Core Profile
Аудитория: {{ bm.core.audience }}
Тон: {{ bm.core.tone_of_voice }}
Запрещённые темы: {{ bm.core.taboo }}

# Telegram Overlay
Оптимальная длина: {{ bm.overlay_tg.length_range }} символов
Стиль форматирования: {{ bm.overlay_tg.markdown_style }}

# Релевантные посты из истории этого канала (top-5 по similarity)
{% for post in retrieved_posts %}
- {{ post.content[:200] }} (просмотры: {{ post.views }})
{% endfor %}

# Похожие посты конкурентов (если включён мониторинг)
{% for post in retrieved_competitor_posts %}
- {{ post.content[:200] }} ({{ post.channel.name }})
{% endfor %}

# Запрос пользователя
{{ user_request }}

Сгенерируй пост в формате Telegram MarkdownV2.
Поддерживаемые сущности: bold (**text**), italic (__text__),
strike (~~text~~), code (`text`), spoiler (||text||), [links](url).
```

---

## 4. Авторизация — самописная (D36, реализация D28 из `04`)

> **Что выбираем:** **самописная авторизация на FastAPI**. Никаких сторонних библиотек типа `FastAPI-Users`, `Supabase Auth`, `Auth.js`, `Logto-as-SDK`. Это сознательное исключение из принципа «Buy > Build» (П7 из `04`) — авторизация слишком тесно сцеплена с RLS-контекстом, JWT-claims, `Membership` и `Workspace switch`, чтобы зависеть от чужой реализации.

### 4.1 Что входит в реализацию

| Компонент | Что используем |
| --- | --- |
| Базовая реализация | **Самописная** на FastAPI (без third-party auth-библиотек) |
| Хранение пользователей | PostgreSQL (та же БД, что и весь бэкенд) |
| Хеширование паролей | **Argon2id** (через `passlib[argon2]`) |
| Access-токен | JWT (HS256), TTL 15 минут |
| Refresh-токен | Случайный opaque-токен (32 байта), HttpOnly Secure cookie, TTL 30 дней, ротация при каждом refresh |
| Email + пароль | Обязательный способ входа на MVP |
| MFA | TOTP через `pyotp` (обязателен для admin-роли с первого дня; для обычных пользователей — опционален в Settings → Security) |
| OAuth (Google, Telegram Login Widget) | После MVP — через `httpx-oauth` |
| Email-верификация и восстановление пароля | Через UniSender Go (D39) |

### 4.2 Что хранится где

- **`users`** — основные данные (email, hashed_password, status, mfa_secret, created_at, ...)
- **`refresh_tokens`** — `(token_hash, user_id, jti, expires_at, revoked_at, user_agent, ip_hash)`. Один пользователь = много активных сессий (десктоп + телефон); revoke на конкретную сессию, а не на всех
- **`workspace_memberships`** — `(user_id, workspace_id, role)` — связь юзер ↔ workspace ↔ роль
- **`platform_roles`** — `user / admin / support` (см. матрицу доступа в `04` §18.2)
- **JWT хранит только** `user_id` / `platform_role` / `active_workspace_id` / `exp` / `jti` (D64 из `04` — JWT-strict-minimum)
- **Список workspace-memberships** живёт в Redis (`user:{id}:memberships`, TTL 5 мин) и подтягивается middleware при каждом запросе. Это снимает проблему JWT-bloat у агентств с десятками workspace'ов и даёт мгновенный revoke прав (`DEL` + WS-push)

### 4.3 Архитектура HTTP-флоу

```
┌────────────────────────────────────────────────────────┐
│  Браузер (Next.js)                                     │
│  - Access JWT — в памяти                               │
│  - Refresh-токен — в HttpOnly Secure cookie            │
└──────────────────────┬─────────────────────────────────┘
                       │ HTTPS
┌──────────────────────▼─────────────────────────────────┐
│  FastAPI Backend                                       │
│  - POST /v1/auth/login            → access + refresh   │
│  - POST /v1/auth/refresh          → новый access       │
│  - POST /v1/auth/logout           → отзыв refresh      │
│  - POST /v1/auth/register         → email + password   │
│  - POST /v1/auth/forgot-password  → email-link         │
│  - POST /v1/auth/reset-password   → новый пароль       │
│  - POST /v1/auth/verify           → подтверждение      │
│  - POST /v1/auth/mfa/setup        → QR-код TOTP        │
│  - POST /v1/auth/mfa/verify       → 6-значный код      │
│  - POST /v1/workspaces/{id}/switch → активный ws       │
└────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────┐
│  Middleware tenancy + RLS:                             │
│  - вычитывает active_workspace_id из JWT               │
│  - открывает транзакцию                                │
│  - SET LOCAL app.current_user_id = ...                 │
│  - SET LOCAL app.current_tenant_id = ...               │
│  - SET LOCAL app.platform_role = ...                   │
└────────────────────────────────────────────────────────┘
```

### 4.4 Минимальная реализация

```python
# apps/backend/core/auth/jwt.py
import jwt
from datetime import datetime, timedelta, timezone

def issue_access_token(user_id: UUID, platform_role: str, active_workspace_id: UUID | None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "pr": platform_role,
        "aws": str(active_workspace_id) if active_workspace_id else None,
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
```

```python
# apps/backend/core/auth/middleware.py
@app.middleware("http")
async def tenancy_middleware(request, call_next):
    user = await maybe_get_user_from_jwt(request)   # читает Authorization: Bearer ...
    if user is None:
        return await call_next(request)             # публичные эндпоинты

    ws_id = user.active_workspace_id
    memberships = await get_memberships_cached(user.id)  # Redis (D64)
    if ws_id and not any(m.workspace_id == ws_id for m in memberships):
        raise HTTPException(403, "Workspace access revoked")

    async with db.transaction() as conn:
        await conn.execute(
            "SET LOCAL app.current_user_id = $1;"
            "SET LOCAL app.current_tenant_id = $2;"
            "SET LOCAL app.platform_role = $3;",
            user.id, ws_id, user.platform_role,
        )
        request.state.user = user
        return await call_next(request)
```

```python
# apps/backend/core/auth/dependencies.py
async def current_user(request: Request) -> User:
    if not getattr(request.state, "user", None):
        raise HTTPException(401)
    return request.state.user

async def require_admin(user: User = Depends(current_user)) -> User:
    if user.platform_role != "admin":
        raise HTTPException(403)
    return user
```

### 4.5 Что отдельно тестируется

| Сценарий | Что проверяем |
| --- | --- |
| Login + refresh-ротация | Каждый refresh выдаёт новый refresh-токен, старый помечается `revoked_at` |
| Кража refresh (reuse) | Использование уже отозванного refresh → revoke **всей семьи** токенов этого пользователя + alert |
| Смена ролей | `DEL user:{id}:memberships` → WS-push `auth.refresh_required` → клиент дёргает `/v1/auth/refresh` |
| MFA для admin | Без TOTP-кода вход в `/v1/admin/*` невозможен |
| RLS escape | Тест: пользователь A пытается читать `posts` пользователя B — Postgres сам блокирует |
| `SET LOCAL` cleanup | После транзакции в пуле PgBouncer переменные `app.*` не утекают в следующий запрос |
| Длина JWT | Integration test: размер access-токена ≤ 2 KB |

### 4.6 Если в будущем решим уйти от своей реализации

Все вызовы из бизнес-кода идут через `app.core.auth.*` → миграция возможна без переписывания приложения.

| Опция | Когда имеет смысл |
| --- | --- |
| **Logto** (self-hosted) | Если потребуется enterprise SSO (SAML, SCIM) и нет ресурса допиливать своё |
| **Keycloak** | Сложный B2B с агентствами и SSO (год 2+) |
| **Clerk** | Если выйдем на международный рынок и нужен готовый UX |

---

## 5. Telegram-интеграция

> **Стандартное правило (применяется по всему стеку):** Telegram-интеграция разделена на **два сценария** с разными механизмами доступа.

### 5.1 Bot API через `aiogram 3.x` — для каналов пользователя

Для **всех каналов, в которых наш бот добавлен админом**, мы работаем через стандартный Telegram Bot API. Это легитимный путь по ToS, без серых зон.

**Почему `aiogram` 3.x:**

- Лучшая Python-библиотека для Telegram Bot API
- Async-first
- Type hints, Pydantic-модели
- FSM (для интерактивных бот-сценариев — например, approve flow в нативном TG-боте, фича S5 из `03`)
- Активное развитие, большое сообщество
- Поддержка **MarkdownV2** (D17 из `03`), HTML, всех типов сообщений

**Где используем Bot API:**

- Webhook на новые посты, реакции, комментарии в каналах пользователя
- Публикация постов (Publisher Agent)
- Получение метрик (количество подписчиков, просмотры через `getMessage*`) — Analyst Agent
- Нативный TG-бот для approve-flow: черновик готов → inline-кнопки **Approve / Edit / Reject** прямо в Telegram (без email, см. `04` §8.6.1)

### 5.2 user-bot с `api_id` + `api_hash` + ротация — для чтения публичных каналов конкурентов

Bot API **не позволяет** читать историю каналов, где наш бот не админ. Это нужно для:

- A7 — Research Agent (после MVP): анализ контента и тем конкурентов
- L1 — Inspiration Board: лента референсных постов из выбранных пользователем публичных каналов
- Monitor Agent (после MVP): тренды и упоминания в публичных каналах

**Стек:**

- **Pyrogram** — Python-клиент MTProto, лучше по количеству выбранных методов, async-friendly, хорошо документирован, комьюнити активное
- **`api_id` + `api_hash`** — это легитимные ключи приложения Telegram, выдаются на `my.telegram.org`
- **Пул аккаунтов user-bot + ротация** — не один аккаунт на весь продукт, а пул, между которыми распределяются запросы. Это снижает риск rate-limit/блокировки и распределяет нагрузку
- **Сессии** аккаунтов хранятся **зашифрованными** в БД (`telegram_userbot_sessions`, encrypted_at_rest), ключ — из секрет-менеджера
- **Использование только для чтения** публичных каналов (никаких приватных групп, никаких личных переписок, никакой публикации через user-bot)
- **Дедупликация через Global Channel Registry** (D20 из `03`) — один и тот же публичный канал читается одним user-bot для всех тенантов, результаты шарятся

**Прокси-пул** (через провайдер типа Bright Data) — после MVP, на MVP без прокси.

### 5.3 Где живёт код Telegram

```
apps/backend/adapters/telegram/
├── bot_api/          # aiogram 3.x — для каналов пользователя
│   ├── client.py
│   ├── publisher.py
│   ├── webhook.py
│   └── approve_flow.py    # FSM для inline-кнопок approve
└── userbot/          # Pyrogram — для чтения публичных каналов
    ├── pool.py            # пул аккаунтов + ротация
    ├── reader.py          # чтение публичных каналов
    └── session_store.py   # зашифрованные сессии в БД
```

---

## 6. Frontend

### 6.1 Next.js 15 + React 19

**Почему Next.js:**

- App Router + Server Components — современный подход
- Хорошо ложится на SaaS-дашборд
- Огромная DX, много примеров
- Деплоится как Docker-образ рядом с backend (D37)

**Структура `apps/web/`** — см. §2.1.2: внутри одного Next.js-приложения живут роут-группы `(public)/`, `(auth)/`, `(app)/` и `(admin)/`. Отдельного приложения под админку нет — это тот же кодовый базис и деплой.

### 6.2 shadcn/ui + Tailwind CSS 4

**Почему:**

- Полный контроль над кодом компонентов (это не библиотека, а скаффолдинг — компоненты копируются к нам в репо и дальше живут своей жизнью)
- Tailwind v4 — быстрый, минимальный билд (новый Oxide-движок на Rust)
- Идеально для брендинга и темизации в будущем (B2B-тариф для агентств)
- Огромное сообщество, готовые блоки (shadcn-blocks, magicui)

### 6.3 TanStack Query v5 + Zustand

- **TanStack Query** — серверное состояние (запросы к API, кэш, инвалидация). Все мутации — optimistic update + откат при ошибке
- **Zustand** — локальное UI-состояние (открытые модалки, drag-and-drop черновиков, фильтры)
- ❌ **НЕ** используем Redux Toolkit — overhead для нашего масштаба

### 6.3.1 Виртуализация длинных списков — `@tanstack/react-virtual` ⭐

Все списки длиннее 200 строк обязаны быть виртуализированы — рендерим только видимые ряды:

| Где | Зачем |
| --- | --- |
| Admin `/admin/llm-calls` (M3 из `03`) | Миллионы LLM-вызовов, не рендерим ничего вне viewport |
| Admin `/admin/agent-runs` | Десятки тысяч запусков пайплайнов |
| Inspiration Board (EPIC-L, L1) | 10K+ карточек постов конкурентов |
| Календарь публикаций (EPIC-C, C6) | > 200 запланированных слотов в месяц при multi-brand |
| Очередь публикаций (C6.1) и `/audit` | Длинные таблицы операций |

Пакет: `@tanstack/react-virtual@^3`. Используем через хук `useVirtualizer` + поддержка variable row height (карточки разной высоты в Inspiration Board). Совместим с TanStack Query — infinite scroll через `useInfiniteQuery`.

### 6.4 Tiptap 2 — WYSIWYG-редактор (B6 из `03`, D17)

Идеален для нашего MarkdownV2-редактора:

- Headless (полный контроль над UI)
- Расширяемый через extensions
- Поддерживает всё, что нужно для TG MarkdownV2
- Можно экспортировать в любой формат через сериализатор

**Кастомные расширения:**

- **Spoiler** (TG-специфичный) — рендер как `<tg-spoiler>`, экспорт `||text||`
- **MarkdownV2 Serializer** — экспорт документа в TG MarkdownV2 (с правильным экранированием `_ * [ ] ( ) ~ \` > # + - = | { } . !`)
- **MarkdownV2 Parser** — импорт черновиков от Content-агента (B7 из `03`)
- **TG Preview Mode** — рендер «как увидит подписчик» с TG-стилизацией

### 6.5 react-hook-form + zod

- Лучший паттерн для форм в React
- Совместим с shadcn/ui (`<FormField>`)
- zod-схемы используются и на фронте (валидация формы), и **синхронизируются** с backend Pydantic-схемами через генератор `pydantic-to-zod` (часть CI)

### 6.6 Real-time (D43, П9)

**WebSocket — основной транспорт:**

- FastAPI native WS (`websocket` route)
- Авторизация — access JWT в первом сообщении
- Подписка на события `events:post.draft_generated`, `events:post.published`, `events:notification.created` — фильтрация по `workspace_id`
- На клиенте — кастомный хук `useRealtime("post.draft_generated", postId)` поверх TanStack Query: пришло событие → инвалидируется кэш → UI обновляется

**SSE — резерв:**

- Если WS не подключается за 3 секунды (корпоративный прокси) → автоматический фолбэк на `EventSource`
- Тот же event bus, тот же контракт

### 6.7 Графики: Recharts

- Хватает для дашборда канала на MVP
- Легко стилизуется под shadcn/ui
- Если понадобится сложная аналитика (тепловые карты, sankey) → ECharts (после MVP)

---

## 7. Платежи (D21 из `03`)

> Любой биллинг идёт через единый интерфейс `PaymentProvider`. Сменить провайдера или добавить нового можно без переписывания бизнес-логики. См. также `04` §10.

### 7.1 ЮKassa

- **SDK:** `yookassa-sdk-python` (официальный)
- **Webhook handler:** FastAPI-эндпоинт, валидация подписи + `Idempotency-Key` (П13)
- **Подписки:** через автоплатежи на сохранённый платёжный метод

### 7.2 bepaid.by

- **REST API напрямую** (нет официального Python SDK) — пишем тонкий клиент в `apps/backend/adapters/payment/bepaid.py`
- Тестовый sandbox для разработки
- Поддерживает БелКарт и рассрочки локальных банков

### 7.3 Абстракция `PaymentProvider`

```python
# apps/backend/adapters/payment/base.py
class PaymentProvider(Protocol):
    name: str
    async def create_subscription(self, ...) -> SubscriptionResult: ...
    async def charge(self, ...) -> ChargeResult: ...
    async def cancel(self, ...) -> None: ...
    async def webhook_handler(self, request) -> WebhookResult: ...

# Роутинг по поддерживаемой валюте / банку пользователя
class PaymentRouter:
    def get_provider(self, payment_context: PaymentContext) -> PaymentProvider:
        # выбор конкретного провайдера — по совместимости с картой / валютой
        ...
```

Подключение новых провайдеров (Stripe, ЮMoney, CloudPayments) — это новый класс в `apps/backend/adapters/payment/` без изменения `apps/backend/modules/billing/`.

### 7.4 Биллинг-модель

- **Подписки** с автосписанием
- **Free trial** 7 дней (без карты на MVP, с картой — после MVP)
- **Refund-окно** 14 дней (по требованию законов о защите прав потребителей)
- **Учёт LLM-токенов и Media-картинок — внутренний.** Пользователь платит фиксированную подписку; внутри мы сами следим за себестоимостью через `agent_runs` + `image_generations` (П12)
- **F8 и F8b разделены** (см. `03` §EPIC-F):
  - **F8** — внутренний `CostGuardian`: автоматические алерты администратору при превышении технического порога LLM-расходов на бренд, автоматический даунгрейд модели LLM, пауза новых LLM-генераций для бренда, kill-switch автопилота (full-auto) с переходом в режим «человек утверждает». Пользователю не видно.
  - **F8b** — пользовательские лимиты тарифа: бесшумные уведомления о потреблении (60% / 80%), при 100% — блокируется именно та фича, по которой исчерпан лимит (например, новые AI-генерации). Уже сгенерированные и запланированные посты — продолжают публиковаться по расписанию

---

## 8. Object Storage

| Что храним | Где |
| --- | --- |
| **Картинки постов** (Media — часть Content) | S3-совместимое хранилище у хостера |
| Аватары пользователей и логотипы каналов | Там же |
| Экспорты отчётов (PDF в будущем) | Там же |
| Бэкапы БД (`pg_dump` ежедневно, retention 14 дней) | **Backblaze B2** (отдельная зона для надёжности) |

**Структура бакетов:**

- `assets` — публичные (картинки постов, аватары). Раздаются напрямую с S3 + nginx-кэш
- `backups` — приватные, lifecycle: переход в холодное хранение через 7 дней
- `exports` — приватные, presigned URLs с TTL 1 час

---

## 9. Email: UniSender Go (D39)

**UniSender Go** ([godocs.unisender.ru](https://godocs.unisender.ru)) — транзакционный email-API:

- Хорошая deliverability в Yandex / Mail.ru
- Простой REST API + SMTP
- Webhooks: доставка, открытие, отписка, bounce
- Free tier 1000 писем/мес → дальше ~₽0.30 за письмо
- Отдельные домены/IP для лучшей репутации

**Архитектура:**

```python
# apps/backend/adapters/email/base.py
class EmailProvider(Protocol):
    async def send(self, to: str, template_id: str, data: dict) -> EmailResult: ...

# apps/backend/adapters/email/unisender_go.py
class UniSenderGoProvider:
    async def send(self, to, template_id, data):
        # POST https://go1.unisender.ru/ru/transactional/api/v1/email/send.json
        ...
```

**Шаблоны** хранятся в админке UniSender Go (быстрее редактировать без релиза). В коде ссылаемся только по `template_id`.

**Транзакционные письма, которые шлём (только редкие / нужные):**

- Email-верификация при регистрации
- Восстановление пароля
- Биллинг — J5 (счета, напоминание о продлении, неудачное списание)
- Еженедельный отчёт от Analyst-агента — J6
- Аварийные системные письма (блокировка аккаунта, kill-switch)

**❌ НЕ через email** (чтобы юзер не отписался от перегруженной почты — `03` EPIC-J, `04` §8.6.1):

- ❌ «Черновик поста готов» (J1) → **только Telegram-бот** (inline-кнопки Approve / Edit / Reject)
- ❌ «Требуется approve» (J2) → **только Telegram-бот** (preview + кнопки)
- ❌ Алерты модерации (J3) и статусы публикаций (J4) → Telegram-бот

**На случай переезда:** Resend, SendPulse, Mailganer — все интегрируются через ту же абстракцию `EmailProvider`.

---

## 10. Observability (наблюдаемость)

### 10.1 Sentry — ошибки

- Free tier 5K events/мес — хватает на MVP
- Sentry SDK для Python (FastAPI integration) и Next.js
- Авто-tracking ошибок + источник + breadcrumbs + performance
- К каждому событию привязываются `workspace_id`, `brand_id`, `agent` (П12)

### 10.2 OpenTelemetry — трейсы + метрики

- OTel Python SDK (instrumentations для FastAPI, SQLAlchemy, Celery, Redis) + OTel JS SDK
- Экспорт в **Grafana Tempo** (трейсы) и **Prometheus** (метрики)
- Self-hosted Grafana stack поднимается в своём Docker-контейнере (локально — через `docker-compose.dev.yml`, в будущем на проде — рядом с backend)
- **Trace propagation** через шину событий — каждое событие несёт `trace_id`, чтобы можно было проследить полный путь: «UI-клик → API → enqueue → агент → publish»

### 10.3 PostHog — продуктовая аналитика

**Self-hosted PostHog** в своём Docker-контейнере (локально — через `docker-compose.dev.yml`):

- Бесплатно (open source)
- Все данные у нас (важно для приватности пользователей)
- Funnel analysis, retention, A/B-тесты, session replay (опционально)

**Что меряем (Key Metrics из `01-product-vision.md`):**

- **TTFAA** (Time To First Autonomous Action) — целевой < 2 часов (Acceptance Criteria из `03`)
- Activation rate (регистрация → первый опубликованный пост)
- **NSM:** Autonomous Actions per Active Brand per Week
- **Headline KPI:** % Brand Operations Automated
- **AI Acceptance Rate** (по агентам)
- Retention: D7, D30, D90
- Feature adoption: Brand Memory completion, multi-brand usage

### 10.4 AI Audit Log (П5, П12 — реализация из `04`)

Отдельная таблица `agent_runs` (полная схема — в `04`):

| Колонка | Тип |
| --- | --- |
| `id` | UUID |
| `workspace_id` / `brand_id` / `agent` / `model` | UUID, str |
| `prompt_hash` / `prompt_full` | str / text |
| `tools_called` | JSONB |
| `chain_of_thought` | text (если модель вернула reasoning) |
| `raw_output` | JSONB |
| `prompt_tokens` / `completion_tokens` / `cost_usd` | int / int / numeric |
| `latency_ms` | int |
| `accepted_by_user` | bool / null |
| `skills_used` ⭐ | JSONB (для D68–D70) |
| `created_at` | TIMESTAMPTZ |

**UI «Объяснимость»:** для каждого сгенерированного поста / отчёта юзер может раскрыть **что увидит сам пользователь:**

- Какой агент это сделал и какая модель
- Какой prompt был отправлен (с retrieved context — Brand Memory, история, конкуренты)
- Какой ответ модели был получен
- Какие skills были применены (для skill-based архитектуры)

**LLM-токены и стоимость в этом UI пользователю НЕ показываем** — у пользователя подписочная модель, без pay-per-use. Эти поля видны только в `/admin/*`.

### 10.5 Cost dashboard (П12) — internal-only ⭐

**Виден только во внутренней админ-панели** `/admin/*`. У пользователя в UI расходомера нет:

- Cost per `(brand × agent × month)` — для фаундера / ops
- Token consumption по моделям
- Картинки по провайдерам (Flux-2 Pro vs Nano Banana)
- Алерты в TG-бот фаундера при cost-spike (правила в `04` §17.6)
- Автоматический даунгрейд модели / пауза LLM-генераций для бренда / kill-switch автопилота — по логике F8 (см. `03` §EPIC-F и §7.4 здесь)

---

## 11. CI / CD

### 11.1 GitHub Actions

**Pipeline:**

```yaml
on: pull_request:
  - lint            # ruff + biome
  - typecheck       # mypy (strict) + tsc
  - test-backend    # pytest + pytest-asyncio + pytest-postgresql
  - test-frontend   # Vitest
  - build           # docker images
  - openapi-diff    # ломает PR, если ломают API-контракт без миграции клиента
  - pydantic-to-zod # синхронизация схем backend ↔ frontend
  - validate-skills # SKILL.md → SkillManifest valid?
  - skill-token-budget # каждый skill ≤ token_budget

on: push to main:
  - всё из pull_request
  - deploy to staging   # через Ansible
  - run E2E (Playwright)
  - notify Telegram-канал команды

on: tag v*.*.*:
  - require manual approval (environment: production)
  - deploy to production
  - run smoke tests
```

### 11.2 Docker

- Один `Dockerfile` на backend (multi-stage build, финальный образ ~120 MB)
- Один `Dockerfile` на frontend (Next.js standalone output)
- `docker-compose.yml` — для production-деплоя (backend + worker + beat + frontend + nginx)
- `docker-compose.dev.yml` — для локальной разработки (Postgres + Redis + MailHog + PostHog + Unleash)

### 11.3 Provisioning серверов

- **На локальной разработке:** всё работает в Docker Compose (Postgres, Redis, Nginx, backend, web) — provisioning не нужен
- **При переходе на боевой хостинг:** Ansible playbooks для установки / конфигурации сервера (Postgres, Redis, Nginx, Docker, certbot/Let's Encrypt). Сам хостинг / домен / SSL выбираем позже
- **Долгосрочно:** Terraform — если перейдём на облачную инфраструктуру с автоматическим масштабированием

### 11.4 Секреты

- **На MVP** — encrypted env-файлы в репозитории через `sops` + `age`-ключи у разработчиков
- **После MVP** — Hashicorp Vault или его аналог (когда мигрируем на Kubernetes)
- В коде секреты загружаются через Pydantic Settings (`apps/backend/core/config.py`); **никаких `os.getenv`** напрямую в бизнес-коде

---

## 12. Тесты

### 12.1 Backend

- **pytest + pytest-asyncio** — основа
- **factory-boy + Faker** — фикстуры моделей
- **httpx AsyncClient** — интеграционные тесты API (реальная БД + моки внешних сервисов)
- **pytest-postgresql** — изолированная БД на тест (template + per-test database)
- **respx / pytest-httpx** — мокинг внешних API (polza.ai, ЮKassa, UniSender Go, Telegram Bot API, bepaid)
- **pytest-celery** — тесты воркеров в eager-режиме
- **fakeredis** — для unit-тестов event bus

**Структура:**

- **Unit** — на сервисный слой + агентов (моки `LLMProvider` / `ImageProvider`)
- **Integration** — на API-эндпоинты (реальная БД + моки внешних)
- **Event-driven** — пайплайны через шину событий (`publish` → `subscribe` → проверка БД)
- **E2E** — несколько критических сценариев (signup → подключить канал → онбординг → publish → analytics)

### 12.2 Frontend

- **Vitest** — unit и component-тесты
- **Testing Library** — для рендера компонентов
- **Playwright** — E2E на критические сценарии (signup → онбординг → первый пост → publish)
- **MSW (Mock Service Worker)** — мокинг API в компонент-тестах
- **Storybook** (после MVP) — для UI-компонентов

### 12.3 Целевое покрытие на MVP

- **Критический путь** (авторизация, биллинг, создание поста, модерация, публикация): **80%+**
- Общее покрытие: **60%+**
- 90% покрытия — не цель. Это ловушка: время уходит, ценность падает

---

## 13. Локальная разработка (DX)

**Цель:** новый разработчик запускает всё локально за **≤ 30 минут**.

```bash
# 1. Клон репо
git clone git@github.com:alexeimozol227/social-media.git && cd social-media

# 2. Зависимости
make install              # uv sync (backend) + pnpm install (frontend)

# 3. Локальная инфра через Docker Compose
docker compose -f docker-compose.dev.yml up -d
# поднимает: Postgres 16, Redis 7, MailHog (UI для писем),
#            PostHog, Unleash (feature flags), MinIO (S3-совместимое)

# 4. Миграции
make migrate

# 5. Сиды (тестовый пользователь, workspace, brand, BM)
make seed

# 6. Запуск
make dev   # параллельно: uvicorn (backend) + next dev (frontend)
           # + celery worker + celery beat (через honcho / foreman)
```

**Дополнительно:**

- `make test` / `make lint` / `make format` / `make typecheck`
- `.env.example` со всеми ключами (заглушки для polza.ai, ЮKassa, UniSender Go, Telegram Bot Token, S3)
- **Pre-commit hooks:** ruff, biome, mypy, `detect-secrets`, `pydantic-to-zod` sync, `validate_skills`
- **Direnv** (`.envrc`) для автоматической загрузки env
- **Devbox / mise** (опц.) — пин версий Python 3.12 / Node 20 / uv / pnpm

**Что НЕ нужно для запуска:**

- Реальный polza.ai ключ — есть мок-провайдер с фикстурами в `tests/fixtures/llm_responses/`
- Реальный Telegram Bot Token — есть `MockTelegramAdapter`
- Реальный ЮKassa-аккаунт — есть sandbox + respx-фикстуры
- Реальные `api_id` / `api_hash` user-bot'а — для разработки используется фикстурный набор публичных каналов из `tests/fixtures/userbot/`

---

## 14. Менеджеры пакетов

### Python: **uv**

- В 10–100 раз быстрее pip/poetry
- Lockfile `uv.lock`
- Активно развивается (Astral, авторы Ruff)
- Совместим с PEP-517, может заменить poetry/pip

### JavaScript: **pnpm**

- Быстрее npm/yarn
- Эффективное использование диска (через символические ссылки)
- Workspace для возможного monorepo (web + admin + landing) — на MVP только один пакет `web/`

---

## 15. Стоимость стека на MVP (только переменные расходы)

> **Разработка ведётся локально** (Docker Compose на машине разработчика). Хостинг, VPS, домен и SSL покупаются позже — когда продукт будет готов к выкатке; в этой документации их стоимость не фиксируем. Ниже — только переменные расходы, которые появляются при работе с внешними API. Никаких прогнозов выручки, тарифов и unit-economics здесь нет — они живут в `07-monetization.md`.

| Компонент | Стоимость |
| --- | --- |
| Sentry | $0 (free tier 5K events) |
| UniSender Go | $0 (free tier 1000 писем) |
| PostHog self-hosted | $0 (в собственном Docker-контейнере) |
| Unleash self-hosted | $0 (в собственном Docker-контейнере) |
| Самописная авторизация | $0 (часть backend) |
| polza.ai LLM | пропорционально активности пользователей |
| polza.ai Image (Flux-2 Pro / Nano Banana) | пропорционально количеству генераций |
| GitHub Actions | $0 (2000 минут free) |

На стадии локальной разработки реальные деньги уходят только на polza.ai (LLM + Image) при реальных вызовах API во время разработки и тестирования. Остальное — в free tier или в своём Docker.

---

## 16. Что НЕ берём (анти-стек)

| Технология | Почему |
| --- | --- |
| **FastAPI-Users / Supabase Auth / Auth.js** | Нам нужен полный контроль над user-моделью, JWT-claims, refresh-логикой, RLS-контекстом и `Membership`. Авторизацию пишем сами (D36) |
| **Django** | Тяжелее FastAPI для async-heavy продукта |
| **MongoDB** | Postgres покрывает всё; multi-tenant с RLS на Postgres лучше; pgvector закрывает векторку (D29) |
| **Kubernetes** | Overkill для MVP (Docker Compose хватает) |
| **Kafka** | Redis Pub/Sub достаточно — до > 100 событий/сек на топике (D32, D41) |
| **Microservices** | Модульный монолит проще на старте — D27 из `04` |
| **GraphQL** | OpenAPI + REST + autogen-client покрывают всё; GraphQL — лишний overhead |
| **Vue / Svelte** | React выигрывает по найму и экосистеме |
| **CSS-in-JS / styled-components** | Tailwind 4 быстрее и проще |
| **Redux / MobX** | TanStack Query + Zustand покрывает всё |
| **HTML-парсер `t.me/s/{channel}`** | Хрупко и неполно. Чтение публичных каналов делаем через user-bot (`api_id` + `api_hash` + ротация) |
| **Vercel / Cloudflare Pages / Cloudflare (DNS / CDN / R2 / WAF)** | Нам нужен полный контроль над инфраструктурой без vendor lock-in. Статика раздаётся nginx + S3-совместимым объектным хранилищем. См. D37 |
| **Heavy WYSIWYG (Lexical)** | Tiptap 2 покрывает 100% наших нужд проще |
| **`react-window` / `react-virtuoso`** | `@tanstack/react-virtual` v3 — в той же экосистеме, что Query/Form, и легче API |
| **Pay-per-use расходомер в клиентском UI** | Подписочная модель (см. `07`). Cost dashboard — **только в `/admin/*`** |
| **`dict[str, Any]` в межагентных сообщениях** | Запрещено П6, D34 — только Pydantic discriminated unions |
| **Long-polling в UI** | Запрещено П9 — только WebSocket / SSE (D43) |
| **Своя реализация feature flags / kill-switch** | Unleash покрывает (П10, D42) |
| **IP-whitelist в auth** | Не используем (см. `03` F6) — оставляем обязательный MFA, без привязки к IP |

---

## 17. Связанные документы

- `01-product-vision.md` — общее видение, NSM, инварианты I1–I17
- `02-target-audience.md` — ICP, персоны Анна / Денис / Мария, JTBD
- `03-feature-scope.md` — MoSCoW-границы MVP, EPIC-A..M, D12–D25
- `04-architecture.md` — архитектура, принципы П1–П13, D26–D34, D56–D70
- `06-roadmap.md` — поэтапный roadmap
- `07-monetization.md` — тарифы, unit-economics


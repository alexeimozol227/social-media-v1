# Архитектура платформы

> **Документ:** `04-architecture.md`
> **Статус:** v2.5
> **Автор:** Мозоль Алексей
> **Дата обновления:** 2026-05-12
> **Зачем этот документ:** Описать архитектуру так, чтобы при добавлении новых соцсетей, агентов, тарифов и команд **не пришлось переписывать систему** — только подключать новые модули.

---

## 0. Если коротко (one-pager)

| Поле | Значение |
| --- | --- |
| **Стиль** | Модульный монолит (всё в одном репо, но логически разбит на модули). На микросервисы переходим только когда упрёмся в нагрузку |
| **Backend** | Python (FastAPI) — потому что лучший AI-стек (LangChain, LlamaIndex и т.д.) |
| **БД** | Postgres + pgvector (вектора храним там же, не отдельный Vector DB) |
| **Кэш / очереди / шина событий** | Redis (очереди задач, кэш, Pub/Sub) |
| **Файлы (картинки, медиа)** | Объектное S3-совместимое хранилище |
| **Изоляция данных пользователей** | Многоарендность (multi-tenancy) с первого дня + Postgres Row-Level Security |
| **Авторизация** | **Самописная** (JWT + refresh, email/password, MFA). Без сторонних библиотек типа FastAPI-Users — нужен полный контроль |
| **Агенты на MVP** | **8 агентов:** Content, Publisher, Analyst, Orchestrator, Brand Memory, Onboarding, Moderation, Notification. Media-агент работает «под капотом» как часть пайплайна Content. Остальные 7 — после MVP |
| **Внешние сервисы** | LLM, картинки, платежи, соцсети — **через единые интерфейсы** (`LLMProvider`, `ImageProvider`, `PaymentProvider`, `SocialChannel`). Сменить поставщика — не переписывать бизнес-логику |

---

## 1. ⭐ Зафиксированные решения

| # | Решение | Что это значит простыми словами |
| --- | --- | --- |
| D26 | **Оркестратор задач** | Очередь на базе Redis. На Python — `arq` (async-native) или `Celery` (зрелая). Финальный выбор — в `05-tech-stack.md` |
| D27 | **Архитектурный стиль** | Модульный монолит (один кодбэйз, модульная структура). Микросервисы — год 2+, когда упрёмся в нагрузку |
| D28 | **Авторизация** | **Самописная** (не FastAPI-Users и не другие готовые библиотеки): JWT + refresh tokens, email/password, привязка к нашей таблице `User`/`Membership`. Полный контроль над потоком |
| D29 | **Векторная БД** | pgvector внутри Postgres (одна БД, проще в эксплуатации, не нужно держать отдельный Qdrant/Weaviate) |
| D30 | **Хостинг** | Один регион. Без мульти-региональных схем |
| D31 | **Backend язык** | Python (FastAPI) — лучший AI-стек (LangChain, LlamaIndex, LLM-библиотеки) |
| D32 | **Шина событий между агентами** | Внутренний event bus через Redis Pub/Sub + Pydantic-схемы. Kafka — только когда упрёмся в нагрузку |
| D33 | **Структура Brand Memory** | Двухслойная: Core Profile (общий для всех соцсетей бренда) + Network Overlays (TG, IG, YT — настройки под конкретную соцсеть). Привязана к бренду, не к каналу |
| D34 | **Сообщения между агентами** | Все межагентные сообщения — **строго типизированные** Pydantic-объекты. «Свободный JSON» запрещён — иначе теряем предсказуемость |
| D35 | **Платформенные роли** | На MVP — `admin` / `support` / `user` (в колонке `users.platform_role`). Для `admin` и `support` обязателен MFA. См. §17.2 |
| D36 | **Хранилище файлов и CDN** | Объектное S3-совместимое хранилище у того же хостера. Статика — раздаётся напрямую с nginx + S3 public bucket с длинными cache-заголовками. Внешний CDN на MVP не используем |
| D37 | **Стоимость LLM — только внутренняя метрика** | Дашборды по стоимости LLM (`/admin/llm-calls`, `/admin/tenants/{id}`) — **только для `admin` и `support`**. У пользователя — подписочная модель, в его UI нет счётчиков «сколько потрачено LLM-токенов» |
| D38 | **Куда уходят уведомления** | Оперативные уведомления (черновик готов, нужен approve, статус публикации) — **только в Telegram-бот**. Email — только для биллинга и еженедельных отчётов. См. §8.6.1 |
| D56 | **Auto-rules UI** | **MVP** — только готовые preset-чекбоксы (10–15 правил, прописаны в коде). **Post-MVP (v1.2)** — естественный язык, но через отдельный компилятор: пользовательский текст превращается в DSL и исполняется детерминированно. Свободный текст «как есть» в проде запрещён — иначе AI может «по-своему» истолковать правило |
| D57 | **Сроки хранения данных** | `agent_runs.chain_of_thought` + `retrieved_context` — 30 дней «горячо», далее обрезаем. `llm_calls` — 90 дней + дневной агрегат навсегда. `channel_posts` — 180 дней + 24 мес «холодно» в S3. `audit_log` — 2 года «горячо» + 5 лет в архиве. `notifications` — 30 дней. Использование данных для обучения собственных моделей — **по явному согласию пользователя**, по умолчанию выключено. См. §18.5 |
| D58 | **Очистка входа LLM от инъекций** | Перед тем как подать что-либо из публичных каналов / комментариев в LLM: (1) URL → `[URL_REDACTED]`, телефон → `[PHONE_REDACTED]`, (2) `bleach` + список prompt-injection паттернов («ignore previous instructions», «system:», zero-width символы), (3) allow-list URL — только домены из Channel Registry, (4) исходящий контент дополнительно проверяется Moderation-агентом. См. §17.4 |
| D59 | **Cost Guardian — внутренний контроль LLM** | Внутренний компонент (не отдельный из 8 MVP-агентов). Если средняя стоимость поста по бренду растёт: дорого → алерт админу; дороже → автоматический перевод на более лёгкую модель LLM на 24 часа; ещё дороже → **пауза на новые LLM-генерации для бренда на час** (именно генерации — деньги тратит LLM, а не публикация уже готовых постов); критично дорого → **выключение режима «AI делает сам»** (бренд переходит в «человек утверждает»). См. §16.6. *Это не то же самое, что лимиты тарифа пользователя — см. F8b в `03` и §16.7.* |
| D60 | **Кэш картинок — только внутри бренда** | Ключ кэша: `(brand_id, sha256(style_prompt + size + seed_lora))`. **Шаринг между брендами запрещён** (один бренд не должен случайно увидеть картинку другого). TTL 30 дней. Сбрасывается при изменении визуального блока Brand Memory. См. §13.5 |
| D61 | **Партиционирование embeddings + HNSW** | `channel_post_embeddings` помесячно партиционируем через `pg_partman` с шаблон-таблицей, где **уже созданы все индексы** (включая HNSW). pg_partman копирует индексы на каждую новую партицию автоматически. Плюс `pg_cron` job 25-го числа проверяет, что HNSW есть в партиции на следующий месяц — иначе алерт. См. §18.6 |
| D62 | **Типизированные ошибки API → UI** | Все ошибки агентов / LLM — строго типизированные Pydantic-схемы с кодами: `LLMBudgetExceeded` (429), `ModelTimeout` (504), `CircuitBreakerOpen` (503), `ModerationBlocked` (422). Frontend ловит по коду → показывает понятный toast с CTA. **Никаких тихих сбоев** — статус каждой задачи виден в `/dashboard/agent-runs`. См. §17.5 |
| D63 | **Локаль агентов** | System prompt LLM **всегда на английском** (Claude/GPT лучше токенизируют, дешевле, точнее следуют инструкциям). Язык генерируемого поста — `brand.content_language` (язык канала). Уведомления пользователю — `user.locale`. См. §18.2.1 |
| D64 | **JWT — только минимум полей + Redis для членств** | В JWT хранится только: `user_id`, `platform_role`, `active_workspace_id`, `exp`, `jti`. Список workspace'ов пользователя — в Redis (TTL 5 мин). При смене ролей: `DEL` + WebSocket-push клиенту. Защита от JWT-bloat у агентств с сотнями workspace'ов, плюс мгновенный revoke. См. §17.6 |
| D65 | **PgBouncer + RLS — transaction pooling** | Connection pool в режиме `transaction` (не `session`). FastAPI dependency-обёртка: каждый HTTP-request = одна транзакция, начинается с `SET LOCAL app.current_tenant_id = ...; SET LOCAL app.current_user_id = ...;`. `SET LOCAL` гасится при commit — соединение возвращается в пул чистым. `SET` без `LOCAL` запрещён — CI-линтер проверяет. См. §18.7 |
| D66 | **Жёсткий потолок расходов на бренд — внутренний** | Технический предохранитель: чтобы один бренд не «съел» больше определённой доли ресурсов. Это **внутренний CostGuardian** (для админа), не пользовательский лимит тарифа. Пользовательские лимиты тарифа (сколько постов / AI-генераций в месяц можно по подписке) — отдельная фича F8b в `03`, см. §16.7 |
| D67 | **Обучение собственных моделей — только с согласия** | По умолчанию данные пользователя **не используются** для обучения / fine-tuning наших моделей. В Settings → Privacy есть явный чекбокс «Разрешить использовать мои анонимизированные данные для улучшения AI». Без согласия — данные удаляются по retention (D57). С согласия — могут пережить retention в анонимизированной форме |
| D68 | **Skill-based архитектура агентов** | Агенты **не используют** монолитные большие system-промпты. Знание агента разбито на **skills** (`app/skills/<name>/SKILL.md`) — переиспользуемые модули с YAML-заголовком + Markdown-телом. На каждый LLM-вызов компилируется **минимально необходимый набор skills** под текущий контекст. Экономия ~28–30% input tokens + автоматическое версионирование промптов через `agent_runs.skills_used` JSONB. На MVP — 5–7 базовых skills, остальная инфраструктура (Registry / Compiler / Pydantic-схема) делается в Спринте 1. См. §19 |
| D69 | **DSL для условий «когда использовать skill»** | Не Python (опасно), не Jinja (тяжело). **Свой YAML DSL** с операторами `eq` / `neq` / `in` / `not_in` / `gt` / `lt` / `exists` / `not_empty` / `matches` / `contains_any` + группировки `any_of` / `all_of` / `not`. Парсер ~200 строк, безопасный (без `eval`), статически анализируем (CI ловит «мёртвые» skills). См. §19.4 |
| D70 | **Кастомизация skills под бренд — 3 уровня** | **L1 (MVP, все тарифы):** отключить не-safety skills через `brands.disabled_global_skills TEXT[]`. **L2 (Pro tier, v1.1):** добавить кастомные skills бренда (новая таблица `brand_custom_skills`, имя с префиксом `brand_{uuid}_`). **L3 (Agency tier, v1.2):** переопределить глобальный skill своим. Skills с тегом `safety` или `system` **никогда** не отключаются и не переопределяются. См. §19.6 |

> **Нумерация:** D24–D25 заняты в `03-feature-scope.md` (MVP-агенты, Brand vs Channel). Нумерация продолжена с D26.

---

## 2. Архитектурные принципы

> Это правила, которыми мы измеряем любое архитектурное решение. Если фича им не соответствует — она спроектирована неправильно.
>
> Согласованы с принципами I1–I17 из `01-product-vision.md`.

| # | Принцип | Что это значит на практике |
| --- | --- | --- |
| П1 | **Модульность и абстракции** | Любая внешняя зависимость (соцсеть, LLM, платежка, генератор картинок, парсер) живёт за абстрактным интерфейсом. На MVP — одна реализация. В будущем — N |
| П2 | **Multi-tenant by design** | `workspace_id` в каждой таблице с данными пользователей + Postgres RLS. Невозможно случайно «вытащить» чужие данные |
| П3 | **Async everything** | Долгие операции — через очередь. LLM, соц-API, тяжёлые операции **никогда** не блокируют HTTP-запрос. Только enqueue → return `job_id` |
| П4 | **Изолированные агенты** | Каждый агент — отдельный модуль. Общаются **только через event bus**. Общая память — только Brand Memory |
| П5 | **Event sourcing для AI** | Каждое решение агента записывается в неизменяемый аудит-лог: модель, prompt, цепочка рассуждений, стоимость, время |
| П6 | **Schema-first contracts** | Между frontend↔backend и между агентами — **Pydantic-схемы**. `dict[str, Any]` в межагентном сообщении — отклонено |
| П7 | **Buy > Build (кроме авторизации)** | Очереди, биллинг, observability — берём готовые решения. **Авторизацию пишем сами** (D28) — нужен полный контроль |
| П8 | **12-Factor App** | Конфигурация через env-переменные. Стейт только в БД / кэше. Сервисы без локального состояния. Логи — в stdout |
| П9 | **Real-time by default** | Изменение состояния → event bus → UI обновляется через WebSocket / SSE. Polling — антипаттерн |
| П10 | **Доверие > скорость (operational)** | Любая авто-фича (auto-publish, auto-reply) имеет: feature flag + kill-switch + rate-limit + safety-net (модерация) |
| П11 | **Brand Memory — единственный источник истины о бренде** | Все агенты читают Brand Memory через **один интерфейс**. Никаких локальных копий ToV в коде агента |
| П12 | **Cost observability** | Каждый LLM-вызов помечен `tenant_id + workspace_id + brand_id + agent`. Дашборд для админа: стоимость per (бренд × агент × месяц) |
| П13 | **Idempotency** | Каждое внешнее действие — идемпотентно по ключу. Повтор retry не создаёт дубликат |

---

## 3. Большая картинка (high-level)

```
┌───────────────────────────────────────────────────────────────────┐
│                          USERS / CLIENTS                          │
│   (Web SPA, Telegram-бот approve, в будущем — Mobile PWA)         │
└──────────────────────────┬────────────────────────────────────────┘
                           │ HTTPS / TLS
┌──────────────────────────▼────────────────────────────────────────┐
│                    EDGE / API GATEWAY                              │
│      Auth (JWT)  •  Rate-limit  •  Tenant context  •  CORS        │
└──────────────────────────┬────────────────────────────────────────┘
                           ▼
┌───────────────────────────────────────────────────────────────────┐
│                        APPLICATION CORE                            │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐   │
│  │ Auth Service │  │ Workspace /  │  │ Billing Service       │   │
│  │ (самописный) │  │ Brand Svc    │  │ (через PaymentProvider)│  │
│  └──────────────┘  └──────────────┘  └───────────────────────┘   │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐   │
│  │ Channels Svc │  │ Posts Svc    │  │ Analytics Svc          │  │
│  │ (Registry)   │  │ (drafts/pub) │  │                        │  │
│  └──────────────┘  └──────────────┘  └───────────────────────┘   │
│  ┌──────────────┐  ┌──────────────┐                              │
│  │ Brand Memory │  │Notifications │                              │
│  │   Service    │  │   Service    │                              │
│  └──────────────┘  └──────────────┘                              │
└──────────────────────────┬────────────────────────────────────────┘
                           ▼
┌───────────────────────────────────────────────────────────────────┐
│                    EVENT BUS (Redis Pub/Sub + Streams)             │
│    Pydantic-типизированные события • Маршрутизация agent→agent     │
└──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────────┘
       ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼
 ┌────────────────────────────────────────────────────────────────┐
 │                       АГЕНТЫ НА MVP (8)                         │
 │  ┌────────────┐  ┌────────────┐  ┌────────────┐               │
 │  │  Content   │  │ Publisher  │  │   Analyst  │               │
 │  │ Sonnet 4.6 │  │GPT-4o-mini │  │Gemini 2.5 P│               │
 │  └────────────┘  └────────────┘  └────────────┘               │
 │  Media — работает «под капотом» как часть Content                │
 │                                                                 │
 │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│
 │  │Orchestra-│ │  Brand   │ │Onboarding│ │Moderation│ │Notificat-││
 │  │tor       │ │  Memory  │ │          │ │          │ │ion       ││
 │  │GPT-4o-mini││GPT-4o-mini│ │Haiku 4.5 │ │GPT-4o-mini││GPT-4o-mini││
 │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘│
 └────────────────────────────────────────────────────────────────┘
 ┌────────────────────────────────────────────────────────────────┐
 │              ПОСЛЕ MVP (7 агентов)                              │
 │  Strategist • Research • Engagement • Optimizer •               │
 │  Monitor • Repurpose • (+ Media как самостоятельный, если выйдет из-под Content) │
 └────────────────────────────────────────────────────────────────┘
                           ▼
┌───────────────────────────────────────────────────────────────────┐
│                  ABSTRACTIONS (Adapters Layer)                     │
│  ┌─────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────┐  │
│  │SocialChannel│  │LLMProvider │  │ImageProvider│ │TrendSource│  │
│  │  (Telegram) │  │ (polza.ai) │  │(Flux-2 Pro, │ │ (TGStat,  │  │
│  │  далее: YT/ │  │  далее:    │  │ Nano Banana)│ │  RSS,…)   │  │
│  │  IG/TT/VK   │  │ OpenAI/…)  │  │             │ │           │  │
│  └─────────────┘  └────────────┘  └────────────┘  └───────────┘  │
│  ┌──────────────────┐  ┌─────────────────────────────┐           │
│  │PaymentProvider   │  │NotificationTransport         │           │
│  │(ЮKassa, bepaid)  │  │(Email, TG-бот)               │           │
│  └──────────────────┘  └─────────────────────────────┘           │
└──────────────────────────┬────────────────────────────────────────┘
                           ▼
┌───────────────────────────────────────────────────────────────────┐
│                    DATA LAYER                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐     │
│  │ Postgres │  │  Redis   │  │ Object   │  │   pgvector   │     │
│  │  (RLS)   │  │ (cache,  │  │ Storage  │  │ (Brand Memory│     │
│  │          │  │ queues,  │  │   (S3)   │  │  + Channel   │     │
│  │          │  │ event bus│  │          │  │  embeddings) │     │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘     │
└───────────────────────────────────────────────────────────────────┘
                           ▼
┌───────────────────────────────────────────────────────────────────┐
│       OBSERVABILITY: логи, метрики, трейсы, AI Audit Log,         │
│       cost per (brand × agent × month) — только для admin/support  │
└───────────────────────────────────────────────────────────────────┘
```

---

## 4. Multi-tenancy (детально)

### 4.1 Иерархия данных

```
User            — конкретный человек (email, password_hash)
Workspace       — изолированное пространство данных (tenant, billing unit)
Membership      — связь User ↔ Workspace + role (owner/editor/viewer)
Brand           — единица контента + Brand Memory
Channel         — подключённая соцсеть бренда (TG-канал, IG-аккаунт)
PlatformRole    — `admin` / `support` / `user` (хранится в `users.platform_role`, D35).
                  `admin`+`support` имеют доступ к `/admin/*`
```

**Полная иерархия:**

```
User
  └─ Workspace (billing unit, tenant)
       └─ Brand (1:N) — у каждого Brand своя Brand Memory
            └─ Channel (1:N) — TG, IG, YT, каждый со своим Network Overlay
```

**Паттерны по персонам (см. `02`):**

| Персона | Паттерн | Тариф |
| --- | --- | --- |
| Анна (SMB) | 1 workspace × 1 brand × 1 канал (TG) | Базовый / средний |
| Денис (сеточник) | 1 workspace × 3–10 брендов × 1–3 канала на бренд | Сетевой |
| Мария (эксперт) | 1 workspace × 1 brand × 1–2 канала (TG + IG) | Средний |

### 4.2 Изоляция через Postgres Row-Level Security

Каждая таблица с данными пользователей имеет колонку `workspace_id`. RLS-политика на уровне БД:

```sql
CREATE POLICY tenant_isolation ON posts
  USING (workspace_id = current_setting('app.workspace_id')::uuid);
```

При каждом HTTP-запросе middleware:
1. Валидирует JWT
2. Извлекает `user_id`
3. Находит активный `workspace_id` через `Membership`
4. Выставляет `SET LOCAL app.workspace_id = ...` для текущей транзакции

**Следствие:** даже если разработчик случайно напишет `SELECT * FROM posts`, БД вернёт только посты текущего тенанта.

### 4.3 Расширение до агентств (post-MVP) — без миграций

Уже на MVP в схеме закладываем:

```sql
CREATE TABLE workspaces (
  id                   UUID PRIMARY KEY,
  parent_workspace_id  UUID REFERENCES workspaces(id),  -- для агентств
  name                 TEXT,
  type                 ENUM('personal', 'agency', 'client'),
  billing_id           UUID REFERENCES billing_accounts(id),
  created_at           TIMESTAMPTZ
);

CREATE TABLE memberships (
  id              UUID PRIMARY KEY,
  user_id         UUID REFERENCES users(id),
  workspace_id    UUID REFERENCES workspaces(id),
  role            ENUM('owner', 'editor', 'viewer'),
  created_at      TIMESTAMPTZ
);
```

На MVP: `parent_workspace_id = NULL`, `type = 'personal'`, `role = 'owner'`, один user — один workspace.

В год 2 для агентств: создаём workspace с `type = 'agency'`, привязываем child workspaces, менеджеры получают `Membership`. **Никаких миграций данных — только новые записи и новые UI-роли.**

---

## 5. Brand Memory Architecture

> Ключевая подсистема. Все 15 агентов читают Brand Memory перед каждым действием. Привязана к **бренду**, не к workspace и не к channel.

### 5.1 Двухслойная структура

```
Brand Memory
├── Core Profile (общий для всех соцсетей бренда)
│   ├── Tone of Voice (стиль, обращение к аудитории)
│   ├── Тематические столпы (3–7 тем)
│   ├── Табу-лист (запрещённые темы, слова, форматы)
│   ├── Аудитория (ICP, демография, интересы)
│   ├── Визуальный гайдлайн (цвета, стиль изображений)
│   ├── Конкуренты-референсы (3–5 каналов)
│   └── Эталонные примеры (5–10 лучших постов)
│
└── Network Overlays (настройки под конкретную соцсеть)
    ├── TG Overlay
    │   ├── Формат постов (длина, стиль MarkdownV2)
    │   ├── Расписание (частота, время)
    │   └── Стиль реакций и комментов
    ├── IG Overlay (после MVP)
    └── YT Overlay (после MVP)
```

### 5.2 Жизненный цикл

```
   Onboarding Agent                Brand Memory Agent              Все агенты
        │                                │                            │
        │  1. Wizard или авто-           │                            │
        │     экстракция из 50 постов     │                            │
        │───────────────────────────────►│                            │
        │  Создаёт Core Profile          │                            │
        │                                │  2. Полнота BM ≥ 80%?      │
        │                                │     Если нет → нудж юзеру  │
        │                                │                            │
        │                                │  3. После каждого поста:   │
        │                                │     Analyst → результаты    │
        │                                │     BM Agent обновляет      │
        │                                │     паттерны                │
        │                                │                            │
        │                                │  4. merge(Core, Overlay)    │
        │                                │─────────────────────────►│
        │                                │  final_context для агента   │
```

### 5.3 Интерфейс доступа

```python
class BrandMemoryService(Protocol):
    """Единый интерфейс для всех агентов."""

    async def get_context(
        self, brand_id: UUID, network: Network | None = None
    ) -> BrandContext:
        """Возвращает merged context: Core Profile + Network Overlay."""
        ...

    async def update_core(self, brand_id: UUID, updates: CoreProfileUpdate) -> None: ...
    async def update_overlay(self, brand_id: UUID, network: Network, updates: OverlayUpdate) -> None: ...
    async def get_completeness(self, brand_id: UUID) -> float: ...  # 0.0–1.0
    async def search_similar(self, brand_id: UUID, query: str, top_k: int = 5) -> list[MemoryChunk]: ...
```

### 5.4 5-уровневый стек контекста (RAG)

При каждом действии агент собирает контекст из 5 уровней:

| Уровень | Источник | Хранилище | Приоритет |
| --- | --- | --- | --- |
| 1 | **Brand Memory** (Core + Overlay) | Postgres + pgvector | Высший — определяет стиль |
| 2 | **История канала** (последние N постов + метрики) | pgvector embeddings | Высокий — контекст |
| 3 | **Конкуренты** (что «зашло» у конкурентов) | Channel Registry + pgvector | Средний — вдохновение |
| 4 | **Тренды** (внешние источники: TGStat, RSS) | trends_cache | Низкий — актуальность |
| 5 | **Запрос пользователя** (тема, тезисы, правки) | in-memory | Низкий — override темы |

---

## 6. Channel Registry с дедупликацией

### 6.1 Зачем

Если 5 разных тенантов мониторят канал «Финансы для людей», парсить его 5 раз — потеря квоты, времени и денег. Дедупликация = **один источник истины**.

### 6.2 Схема

```
┌────────────────────────────┐
│  ГЛОБАЛЬНЫЙ РЕЕСТР КАНАЛОВ │
│ (общий для всех тенантов)  │
├────────────────────────────┤
│ channels                   │
│   id, network (tg|ig|yt|vk)│
│   external_id,             │
│   username, name, niche,   │
│   subscribers, language    │
├────────────────────────────┤
│ channel_posts              │
│   id, channel_id,          │
│   external_post_id,        │
│   content,                 │
│   entities (markdown),     │
│   metrics (views, react.), │
│   posted_at                │
├────────────────────────────┤
│ channel_post_embeddings    │
│   channel_post_id,         │
│   vector (pgvector)        │
└────────────────────────────┘
            │
            │ M:N (через Brand)
            ▼
┌────────────────────────────┐
│  ПЕР-БРЕНД OVERLAYS        │
├────────────────────────────┤
│ brand_channels             │
│   brand_id, channel_id,    │
│   relation (owner /        │
│     competitor / reference)│
│   network (tg/ig/yt)       │
├────────────────────────────┤
│ brand_channel_insights     │
│   AI-инсайты, заметки      │
└────────────────────────────┘
```

### 6.3 Поведение при подключении канала

```
Пользователь добавляет канал @example_channel к бренду «Мой бизнес»:
  1. Платформа ищет в `channels` запись с username = @example_channel
  2. Если есть → создаёт `brand_channels(relation=owner|competitor)`,
     переиспользует существующие посты и эмбеддинги
  3. Если нет → создаёт новую `channels`, ставит в очередь парсинг истории
  4. Парсинг истории работает для всех тенантов одновременно
  5. Onboarding Agent запускает авто-экстракцию Brand Memory из постов
```

### 6.4 Парсинг — как именно собираем посты

**Принципиальная развилка:**

| Тип канала | Как получаем данные | Что используем | Частота |
| --- | --- | --- | --- |
| **Свой канал бренда** (пользователь сделал нашего бота админом) | Через **Bot API** (webhook) | официальный Bot API Telegram | Real-time на каждый пост |
| **Канал конкурента / референс** (наш бот не админ, канал публичный) | Через **user-bot** с `api_id` + `api_hash` + **ротация аккаунтов** | официальный Telegram MTProto через пользовательскую сессию | 1 раз в день для активных, 1 раз в неделю для остальных |

**Что мы НЕ делаем:**

- ❌ Не парсим HTML-страницы каналов (`t.me/s/...`) — это отдельный канал отказа, который мы убрали из архитектуры
- ❌ Не берём из приватных каналов и личной переписки — только публичные

Парсер — отдельный сервис с rate-limiting и пулом аккаунтов user-bot. Запросы дедуплицируются: один канал — одна задача парсинга на цикл.

### 6.5 Безопасность и приватность

- Парсим только **публичные** каналы (доступные без подписки)
- Личные сообщения, приватные каналы, чаты — **никогда**
- В Privacy Policy чётко описано, какие данные собираем

---

## 7. Архитектура агентов

### 7.1 Сколько агентов и какие

**Всего в системе — 15 агентов** (16 операций workflow: Publisher закрывает 2 — подбор времени и публикацию; остальные 14 операций — по одной на 14 агентов).

**На MVP — 8 агентов:**

| Агент | Tier | Модель (через polza.ai) | Tools (упрощённо) |
| --- | --- | --- | --- |
| **Content** | Heavy | Claude Sonnet 4.6 | SearchBrandMemory, SearchHistory, FormatMdV2 |
| **Publisher** | Light | GPT-4o-mini | SelectBestTime, PublishToChannel, SchedulePost (закрывает 2 операции workflow) |
| **Analyst** | Medium | Gemini 2.5 Pro | FetchMetrics, CompareBaseline, GenerateInsight |
| **Orchestrator** | Light | GPT-4o-mini | RouteTask, CheckAgentStatus, RetryPipeline |
| **Brand Memory** | Light | GPT-4o-mini | UpdateCoreProfile, UpdateOverlay, TrackCompleteness |
| **Onboarding** | Medium | Claude Haiku 4.5 | ParseHistory, ExtractToV, CreateBrandMemory |
| **Moderation** | Light | GPT-4o-mini | CheckToxicity, CheckBrandCompliance, CheckTaboo |
| **Notification** | Light | GPT-4o-mini | SendEmail, SendTGBot, AggregateAlerts |

**Media-агент** работает как часть пайплайна Content: когда Content-агент решает, что посту нужна картинка — внутри пайплайна вызывается Media-агент. Отдельным агентом в UI он не представлен — пользователь видит «Content сгенерировал пост с картинкой».

**После MVP (7 агентов):**

| Агент | Tier | Модель | Фаза |
| --- | --- | --- | --- |
| **Strategist** | Medium | Gemini 2.5 Pro | Should |
| **Research** | Medium | Gemini 2.5 Pro | Should |
| **Engagement** | Medium | Claude Haiku 4.5 | Should |
| **Optimizer** | Medium | Gemini 2.5 Pro | Could |
| **Monitor** | Light | GPT-4o-mini | Could |
| **Repurpose** | Medium | Claude Haiku 4.5 | Could |
| **Media** (если выходит в самостоятельные) | — | Flux-2 Pro / Nano Banana | Could |

### 7.2 Каноническая структура агента (BaseAgent)

```python
from pydantic import BaseModel
from enum import Enum

class AgentTier(Enum):
    HEAVY  = "heavy"    # Claude Sonnet 4.6 — для креативных задач
    MEDIUM = "medium"   # Gemini 2.5 Pro / Claude Haiku 4.5
    LIGHT  = "light"    # GPT-4o-mini — для роутинга / классификации

class BaseAgent:
    name: str
    tier: AgentTier
    model_default: str
    tools: list[Tool]

    async def execute(self, input: BaseModel, ctx: AgentContext) -> BaseModel:
        # 1. Получить Brand Memory context
        bm_context = await self.brand_memory.get_context(ctx.brand_id, ctx.network)

        # 2. Собрать prompt через SkillCompiler (§19)
        prompt = await self.skill_compiler.compile(
            agent=self.name, brand_id=ctx.brand_id, context=ctx.dict()
        )

        # 3. Вызвать LLM через абстракцию LLMProvider
        result = await self.llm.complete(prompt, model=self.model_default, tools=self.tools)

        # 4. Записать audit log + cost tracking
        await self.audit_log.record(
            agent=self.name, input=input, prompt=prompt, result=result,
            cost=result.cost, brand_id=ctx.brand_id, workspace_id=ctx.workspace_id,
        )

        # 5. Опубликовать результат в event bus
        await self.event_bus.publish(self.output_event_type, result)

        return self.parse_result(result)
```

### 7.3 Orchestrator — координатор пайплайнов

Orchestrator — центральный координатор. Маршрутизирует задачи между агентами.

```
Pipeline 1: Генерация поста (ежедневно, per-post)
─────────────────────────────────────────────────
Orchestrator
  ├── 1. Content Agent → текст (с контекстом Brand Memory)
  │      └── (внутри) Media Agent → визуал (если нужен)
  ├── 2. Moderation Agent → pre-publish проверка
  ├── 3a. [Full-auto] Publisher Agent → публикация
  └── 3b. [Human-approves] Notification Agent → запрос approve
                            └── Юзер одобрил → Publisher Agent

Pipeline 2: Онбординг бренда (одноразовый)
──────────────────────────────────────────
Orchestrator
  ├── 1. Onboarding Agent → парсинг 50 постов, wizard
  ├── 2. Brand Memory Agent → создаёт Core Profile + TG Overlay
  ├── 3. Content Agent → тестовый черновик (подтверждение стиля)
  └── 4. Notification Agent → «Brand Memory готова, проверьте стиль»

Pipeline 3: Еженедельная аналитика
──────────────────────────────────
Orchestrator
  ├── 1. Analyst Agent → сбор метрик за неделю
  ├── 2. Brand Memory Agent → обновление паттернов
  └── 3. Notification Agent → отчёт пользователю

Pipeline 4: Multi-brand batch (для Дениса)
──────────────────────────────────────────
Orchestrator
  └── for each brand in workspace:
        ├── Pipeline 1 (параллельно для каждого бренда)
        └── Pipeline 3 (параллельно)
```

**Роутинг режима auto / approve:**

```python
class OrchestratorAgent(BaseAgent):
    async def route_post(self, post: GeneratedPost, brand: Brand) -> None:
        if brand.mode == "full_auto":
            await self.event_bus.publish("publisher.publish", post)
        elif brand.mode == "human_approves":
            # Гибрид: применяются auto-rules. Продающие → approve, новостные → auto
            if brand.auto_rules.matches(post):
                await self.event_bus.publish("publisher.publish", post)
            else:
                await self.event_bus.publish("notification.request_approve", post)
```

### 7.4 Включение / выключение агентов по тарифам

```yaml
plans:
  base:
    max_brands: 1
    enabled_agents:
      mvp: [orchestrator, content, media, moderation, publisher, analyst, onboarding, notification, brand_memory]
  middle:
    max_brands: 3
    enabled_agents:
      mvp:    [orchestrator, content, media, moderation, publisher, analyst, onboarding, notification, brand_memory]
      should: [strategist, research, engagement]
  network:
    max_brands: 10
    enabled_agents: [all]
    multi_brand: true
```

При оркестрации workflow проверяет флаг и пропускает выключенных агентов. Это позволяет: продавать «команды агентов» как продукт, выкатывать новых агентов как opt-in бету для отдельных тенантов, лимитировать дорогих агентов для дешёвых тарифов.

### 7.5 Память агентов

| Тип памяти | Что хранится | Где | Доступ |
| --- | --- | --- | --- |
| **Short-term** | Контекст текущей задачи / pipeline | Redis, TTL 1 час | Только текущий pipeline |
| **Brand Memory (Core)** | ToV, табу, столпы, визуальный стиль | Postgres + pgvector | Все агенты бренда |
| **Brand Memory (Overlay)** | Настройки под конкретную соцсеть | Postgres | Агенты при работе с конкретной сетью |
| **Channel Memory** | Семантический индекс истории канала | pgvector | Content, Research, Analyst |
| **Cross-tenant World Memory** | Тренды, форматы, что работает в нише | pgvector (глобальный) | Research, Strategist, Monitor |

Агент при выполнении делает **RAG**: ищет релевантные чанки через 5-уровневый стек (§5.4) и подставляет в prompt.

---

## 8. Event Bus

### 8.1 Зачем

Все inter-agent коммуникации проходят через event bus. **Прямой вызов одного агента из другого запрещён** — иначе мы получаем монолит с жёсткими связями.

### 8.2 Реализация (MVP)

```
Redis Pub/Sub (каналы по типу события)
  ├── content.post_generated      → Moderation Agent подписан
  ├── moderation.content_cleared  → Orchestrator подписан → роутит в Publisher
  ├── moderation.content_flagged  → Notification Agent подписан
  ├── publisher.post_published    → Analyst, Brand Memory подписаны
  ├── analyst.metrics_collected   → Brand Memory подписан
  ├── onboarding.bm_created       → Notification подписан
  ├── orchestrator.pipeline_start → Все агенты в pipeline подписаны
  └── ...
```

Для критичных событий (публикация, биллинг) используем **Redis Streams** (надёжнее: at-least-once + consumer groups + DLQ). Для остального хватает Pub/Sub.

### 8.3 Pydantic-типизированные события

```python
from pydantic import BaseModel
from datetime import datetime
from uuid import UUID
from typing import Literal

class AgentEvent(BaseModel):
    """Базовое событие — все межагентные сообщения наследуются."""
    event_id: UUID
    event_type: str
    brand_id: UUID
    workspace_id: UUID
    agent_source: str
    timestamp: datetime
    idempotency_key: str  # для дедупликации при retry

class ContentGeneratedEvent(AgentEvent):
    event_type: Literal["content.post_generated"] = "content.post_generated"
    post_text: str
    markdown_v2_entities: list[dict]
    media_prompt: str | None
    brand_memory_version: int

class ModerationResult(AgentEvent):
    event_type: Literal["moderation.content_cleared"] = "moderation.content_cleared"
    verdict: Literal["pass", "flag", "block"]
    confidence: float
    reasons: list[str]
    original_event_id: UUID
```

### 8.4 Гарантии

| Свойство | Реализация |
| --- | --- |
| At-least-once delivery | Redis Streams для критичных событий |
| Идемпотентность | `idempotency_key` в каждом событии + дедупликация на receiver |
| Порядок | Per-brand ordering через consumer groups |
| Dead letter queue | После 3 retry событие уходит в DLQ + алерт в Notification |

---

## 9. Абстракции (слой адаптеров)

> Все внешние зависимости — за абстрактными интерфейсами. Сменить поставщика — это поменять реализацию, а не переписать бизнес-логику.

### 9.1 `SocialChannel` — соцсети

```python
class SocialChannel(Protocol):
    async def authenticate(self, credentials: dict) -> AuthResult: ...
    async def post(self, content: PostContent) -> PostResult: ...
    async def schedule(self, content: PostContent, when: datetime) -> ScheduleResult: ...
    async def fetch_metrics(self, post_id: str) -> Metrics: ...
    async def fetch_history(self, channel_id: str, limit: int) -> list[Post]: ...
    async def listen_events(self, channel_id: str) -> AsyncIterator[Event]: ...
```

**Реализации Telegram (две — для двух разных задач):**

| Реализация | Когда используется | Что делает |
| --- | --- | --- |
| `TelegramBotAPIChannel` | Свои каналы бренда (наш бот = админ) | Через **Bot API** (webhook). Публикация, чтение постов и комментов своего канала, аналитика |
| `TelegramUserBotChannel` | Чтение чужих публичных каналов (наш бот **не** админ) | Через **user-bot с `api_id` + `api_hash` + ротация аккаунтов**. Только чтение публичных постов конкурентов / референсов |

**Реализации на год 1+:** `YouTubeChannel`, `InstagramChannel`, `TikTokChannel`, `VKChannel`.

Контент хранится в network-agnostic формате: `content_items` + `network` ENUM + `payload` JSONB. Адаптер трансформирует в нативный формат соцсети.

### 9.2 `LLMProvider` — модели LLM

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        prompt: str,
        model: str,
        tools: list[Tool] | None = None,
        max_tokens: int = 2000,
    ) -> LLMResult: ...

    async def embed(self, text: str, model: str) -> list[float]: ...
```

**Реализации:**
- **MVP:** `PolzaProvider` (единственный, через polza.ai)
- **Год 1+:** `OpenAIProvider`, `AnthropicProvider`, `YandexGPTProvider` и т.п.

Перед каждым вызовом — проверка лимитов тарифа, выбор модели, cost-tagging.

### 9.3 `ImageProvider` — генераторы картинок

```python
class ImageProvider(Protocol):
    async def generate(
        self,
        prompt: str,
        style: ImageStyle | None = None,
        size: tuple[int, int] = (1024, 1024),
    ) -> ImageResult: ...
```

**Реализации:**
- **MVP:** `FluxProProvider` (через polza.ai), `NanoBananaProvider`
- **Год 1+:** `DallEProvider`, `YandexArtProvider`, `StockPhotoProvider` (fallback)

### 9.4 `PaymentProvider` — платежи

```python
class PaymentProvider(Protocol):
    async def create_subscription(self, plan_id: str, customer: Customer) -> Subscription: ...
    async def charge(self, subscription_id: str, amount: Money) -> ChargeResult: ...
    async def cancel(self, subscription_id: str) -> CancellationResult: ...
    async def webhook_handler(self, payload: dict) -> None: ...
```

**Реализации:**
- **MVP:** `YuKassaProvider`, `BepaidProvider` — обе работают через единый интерфейс `PaymentProvider`
- **Год 1+:** добавление нового поставщика (`StripeProvider`, `YooMoneyProvider`, `CloudPaymentsProvider`) **не требует переписывания биллинга** — нужна только новая реализация интерфейса

### 9.5 `TrendSource` — внешние тренды

```python
class TrendSource(Protocol):
    async def fetch_trends(self, niche: str) -> list[Trend]: ...
```

**Реализации:**
- **MVP:** `TGStatPublicSource`, `RSSFeedSource`
- **Post-MVP:** `XTrendsSource`, `RedditSource`, `GoogleTrendsSource`

### 9.6 `NotificationTransport` — транспорт уведомлений

```python
class NotificationTransport(Protocol):
    async def send(self, recipient: User, notification: Notification) -> DeliveryResult: ...
    async def send_with_actions(self, recipient: User, notification: ActionableNotification) -> DeliveryResult: ...
```

**Реализации:**
- **MVP:** `EmailTransport` (UniSender Go), `TelegramBotTransport` (aiogram)
- **Post-MVP:** `PushTransport` (PWA), `SMSTransport`

#### 9.6.1 Routing matrix — какой транспорт для какого события

**Принцип:** оперативные «нажми кнопку» уведомления — **только в Telegram-бот**, чтобы не засорять почту. Email — только для редких системных писем и недельных отчётов.

| Событие (Notification Agent) | EpicID (03) | Default transport | Fallback |
| --- | --- | --- | --- |
| Черновик поста готов | J1 | **Telegram-бот only** | — |
| Требуется approve | J2 | **Telegram-бот only** | — |
| Алерт модерации | J3 | Telegram-бот | — |
| Статус публикации | J4 | Telegram-бот | — |
| Биллинг / системные | J5 | Email | TG-бот (если привязан) |
| Еженедельный отчёт | J6 | Email | TG-бот (если привязан) |
| Кризис: блок аккаунта / kill-switch | — | Email + Telegram-бот | — |

В коде роутинг хранится как таблица `notification_routing_rules` (event_type → channel), переопределяемая в admin-panel (post-MVP). Telegram chat_id привязывается через deep-link `t.me/<bot>?start=<user_token>`. Если у пользователя не привязан TG, J1–J4 ставятся в paused-state с CTA в UI «Привяжите Telegram-бот, чтобы получать оперативные уведомления».

#### 9.6.2 Auto-rules для гибридного режима

> Поле `brands.auto_rules JSONB` хранит **только preset-rule IDs**, не свободный текст. Это критично для предсказуемости production.

**MVP — закрытый каталог preset-rules** (~10–15 правил, прописаны в коде):

| ID | Описание | Параметры |
| --- | --- | --- |
| `auto_publish_short` | Авто-публиковать посты ≤ N символов | `max_chars` (default 500) |
| `auto_publish_no_links` | Авто-публиковать только посты без внешних ссылок | — |
| `auto_publish_no_money` | Не авто-публиковать если упоминается цена / деньги | regex + NER |
| `auto_publish_safe_topics` | Авто-публиковать только из whitelisted topic_pillars | `allowed_pillars[]` |
| `auto_publish_high_confidence` | Авто только если Moderation Agent confidence ≥ X | `min_confidence` (default 0.85) |
| `quiet_hours` | Не публиковать с HH:MM до HH:MM | `start`, `end` |
| `weekend_paused` | Не публиковать в выходные | — |
| `require_human_for_sales` | Посты с CTA / продажей — всегда в approve | rule-based |
| `require_human_for_news` | Новостные / срочные посты — всегда в approve | rule-based |
| `auto_publish_evergreen` | Эвергрин (FAQ, советы, цитаты) — всегда auto | rule-based |
| `daily_post_cap` | Не более N постов в сутки в auto-режиме | `max_per_day` (default 3) |
| `cost_aware_downgrade` | Если у бренда сработал внутренний CostGuardian — Moderation в strict mode | связан с D59 |

`auto_rules.matches(post)` — детерминированная функция в коде, рендерится в UI как набор toggle / слайдеров. **Не использует LLM** — гарантированный output.

**Post-MVP (v1.2) — естественный язык** через отдельный `RuleCompilerAgent`:
- Юзер пишет: «Авто-одобрять посты длиннее 1000 символов и без эмодзи»
- `RuleCompilerAgent` компилирует в DSL: `(post.length > 1000) AND NOT post.has_emoji`
- Скомпилированное правило сохраняется в `auto_rules.compiled_dsl` и исполняется **тем же детерминированным движком**
- В runtime LLM **не задействован** — нет «интерпретационного дрифта»
- Юзер видит preview «как мы поняли ваше правило» с возможностью отредактировать DSL вручную

> **Принцип безопасности (D56):** свободный текст в проде запрещён. Любое правило, влияющее на auto-публикацию, проходит через предсказуемый компилятор → детерминированный исполнитель.

---

## 10. Модель данных — ключевые сущности

> Структура разбита по доменам. Все tenant-scoped таблицы имеют `workspace_id` + RLS-политику.

### 10.1 Identity & tenancy

```
users
  id (UUID), email, hashed_password,
  platform_role ENUM('admin','support','user') NOT NULL DEFAULT 'user',  -- D35
  full_name, avatar_url,
  locale (default 'ru-RU'), timezone (default 'Europe/Minsk'),
  totp_secret (encrypted, nullable),         -- MFA через PyOTP
  recovery_codes (encrypted JSON, nullable),
  last_login_at, last_login_ip,
  created_at

oauth_accounts  -- social login (post-MVP)
  id, user_id (FK), oauth_name,
  access_token (encrypted), refresh_token (encrypted),
  account_id, account_email

sessions  -- revocation, audit
  id, user_id (FK), jti,
  created_at, expires_at, revoked_at,
  ip_address, user_agent

workspaces  -- tenant boundary (RLS scope)
  id (UUID), parent_workspace_id (nullable, FK), name,
  type ENUM('personal','network','agency','client'),
  owner_user_id (FK), plan_id (FK), billing_id (FK), billing_settings JSONB,
  preferred_currency CHAR(3) (default 'RUB'),
  agents_paused BOOL DEFAULT false,           -- kill-switch для админа
  created_at

memberships  -- M:N users в workspace
  id (UUID), user_id (FK), workspace_id (FK),
  role ENUM('owner','admin','editor','viewer'),
  created_at

billing_accounts
  id, plan_id (FK), status, payment_provider, payment_customer_id

audit_events  -- sensitive ops (login, password-change, MFA-toggle, admin-actions)
  id, user_id (FK), workspace_id (FK, nullable),
  event_type, severity ENUM('info','warning','critical'),
  ip_address, user_agent, metadata JSONB,
  created_at
```

### 10.2 Brand & Brand Memory

```
brands
  id (UUID), workspace_id (FK, denorm для RLS),
  name, slug, description, niche, language,
  mode ENUM('full_auto','human_approves'),
  auto_rules JSONB,                          -- preset rule IDs
  disabled_global_skills TEXT[] DEFAULT '{}', -- D70 L1
  created_at, deleted_at

brand_memories  -- 1:1 с Brand
  id, brand_id (PK, FK), workspace_id (denorm),
  core_profile JSONB,
  completeness_score NUMERIC(3,2),           -- 0.00–1.00
  created_at, updated_at

brand_memory_overlays  -- по одному на подключённую соцсеть
  id, brand_id (FK), workspace_id (denorm),
  network ENUM('tg','yt','ig','vk','tt','x'),
  overlay JSONB,
  auto_extracted BOOL, last_synced_at, created_at, updated_at,
  UNIQUE(brand_id, network)

brand_memory_documents  -- knowledge base + RAG-источники
  id, brand_id (FK), workspace_id (denorm),
  document_type ENUM('manual','extracted_post','faq',
                     'competitor_post','article','reference'),
  network (nullable),
  content TEXT,
  metadata JSONB, created_at
```

### 10.3 Channels (Global Registry + per-Brand attachments)

```
channels  -- Global Registry (дедуплицированный, не tenant-scoped)
  id, network ENUM('tg','ig','yt','vk','tt','x'),
  external_id,
  username, name, niche, language, subscribers,
  public_metadata JSONB,
  first_seen_at, last_synced_at

brand_channels  -- какие каналы у бренда
  id, brand_id (FK), workspace_id (denorm),
  channel_id (FK), relation ENUM('owner','competitor','reference'),
  network ENUM(...),
  connection_status,
  oauth_token (encrypted, nullable), bot_token (encrypted, nullable),
  notes, custom_metadata JSONB,
  created_at, deleted_at

channel_posts  -- партиционирована (§17)
  id, channel_id (FK), external_post_id,
  network ENUM,
  content TEXT, entities_jsonb, metrics_jsonb,
  posted_at, fetched_at

brand_channel_insights
  AI-инсайты, заметки (per brand × channel)
```

### 10.4 Контент и публикация (hybrid polymorphism)

```
content_items  -- все форматы постов (network-agnostic ядро + JSONB payload)
  id (UUID), workspace_id (denorm), brand_id (FK), brand_channel_id (FK),
  network ENUM,
  content_type ENUM,
  status ENUM('draft','scheduled','published','failed','retracted'),
  parent_id (UUID, nullable),                -- для derived постов (репурпозинг)
  content_text, entities_jsonb,
  payload JSONB,                             -- network-specific
  scheduled_at, published_at,
  external_post_id, external_url,
  approval_required BOOL, approved_by (FK, nullable),
  agent_run_id (FK), created_at

content_metrics
  id, content_item_id (FK), metric_type, value, fetched_at

media_assets  -- генерированные / загруженные визуалы
  id, content_item_id (FK), brand_id (FK), workspace_id (denorm),
  type ENUM('generated','stock','uploaded'),
  provider ENUM('flux_pro','nano_banana','dalle','uploaded'),
  url, prompt_used, style_jsonb,
  cost_rub NUMERIC(10,4),                    -- учёт стоимости генерации
  created_at

moderation_results
  id, content_item_id (FK),
  verdict ENUM('pass','flag','block'), confidence NUMERIC(3,2),
  reasons_jsonb, agent_run_id (FK), created_at
```

### 10.5 AI Telemetry & Cost Observability

```
agent_runs  -- группирует серию LLM-вызовов в один task (audit-лог)
  id (UUID), workspace_id (denorm), brand_id (FK, nullable),
  agent ENUM(15 значений: orchestrator, content, media, moderation,
             publisher, analyst, onboarding, notification, brand_memory,
             strategist, research, engagement, optimizer, monitor, repurpose),
  content_item_id (FK, nullable),
  trigger ENUM('user','schedule','event'),
  status ENUM('running','success','failed','timeout','budget_exceeded'),
  input_jsonb, output_jsonb,
  total_cost_rub NUMERIC(10,4), total_cost_usd NUMERIC(10,6),
  total_tokens INT, llm_calls_count INT,
  started_at, finished_at, duration_ms,
  chain_of_thought TEXT,                     -- inline trace (MVP: truncate 100KB)
  chain_of_thought_summary TEXT,             -- summary (≤ 2KB) — всегда в БД
  chain_of_thought_uri TEXT,                 -- post-MVP: URI в S3
  skills_used JSONB DEFAULT '[]'             -- D68: какие skills использованы

llm_calls  -- atomic-level truth: каждый вызов LLM
  id (UUID), agent_run_id (FK),
  workspace_id (denorm), brand_id (FK), agent,
  model,
  input_tokens, output_tokens,
  input_cost_usd, output_cost_usd NUMERIC(10,6),
  cost_rub NUMERIC(10,4),
  prompt_hash, cache_hit BOOL,
  duration_ms, status, error_message,
  created_at, metadata JSONB

daily_cost_aggregates  -- денорм для админки (быстрые графики)
  workspace_id, brand_id, date,
  llm_cost_usd, llm_cost_rub, total_tokens, llm_calls_count,
  media_cost_rub,
  posts_published_count, computed_at,
  PRIMARY KEY (workspace_id, brand_id, date)

agent_events  -- персистентность event bus (для дебага и replay)
  id, event_type, brand_id, workspace_id,
  agent_source, agent_target,
  payload_jsonb, idempotency_key,
  status ENUM('pending','processed','failed','dlq'),
  created_at, processed_at

alerts  -- аномалии и превышения (TG-нотификации админу)
  id, workspace_id (nullable, FK), brand_id (nullable, FK),
  alert_type ENUM, severity ENUM('info','warning','critical'),
  message, status ENUM('open','acknowledged','resolved','ignored'),
  metadata JSONB, created_at, resolved_at, resolved_by (FK)
```

### 10.6 Pricing & billing

```
plans
  id, code, tier,
  features JSONB,
  max_brands INT, max_posts_per_month INT,
  max_tokens_per_month BIGINT, max_usd_per_month NUMERIC(10,2),
  enabled_agents JSONB,
  created_at, updated_at                     -- аудит версионирования тарифов

plan_prices
  plan_id (FK), currency CHAR(3),            -- 'RUB','BYN','USD'…
  period ENUM('monthly','annual'),
  amount NUMERIC(10,2),
  effective_from, effective_to
  PRIMARY KEY (plan_id, currency, period, effective_from)

tenant_limit_overrides  -- VIP / promo переопределения
  workspace_id (FK), max_brands, max_posts_per_month,
  max_tokens_per_month, max_usd_per_month, valid_until,
  created_by (FK users), reason TEXT,
  created_at, updated_at

invoices
  id, workspace_id (FK), plan_id (FK),
  amount NUMERIC(10,2), currency CHAR(3),
  reference_amount_usd NUMERIC(10,2),
  exchange_rate NUMERIC(10,6),               -- snapshot на момент charge
  period_start, period_end,
  status ENUM('draft','open','paid','void','failed'),
  created_at
```

### 10.7 Notifications & Onboarding

```
notifications  -- транспорт-агностичные уведомления
  id, workspace_id (denorm), brand_id (FK, nullable), user_id (FK),
  type ENUM('approve_request','alert','digest','status','onboarding'),
  payload_jsonb,
  transport ENUM('email','tg_bot','in_app','push'),
  status ENUM('pending','sent','read','actioned','failed'),
  created_at, sent_at, read_at

onboarding_sessions
  id, brand_id (FK), workspace_id (denorm),
  status ENUM('in_progress','completed','abandoned'),
  wizard_answers_jsonb,
  posts_parsed_count,
  bm_completeness_at_finish NUMERIC(3,2),
  agent_run_id (FK),
  started_at, completed_at
```

### 10.8 Inspiration Boards (EPIC-L)

```
inspiration_boards
  id, brand_id (FK), workspace_id (denorm),
  name, description, is_default BOOL,
  created_at, updated_at

inspiration_items
  id, board_id (FK), workspace_id (denorm), brand_id (FK),
  source_channel_post_id (FK, nullable),
  source_url TEXT,
  niche, format ENUM('text','image','carousel','video','poll'),
  metrics_snapshot JSONB,
  tags TEXT[],
  added_by_user_id (FK), added_via ENUM('manual','weekly_drop','onboarding'),
  created_at,
  INDEX (board_id, created_at DESC)

weekly_inspiration_drops
  id, brand_id (FK), workspace_id (denorm),
  posts_jsonb,
  drop_for_week DATE,
  generated_at, agent_run_id (FK)
```

### 10.9 Vector Embeddings (per-source)

> **Почему по-отдельности:** HNSW/IVFFlat работают на однородных коллекциях. Смешивать brand-vectors с channel-vectors бессмысленно и вредно. Делим на per-source таблицы со своими индексами.

```
brand_memory_embeddings  -- chunks Core Profile + Network Overlays
  id, brand_memory_id (FK), brand_id (FK), workspace_id (denorm),
  chunk_id, chunk_text, vector VECTOR(1536),
  metadata JSONB, created_at,
  INDEX hnsw (vector vector_cosine_ops)

brand_memory_document_embeddings
  id, brand_memory_document_id (FK), brand_id (FK), workspace_id (denorm),
  chunk_id, chunk_text, vector VECTOR(1536),
  metadata JSONB, created_at,
  INDEX hnsw (vector vector_cosine_ops)

channel_post_embeddings  -- партиционируется (network, month)
  id, channel_post_id (FK), network, posted_at,
  vector VECTOR(1536), created_at
  -- HNSW-индекс создаётся на каждой партиции отдельно (D61)

content_item_embeddings  -- post-MVP: RAG по собственному контенту
  id, content_item_id (FK), brand_id (FK), workspace_id (denorm),
  vector VECTOR(1536), created_at
```

**RLS-инвариант:** все tenant-scoped таблицы имеют `workspace_id` + RLS-политику.

### 10.10 Ключевые индексы

**Identity (горячий путь — каждый HTTP-запрос):**
```
sessions(jti)                          UNIQUE
sessions(user_id, expires_at)          WHERE revoked_at IS NULL
users(email)                           UNIQUE
oauth_accounts(provider, oauth_id)     UNIQUE
```

**Cost dashboard / Admin Panel:**
```
llm_calls(workspace_id, created_at DESC)
llm_calls(brand_id, agent, created_at DESC)
llm_calls(agent_run_id)
daily_cost_aggregates(date DESC, llm_cost_usd DESC)
agent_runs(workspace_id, started_at DESC)
agent_runs(brand_id, agent, started_at DESC)
agent_runs USING GIN (skills_used jsonb_path_ops)  -- для bisect регрессий
```

**Alerts:**
```
alerts(severity, created_at DESC)              WHERE status IN ('open','acknowledged')
alerts(workspace_id, status, created_at DESC)
```

**Audit:**
```
audit_events(user_id, created_at DESC)
audit_events(workspace_id, severity, created_at DESC) WHERE severity = 'critical'
```

**Channel Registry / parsing:**
```
channels(network, external_id)         UNIQUE
channel_posts(channel_id, posted_at DESC)
brand_channels(brand_id, network, relation)
```

**Content / moderation:**
```
content_items(brand_id, status, scheduled_at) WHERE status IN ('draft','scheduled')
content_items(parent_id)                       WHERE parent_id IS NOT NULL
moderation_results(content_item_id)
content_items(workspace_id, scheduled_at)      -- календарь по workspace
content_items(brand_id, scheduled_at)          -- календарь по бренду
```

**Brand Memory:**
```
brand_memories(brand_id)               UNIQUE
brand_memories USING gin ((core_profile -> 'topics_pillars'))
brand_memory_overlays(brand_id, network) UNIQUE
brand_memory_documents(brand_id, status)
```

**Vector search (per-source, HNSW):**
```
brand_memory_embeddings             USING hnsw (vector vector_cosine_ops)
brand_memory_document_embeddings    USING hnsw (vector vector_cosine_ops)
channel_post_embeddings_<network>_<YYYY_MM>  -- на каждой партиции
                                    USING hnsw (vector vector_cosine_ops)
```

**Notifications / Billing:**
```
notifications(user_id, status, created_at DESC) WHERE status = 'pending'
invoices(workspace_id, status, period_start DESC)
plan_prices(plan_id, currency, period)
```

---

## 11. Очереди и фоновые задачи

### 11.1 Типы очередей

| Очередь | Что обрабатывает | Приоритет | Агент(ы) |
| --- | --- | --- | --- |
| `orchestrator_pipeline` | Запуск и координация пайплайнов | Critical | Orchestrator |
| `agent_tasks` | Запуски агентов (генерация, анализ) | High | Все |
| `publish` | Публикация постов по расписанию | Critical | Publisher |
| `media_generation` | Генерация изображений | Medium | Media |
| `moderation` | Pre-publish проверка контента | High | Moderation |
| `onboarding` | Парсинг истории, экстракция BM | Medium | Onboarding |
| `brand_memory_update` | Фоновое обновление BM | Low | Brand Memory |
| `parse_history` | Импорт истории канала | Low | — |
| `parse_competitors` | Парсинг публичных каналов через user-bot | Low | — |
| `metrics_fetch` | Сбор метрик с Telegram | Medium | Analyst |
| `notifications` | Email + TG-бот уведомления | Medium | Notification |
| `billing_webhooks` | Обработка вебхуков платёжек | Critical | — |

### 11.2 Реализация на MVP

- **Celery + Redis** или **arq** (async-native) — Python-стек
- Воркеры в отдельных процессах, горизонтально масштабируемые
- Приоритизация через отдельные очереди с разным количеством воркеров

### 11.3 Когда понадобится Temporal

Когда workflow становится длинным и многоэтапным («сгенерируй план → дождись approve → опубликуй → собери метрики через 24 часа → запусти Analyst»), переходим на Temporal с durable workflows. На MVP это overkill.

---

## 12. Moderation Pipeline

> Реализация EPIC-H из `03`. Каждый пост проходит через модерацию перед публикацией (П10: «доверие > скорость»).

### 12.1 Pipeline

```
Content / Media Agent output
        │
        ▼
┌─────────────────────────────┐
│  Шаг 1: Rule-based фильтры   │
│  • Regex: запрещённые слова  │
│  • Табу из Brand Memory       │
│  • Prompt-injection паттерны │
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│  Шаг 2: LLM-judge            │
│  GPT-4o-mini                  │
│  • Токсичность                │
│  • Соответствие бренду        │
│  • Серые ниши                 │
│  • Deepfake-флаги            │
└──────────────┬──────────────┘
               ▼
        ┌──────┴──────┐
        │  Verdict?    │
        └──────┬──────┘
     ┌─────────┼─────────┐
     ▼         ▼         ▼
   PASS      FLAG      BLOCK
     │         │         │
     │    Notification   │
     │    Agent →        Auto-reject
     │    human review   + лог причины
     │         │
     ▼         ▼
  Publisher   Ждём
              действие юзера
```

### 12.2 Safety-net (П10)

Каждая auto-фича имеет 4 элемента:
1. **Feature flag** — можно отключить модерацию per-brand
2. **Kill-switch** — глобальное отключение auto-publish, если что-то пошло не так
3. **Rate-limit** — максимум N auto-publications в час на бренд
4. **Safety-net** — Moderation Agent обязателен перед любой публикацией

---

## 13. Безопасность

| Аспект | Решение |
| --- | --- |
| **Аутентификация** | **Самописная** (D28). JWT (TTL 15 мин) + refresh tokens. **MFA через TOTP** (PyOTP, `users.totp_secret`) + recovery codes. Email-верификация и password-reset через UniSender Go. Brute-force protection — rate-limit. Session revocation через `sessions.jti` |
| **Авторизация** | **Два уровня RBAC.** Платформенный — `users.platform_role` (`admin` / `support` / `user`); определяет доступ к `/admin/*`. Workspace-level — `Membership.role` + RLS на уровне БД. См. §17.2 |
| **Шифрование данных** | TLS в транзите, AES-256 для секретов в БД (`oauth_accounts.access_token`, `brand_channels.bot_token`, `users.totp_secret`, payment customer IDs, `api_id` + `api_hash` для user-bot аккаунтов) |
| **Секреты** | Env-переменные + Vault / Secrets Manager (post-MVP) |
| **Аудит-лог** | `audit_events` для sensitive ops (login, password-change, MFA-toggle, admin-actions) + `agent_runs` для AI-решений. Все записи **неизменяемы** |
| **CSRF / XSS** | SameSite cookies, sanitization markdown через bleach |
| **Rate limiting** | Per-user + per-tenant + per-brand + per-IP |
| **Bot tokens** | Хранятся зашифрованно, ротация раз в 6 месяцев |
| **User-bot аккаунты** | `api_id` + `api_hash` хранятся зашифрованно. Пул аккаунтов с автоматической ротацией, healthcheck. Все парсинги — **только публичных каналов**, никогда не приватных |
| **Auto-publish safety** | Feature flag + kill-switch (`workspaces.agents_paused`) + rate-limit + Moderation Agent |
| **Prompt-injection** | Санитизация user input (темы, комменты). Moderation Agent проверяет |
| **Data export** | `GET /v1/workspaces/{id}/export` — полный архив JSON + media |
| **Admin-panel доступ** | MFA обязателен, короткий JWT TTL (15 мин), re-auth на destructive actions, отдельный аудит impersonate (§17.3) |

---

## 14. Деплой и инфраструктура (high-level)

### 14.1 Контейнеры и оркестрация

- Все сервисы — **Docker-контейнеры**
- На MVP — Docker Compose на VPS
- Post-MVP — Kubernetes, когда нагрузка потребует

### 14.2 Гео и хостинг

| Компонент | Хостинг |
| --- | --- |
| Backend | VPS у одного хостера |
| Object storage | S3-совместимое от того же хостера |
| CDN для статики | Раздаём напрямую с nginx + S3 public bucket с длинными cache-заголовками. Внешний CDN на MVP не используем |
| Redis (cache + queues + event bus) | На том же VPS (MVP), managed Redis (post-MVP) |

### 14.3 Окружения

- **dev** — на локальных машинах разработчиков
- **staging** — для QA, копия prod-структуры
- **prod** — продакшн

### 14.4 CI/CD

- GitHub Actions / GitLab CI
- Любой merge в `main` → автодеплой на staging
- Ручное подтверждение для prod
- Pydantic schema validation в CI — если межагентная схема изменилась, тест падает

### 14.5 Кэш картинок — изоляция по бренду (D60)

> Генерация изображений — самая дорогая операция. Кэшируем результаты, но **строго в пределах бренда** — нельзя, чтобы два конкурирующих канала случайно опубликовали одинаковый визуал.

**Ключ кэша:**

```python
cache_key = sha256(
    f"{brand_id}|"
    f"{normalized_style_prompt}|"
    f"{size}|"
    f"{seed_lora or 'none'}|"
    f"{brand_memory_visual_version}"  # инвалидируется при изменении visual block
).hexdigest()
```

**Хранилище:**
- Redis (горячий кэш): `media:cache:{brand_id}:{cache_key} → s3_url`, TTL 30 дней
- БД (cold reference): `media_assets.cache_key` — для запросов «покажи все сгенерированные»

**Что не кэшируется глобально:** шеринг между брендами **запрещён** архитектурно. Даже «нейтральные фоны» — теоретически дешевле, но создаёт риск «два конкурента с одинаковой картинкой».

**Когда инвалидируется:** юзер обновил визуальный блок Brand Memory → bump `brand_memory_visual_version` → старый ключ перестаёт матчить.

---

## 15. Observability

| Что | Инструмент |
| --- | --- |
| Логи | Sentry (ошибки) + Loki / Grafana или Better Stack (структурированные логи) |
| Метрики | Prometheus + Grafana или Posthog для продуктовых метрик |
| Tracing | OpenTelemetry → Jaeger / Tempo |
| AI Audit Log | Своя реализация (таблица `agent_runs` + UI «Explainability») |
| **Cost Dashboard** | Стоимость per (brand × agent × month). Алерт при cost-per-post > 10 ₽. **Только в админ-панели**, юзеру не показывается |
| **NSM Dashboard** | Autonomous Actions / Active Brand / Week — real-time |
| Алерты | PagerDuty / on-call в Telegram |
| Бизнес-метрики | Posthog: activation rate, TTFAA, AI Acceptance Rate, retention, NSM |

---

## 16. Масштабирование (что и когда)

| Точка боли | Когда наступит | Решение |
| --- | --- | --- |
| Один Postgres не справляется | ~10K активных тенантов | Read replicas, потом sharding по `workspace_id` |
| Расходы на LLM взлетают | На любом росте | Кэш промптов, дешёвые модели для рутины, batch API |
| **Media Agent — основной cost driver (64% LLM-стоимости)** | На любом росте | Кэш изображений по стилю, stock-фото fallback, более экономные модели |
| Парсинг публичных каналов лимитирует | ~1K каналов в Registry | Распределённый парсер с пулом user-bot аккаунтов и ротацией |
| Redis event bus упирается | ~100K events/час | Переход на Kafka |
| Celery / arq очереди упираются | ~100K jobs/час | Переход на Temporal |
| Поиск по vector store медленный | ~1M эмбеддингов | Переход с pgvector на Qdrant / Weaviate |
| Горячие тенанты влияют на остальных | На любом росте | Per-tenant rate-limits, quota enforcement |
| **Multi-brand Дениса нагружает** | ~100 «сетевых» тарифов | Per-brand queues, приоритизация по тарифу |
| **`channel_posts` распухает** | ~100K каналов × 1K постов = 100M строк | Native Postgres partitioning по `network` + sub-partition по `posted_at` (мес). Retention 12 мес для competitor/reference, без ограничения для owner |
| **HNSW-индексы на partitioned tables** | При партиционировании `channel_post_embeddings` | Postgres native partitioning **не наследует** HNSW. Создаём индекс **на каждой партиции отдельно** через `pg_partman` + post-create hook |
| **`chain_of_thought` blob распухает** | ~100K runs/мес × 100KB = 10GB/мес | MVP: inline + truncate 100KB + алерт `cot_size_warning`. Post-MVP: offload в S3, в БД только summary + URI |
| **`embeddings` vacuum bloat** | При write-heavy `channel_post_embeddings` | Per-source таблицы (§10.9) с независимыми autovacuum-настройками |

---

## 17. Cost Telemetry Pipeline

> Полная наблюдаемость LLM-расходов с дня 1 (П12). Observability-first, enforcement-later. **Внутренняя метрика для команды — пользователю не показываем.** У пользователя подписочная модель, без «pay-per-use» расходометра в UI.

### 17.1 Принцип

На MVP **не блокируем** тенантов по бюджету — видим всё и реагируем вручную через алерты и kill-switch. Hard-cap'ы добавим при росте юзербазы. Каждый LLM-вызов помечен `tenant_id + workspace_id + brand_id + agent` и виден **только в `/admin/*`**, не в customer-facing UI.

### 17.2 Архитектура

```
AI Agent (Content / Moderation / …)
           │
           ▼
LLM Budget Guardian (proxy слой)
   ├── Pre-flight: проверяет effective limits
   ├── Принудительно: max_tokens (default 4K in / 2K out)
   ├── Принудительно: timeout (default 30s)
   ├── Считает токены через tiktoken (pre-flight)
   ├── Circuit breaker на retry-loop pattern
   └── Проверяет `workspace.agents_paused` (kill-switch)
           │
           ▼
LLMProvider (polza.ai → OpenAI / Anthropic / Gemini)
           │
           ▼
Sync в БД: llm_calls (atomic) → agent_runs → alerts
           │
           ▼
Celery Beat (hourly):     обновляет daily_cost_aggregates
Celery Beat (every 5 min): проверяет аномалии → alerts
```

### 17.3 Правила обнаружения аномалий

| Правило | Trigger | Severity |
| --- | --- | --- |
| Tenant cost > $5 за сутки | hourly check | warning |
| Tenant cost > $20 за сутки | hourly check | critical |
| Один agent_run > 20 LLM-calls | per-run | warning |
| Один agent_run > 50 LLM-calls | per-run | critical |
| Cost вырос > 5× от среднего за прошлые 7 дней | hourly | warning |
| Repeated timeouts/errors > 10/min у одного tenant | per-min | warning |
| Cost-per-post > 10 ₽ | per-post | warning |
| Media Agent share > 80% cost | hourly | warning |
| `chain_of_thought` size ≥ 90KB | per-run | warning |

Алерт → запись в `alerts` + отправка в TG-бот команде.

### 17.4 Kill-switch (ручная реакция)

Админ через admin-panel (§18) выставляет `workspaces.agents_paused = true`. LLM Budget Guardian видит это на pre-flight и отказывает любой LLM-вызов. Агент получает graceful `AgentsPausedError`, задачи откладываются в dead-letter queue. Клиент видит in-app баннер «Бренд приостановлен, свяжитесь с поддержкой».

### 17.5 Cost-теги (П12)

Каждый LLM-вызов и каждая генерация медиа проходит через cost-tagging middleware:

```python
@dataclass
class CostTag:
    tenant_id: UUID
    workspace_id: UUID
    brand_id: UUID | None
    agent: str
    model: str          # или image-provider
    pipeline_id: UUID
    cost_type: Literal["llm", "image", "embedding"]
```

Cost-дашборд **только во внутренней админ-панели** агрегирует по любой комбинации: `(brand × agent × month)`, `(workspace × model × day)`, `(pipeline_type × week)` и т.д. **В клиентском UI cost-метрики не отображаются** — пользователь платит за подписку, не за токены.

### 17.6 Cost Guardian — внутренние авто-действия (D59)

> Анализ алертов из §17.3 — это **наблюдение**. Cost Guardian превращает наблюдение в **действия**. Внутренний компонент (не один из 8 MVP-агентов), реализован как библиотека в `app/services/cost_guardian/`.
>
> **Это не пользовательский лимит тарифа.** Cost Guardian — это технический предохранитель против runaway LLM-стоимости. Пользовательские лимиты — отдельная фича (см. §17.7).

| Trigger | Действие | Кто узнаёт |
| --- | --- | --- |
| Дорого (порог `T1`) | Алерт в админ-панель (`alerts.severity='info'`) | Только админ |
| Дороже (порог `T2`) | Auto-downgrade модели Content Agent: `Sonnet 4.6 → Haiku 4.5` на 24 часа для этого бренда | Админ + юзер (TG-бот: «временно переключили на эконом-модель, контент-план продолжается») |
| Ещё дороже (порог `T3`) | **Пауза на новые LLM-генерации для бренда на 1 час** (Moderation Agent → strict mode). Уже сгенерированные и запланированные посты продолжают публиковаться по расписанию | Админ + юзер (TG-бот) |
| Критично дорого (порог `T4`) | **Выключение режима «AI делает сам» (full-auto)**: бренд переходит в «человек утверждает», новые автоматические LLM-цепочки не стартуют, пока админ не разберётся | Админ (critical) + юзер |

**Реализация:**
- `CostGuardianService.evaluate(post_id, cost_usd)` вызывается после каждой финализации `agent_run`
- Использует rolling window 1 час + per-brand baseline (среднее за 7 дней)
- Все downgrades / pause логируются в `audit_events` с `event_type='cost_guardian_*'`
- Юзер видит баннер в UI: «На вашем бренде временно включён экономный режим — это не влияет на качество контента, влияет только на скорость восстановления»

### 17.7 Лимиты тарифа подписки — для пользователя (F8b в `03`)

> Это **другая** механика, чем Cost Guardian. Здесь — лимиты, которые видит и контролирует **сам пользователь** в рамках своего тарифа.

| % израсходованного месячного лимита тарифа | Действие | Кто узнаёт |
| --- | --- | --- |
| **60%** | TG-бот / уведомление пользователю: «Использовано 60% месячного лимита генераций» | **Пользователь** |
| **80%** | Повторное уведомление + предложение перейти на тариф выше | **Пользователь** |
| **100%** | **Блокируется именно та фича, по которой исчерпан лимит** (например, новые AI-генерации недоступны до апгрейда тарифа или следующего расчётного периода). Уже сгенерированные и запланированные посты продолжают публиковаться по расписанию | **Пользователь** |

**Реализация:**
- Лимиты тарифа описаны в `plans.max_*` и переопределяются в `tenant_limit_overrides`
- `daily_cost_aggregates` (§10.5) суммируется по бренду / workspace за MTD
- Проверка — `pg_cron` job каждые 30 мин + on-demand при попытке вызвать соответствующую фичу
- Если упёрлись — фронт получает typed-ошибку (см. §18.5) с CTA «Перейти на тариф выше»

> Сравнение F8 vs F8b — см. блок «Что разделять» в начале документа `03 §F8`.

---

## 18. Внутренняя Admin Panel

> Наблюдаемость и управление для команды. **Phase 2 (недели 9–16)**, в том же Next.js приложении под `/admin/*` с RBAC через `users.platform_role` (D35).

### 18.1 Роуты

| URL | Что делает |
| --- | --- |
| `/admin` | Global dashboard: total LLM cost (today / MTD / lifetime), top 10 expensive tenants, cost per agent, model usage breakdown, anomaly alerts, NSM |
| `/admin/tenants` | Список тенантов: email, plan, MRR, posts MTD, LLM cost MTD, lifetime, last_login, status. Фильтры: by plan, by cost-spike |
| `/admin/tenants/{id}` | Профиль + billing + LLM cost по дням (90d), per-agent breakdown, top-50 дорогих постов, drill-down в llm_calls, actions: pause / suspend / refund / notify / reset-rate-limit |
| `/admin/tenants/{id}/brands` | Список брендов тенанта + per-brand cost + полнота Brand Memory |
| `/admin/llm-calls` | Searchable лог всех LLM-вызовов (виртуализированный) |
| `/admin/agent-runs` | Searchable лог agent_runs + viewer chain_of_thought (Explainability) |
| `/admin/alerts` | Активные алерты + история, action buttons (acknowledge / resolve / ignore) |
| `/admin/audit` | Audit log: все sensitive actions (login, password-change, admin-actions) |
| `/admin/plans` | CRUD тарифов и цен (`plan_prices` per currency), feature flags, A/B на ценах |
| `/admin/moderation` | Moderation-queue: посты со статусом `flag` / `block`, manual override, fine-tune labels |
| `/admin/skills` (post-MVP) | Список skills с usage stats, кнопка «промоутить custom skill из бренда в global» |

### 18.2 Платформенные роли — что может admin, что может support

3 платформенные роли уже на MVP (хранятся в `users.platform_role ENUM('admin','support','user')`, default `user`):

| Роль | Доступ | Кому |
| --- | --- | --- |
| `admin` | Полный read-write по всем тенантам, `/admin/*`, impersonate, plans editor, ручные refund / suspend, kill-switch | Команда, ядро |
| `support` | Read-only по тенантам + точечные write-операции из allow-list (см. ниже). Без impersonate, без plans editor, без refund | Ops / support staff |
| `user` | Обычный пользователь продукта. Доступ только к своим workspace через memberships | Все клиенты |

**Матрица: что может `admin` и что может `support` по каждому разделу админ-панели:**

| Раздел / действие | `admin` | `support` |
| --- | --- | --- |
| `/admin` Global dashboard | полный доступ | read-only |
| `/admin/tenants` Список | полный доступ | read-only |
| `/admin/tenants/{id}` — Pause / unpause бренда | ✅ | ✅ |
| `/admin/tenants/{id}` — Reset password / resend verify | ✅ | ✅ |
| `/admin/tenants/{id}` — Snooze алертов | ✅ | ✅ |
| `/admin/tenants/{id}` — Reset rate-limit | ✅ | ✅ |
| `/admin/tenants/{id}` — Suspend / freeze аккаунта | ✅ | ❌ |
| `/admin/tenants/{id}` — Refund / возврат денег | ✅ | ❌ |
| `/admin/tenants/{id}` — Impersonate user | ✅ | ❌ |
| `/admin/tenants/{id}` — Изменение лимитов (`tenant_limit_overrides`) | ✅ | ❌ |
| `/admin/llm-calls` Лог LLM-вызовов | полный доступ | read-only |
| `/admin/agent-runs` Лог + chain_of_thought | полный доступ | read-only |
| `/admin/alerts` — Acknowledge / resolve / ignore | ✅ | ✅ (только acknowledge / snooze) |
| `/admin/audit` Audit log | полный доступ | read-only |
| `/admin/plans` Редактор тарифов | ✅ | ❌ нет доступа |
| `/admin/moderation` Очередь модерации | полный доступ + дообучение разметки | пометить пост на ручной разбор, эскалировать админу |
| Управление платформенными ролями (`users.platform_role`) | ✅ | ❌ нет доступа |
| Использование данных для обучения моделей (включение / выключение) | ✅ | ❌ |

- Middleware `require_platform_role('admin' | 'support')` проверяет JWT + `users.platform_role`
- Все destructive actions логируются в `audit_events` с `severity='critical'`
- Post-MVP — дальнейшее дробление: `finance`, `compliance`

### 18.3 Безопасность админ-панели

- **MFA обязателен** для `platform_role in ('admin','support')` (`users.totp_secret`)
- **Короткий JWT TTL** (15 мин вместо 1 ч) + re-auth на destructive actions (suspend, refund, impersonate, изменение тарифов)
- **Impersonate user** — доступно только для `admin`, отдельный аудит, уведомление юзеру в TG-бот
- **Read-only mode по умолчанию** — для `support` все write-операции вне allow-list заблокированы middleware; для `admin` write на критических действиях включается через ручное подтверждение (re-auth)

### 18.4 Санитизация входа для LLM (D58)

> Защита от prompt injection и malicious content в данных, попадающих в LLM из публичных каналов и комментариев.

**Pipeline для любых входящих данных, идущих в LLM (Inspiration Board → Content Agent, Engagement Agent, Brand Memory Agent):**

```
Внешний текст (пост публичного TG-канала / коммент / парсенный конкурент)
    │
    ▼
1. Pattern-replace (regex):
   - URL → [URL_REDACTED] (агент знает «была ссылка», но не саму ссылку)
   - phone / email → [PHONE_REDACTED] / [EMAIL_REDACTED]
   - long crypto strings (BTC/ETH addresses) → [CRYPTO_REDACTED]
   │
   ▼
2. bleach + injection-pattern denylist:
   - "ignore previous instructions", "you are now", "system:", "</user>"
   - zero-width chars (U+200B…U+200F, U+FEFF)
   - control chars (кроме \n, \t)
   │
   ▼
3. Allow-list для Inspiration Board:
   - Принимаем только URL с доменов, зарегистрированных в Channel Registry
   - Остальное — обрезаем до текста без URL
   │
   ▼
4. LLM-judge sanity (Moderation Agent, fast path):
   - Если detector_score > 0.8 → flag for manual review, не в context
```

**Outbound** (то, что генерирует наш агент перед публикацией) — отдельная защита через Moderation Agent (rule + LLM-judge, описан в EPIC-D `03`).

**Реализация:** `app/services/safety/input_sanitizer.py` — единая точка входа для всех агентов. Покрыто snapshot-тестами (`tests/safety/test_prompt_injection.py`).

### 18.5 Типизированные ошибки API → UI (D62)

> **Никаких тихих сбоев.** Каждый отказ агента / LLM-провайдера — типизированное исключение → HTTP error response с кодом → понятный toast в UI с CTA.

**Backend (Pydantic exception → response):**

```python
class AppError(BaseModel):
    error_code: Literal[
        "LLM_BUDGET_EXCEEDED",   # 429
        "MODEL_TIMEOUT",          # 504
        "CIRCUIT_BREAKER_OPEN",   # 503
        "MODERATION_BLOCKED",     # 422
        "AGENTS_PAUSED",          # 403
        "RATE_LIMITED",           # 429
        "PLAN_LIMIT_REACHED",     # 429 (F8b — лимит тарифа исчерпан)
        "INVALID_INPUT",          # 400
        "SKILL_NOT_FOUND",
        "SKILL_VALIDATION_FAILED",
        "SKILL_OVERRIDE_FORBIDDEN",
        "SKILL_BUDGET_EXCEEDED",
        "SKILL_COMPILATION_TIMEOUT",
    ]
    message_key: str        # для i18n lookup на frontend
    details: dict           # код-зависимые поля
    retry_after_seconds: int | None
    suggested_action: Literal["retry", "downgrade", "wait", "contact_support", "edit_manually", "upgrade_plan"] | None
```

**Frontend (TanStack Query error boundary):** ловит `error_code` → mapping в локализованный toast + CTA:

| `error_code` | Toast (RU) | CTA |
| --- | --- | --- |
| `PLAN_LIMIT_REACHED` | «Лимит тарифа исчерпан: {details.feature}» | «Перейти на тариф выше» |
| `MODEL_TIMEOUT` | «AI не отвечает — попробовать ещё раз» | «Повторить» (retry-after из ответа) |
| `CIRCUIT_BREAKER_OPEN` | «Сервис временно недоступен, восстанавливаем» | «Ждать» (с countdown) |
| `MODERATION_BLOCKED` | «Контент не прошёл модерацию: {details.reason}» | «Открыть в редакторе вручную» |
| `AGENTS_PAUSED` | «Бренд приостановлен — свяжитесь с поддержкой» | «Открыть тикет» |

Все статусы задач **всегда видимы** в `/dashboard/agent-runs` (для пользователя — только свои; для админа — все, в `/admin/agent-runs`). При фоновом сбое создаётся `notification` (TG-бот, J3/J4), а не silent crash.

### 18.6 JWT — strict минимум + Redis для memberships (D64)

> Защита от JWT-bloat (агентство с 500 workspace'ами) + мгновенный revoke ролей.

**JWT claims (минимум):**

```json
{
  "sub": "<user_id>",
  "platform_role": "user|support|admin",
  "active_workspace_id": "<uuid>",
  "jti": "<token_id>",
  "exp": 1735689600,
  "iat": 1735603200
}
```

**Memberships (массив `{workspace_id, role, brand_ids[]}`) НЕ в JWT.** Хранятся в Redis:

```
KEY:   user:{user_id}:memberships
TYPE:  JSON
TTL:   300 (5 минут)
VALUE: [
  {"workspace_id": "...", "role": "owner", "brand_ids": [...]},
  ...
]
```

**Lifecycle:**
- Login / token refresh → backend наполняет Redis из БД (если cache miss)
- Каждый защищённый endpoint → middleware читает из Redis (миллисекунды), не из БД
- При изменении ролей (admin даёт invite, removes member, меняет role) → `DEL user:{uid}:memberships` + WS push клиенту `auth.refresh_required`

**Инвариант:** в проде JWT никогда не должен превышать **2 KB**. CI-проверка размера токена в integration tests.

### 18.7 Не путать с customer-facing UI

Admin Panel — **не для тенантов**. Тенанты имеют свой Settings UI (`/settings/*`) для управления брендами, каналами, биллингом. Admin panel видят только платформенные операторы.

---

## 19. Internationalization (i18n) и multi-currency

### 19.1 Frontend i18n — `next-intl`

- Строки UI в `messages/ru.json` (основной) + `messages/en.json` (базовый для системных страниц: 404, 500, email-templates)
- **Никаких хардкодед русских строк** в компонентах — всё через `useTranslations()`
- Date / number formatting — `Intl.DateTimeFormat`, `Intl.NumberFormat` (locale-aware)
- Routes на MVP без locale-prefix (RU-only). При включении EN — добавим `/en/*` без переписывания
- Локаль юзера хранится в `users.locale` (default `ru-RU`), timezone — в `users.timezone` (default `Europe/Minsk`)

### 19.2 Backend i18n — error codes

- Backend возвращает ошибки как `{error_code: "...", details: {...}}`. Frontend локализует
- Сообщения в email-templates UniSender Go — с placeholders, локаль из `users.locale`
- Время в БД **всегда UTC**; конверсия в `users.timezone` на UI
- LLM-промпты — хранятся с locale-переменными; при росте EN — добавляется второй prompt-template без переписывания логики агентов

### 19.2.1 Локаль агентов — три разных уровня (D63)

> Юзер из СНГ, UI на русском, но ведёт английский Telegram-канал. Что и на каком языке генерируется — описано здесь.

| Слой | Язык | Почему |
| --- | --- | --- |
| **System prompt** (инструкции LLM) | **Всегда `en`** | Claude / GPT лучше токенизируют английский (~30% дешевле), instruction-following точнее на en |
| **User context / Brand Memory** | Whatever язык в БД (обычно `ru`) | Сохраняем как есть — это пользовательский контент |
| **LLM output / generated post** | `brand.content_language` (default `ru`) | Язык канала — где публикуется пост |
| **UI / Notification Agent → user** | `users.locale` (default `ru-RU`) | TG-бот пишет юзеру на его языке UI, не на языке его канала |

**Пример (юзер RU + английский канал):**

```
System prompt: "You are a content writer. Generate post in {brand.content_language}.
                Brand context: {brand_memory_json}. ..."
brand.content_language = "en"
users.locale = "ru-RU"

→ Generated post: "Hello! Three reasons why ..."    (en — для канала)
→ TG-бот юзеру:   "Черновик готов — посмотреть"      (ru — для UI)
```

### 19.3 Multi-currency

Схема в §10.6 (`plan_prices` per `(plan, currency, period, effective_from)`).

**На MVP:**
- Валюты: **RUB и BYN**
- Провайдеры: `YuKassaProvider` (RUB), `BepaidProvider` (BYN), оба через единый `PaymentProvider`
- `users.preferred_currency` / `workspaces.preferred_currency` — пользовательский выбор
- LLM-cost в internal reporting **всегда в USD**, invoicing в `preferred_currency`
- Exchange rate — **fixed snapshot** на момент charge (`invoices.exchange_rate`) — избегаем arbitrage
- Cost в рублях (`cost_rub`) денормализуется в `agent_runs`, `llm_calls`, `media_assets` для быстрых дашбордов

**Post-PMF:**
- Добавление USD / EUR — это **новая запись в `plan_prices` + новый `PaymentProvider`**, не переписывание биллинга
- Динамические exchange rates (snapshot раз в сутки)

### 19.4 i18n-ready чеклист для новых фич

- [ ] Все user-facing строки в `messages/ru.json` (нет хардкода)
- [ ] Все даты / числа форматируются через `Intl.*`
- [ ] Все временные поля в БД — `TIMESTAMPTZ` в UTC
- [ ] Все валютные поля в БД имеют пары `(amount, currency)` или `reference_amount_usd`
- [ ] Backend errors — с `error_code`, не с hardcoded сообщениями
- [ ] Email-templates используют `users.locale`

### 19.5 Data Retention Policy (D57 + D67)

> Без активной политики ретеншена `agent_runs`, `llm_calls`, `channel_posts`, `channel_post_embeddings` за полгода вырастают до сотен GB. Стратегия: hot / warm aggregated / cold S3 / hard delete.

| Таблица | Hot (в БД, детально) | Warm (агрегаты) | Cold (S3 архив) | Hard delete |
| --- | --- | --- | --- | --- |
| `agent_runs.chain_of_thought` + `retrieved_context` | **30 дней** | — | 12 мес (`agent_runs/{year_month}.jsonl.zst` в S3) | через 12 мес |
| `agent_runs` метаданные (model, tokens, cost, status) | **90 дней** | `agent_runs_daily` (tenant × agent × day) навсегда | — | через 12 мес |
| `llm_calls` raw | **90 дней** | `llm_calls_daily` (tenant × agent × model × day) **навсегда** | — | через 12 мес |
| `channel_posts` raw text | **180 дней** | — | 24 мес в S3 (`channel_posts_archive/`) | через 24 мес |
| `channel_post_embeddings` | **12 мес** (текущая партиция + 11 прошлых) | — | Старые партиции → S3 в pgvector dump | через 24 мес |
| `audit_log` (sensitive actions) | **2 года hot** | — | 5 лет cold в S3 | через 5 лет |
| `notifications` | **30 дней** | — | — | hard delete |
| `events` (Redis Stream) | **7 дней TTL** | — | — | auto-expire |
| `content_metrics` (просмотры / реакции) | **24 мес** | `content_metrics_daily` навсегда | — | через 24 мес |
| `media_assets` (S3 файлы) | 12 мес активно | — | Перенос в IA-класс S3 после 12 мес | удаление только при `delete_account` |

**Реализация — `pg_cron`:**

```sql
-- Ежедневный retention job
SELECT cron.schedule('retention_daily', '0 3 * * *', $$
  -- Truncate chain_of_thought на agent_runs старше 30 дней
  UPDATE agent_runs
     SET chain_of_thought = NULL, retrieved_context = NULL
   WHERE created_at < NOW() - INTERVAL '30 days'
     AND chain_of_thought IS NOT NULL;

  -- Hard delete agent_runs старше 12 мес
  DELETE FROM agent_runs WHERE created_at < NOW() - INTERVAL '12 months';
  DELETE FROM llm_calls  WHERE created_at < NOW() - INTERVAL '12 months';
  DELETE FROM notifications WHERE created_at < NOW() - INTERVAL '30 days';
$$);

-- Еженедельный архив в S3
SELECT cron.schedule('retention_weekly_archive', '0 4 * * 0', $$
  -- COPY .. TO PROGRAM в скрипт, который льёт в S3 + verify checksum
  ...
$$);
```

**Использование данных для обучения собственных моделей (D67) — opt-in:**

- Дефолт: `users.training_consent = false` → данные пользователя **не переживают** retention
- Юзер может включить в Settings → Privacy → «Разрешить использовать мои анонимизированные посты для улучшения AI»
- При opt-in: перед hard delete посты + agent_runs пропускаются через анонимизатор (имена брендов → `[BRAND_*]`, имена людей → `[PERSON_*]`) и сохраняются в `training_corpus` (доступ только `admin`)
- Согласие явное, через отдельную галочку, не часть Terms of Service

### 19.6 Партиционирование `channel_post_embeddings` + HNSW (D61)

> `channel_post_embeddings` — самая быстрорастущая таблица (100K–1M строк/мес при росте до 1000 тенантов). Партиционируем помесячно. **Критично:** новые партиции должны иметь HNSW-индекс с момента создания, иначе семантический поиск в 1-й день месяца сломается.

**Решение — pg_partman + template table + pg_cron safety net:**

```sql
-- 1) Template-таблица со ВСЕМИ нужными индексами
CREATE TABLE channel_post_embeddings_template (LIKE channel_post_embeddings INCLUDING ALL);

CREATE INDEX ON channel_post_embeddings_template
  USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
CREATE INDEX ON channel_post_embeddings_template (workspace_id, channel_id);

-- 2) pg_partman setup — копирует индексы из template на каждую новую партицию
SELECT partman.create_parent(
  p_parent_table => 'public.channel_post_embeddings',
  p_control => 'created_at',
  p_type => 'native',
  p_interval => 'monthly',
  p_template_table => 'public.channel_post_embeddings_template',
  p_premake => 2
);

-- 3) Safety-net через pg_cron — 25-го каждого месяца
SELECT cron.schedule('partman_safety_check', '0 2 25 * *', $$
  -- 3a. Запускаем maintenance
  SELECT partman.run_maintenance(p_parent_table => 'public.channel_post_embeddings');

  -- 3b. Проверяем, что у партиции на следующий месяц есть HNSW
  DO $check$
  DECLARE
    next_partition_name TEXT := 'channel_post_embeddings_p' || to_char(now() + interval '1 month', 'YYYY_MM');
    has_hnsw BOOLEAN;
  BEGIN
    SELECT EXISTS(
      SELECT 1 FROM pg_indexes
      WHERE tablename = next_partition_name
        AND indexdef ILIKE '%USING hnsw%'
    ) INTO has_hnsw;

    IF NOT has_hnsw THEN
      PERFORM pg_notify('partman_alert', 'MISSING_HNSW:' || next_partition_name);
      -- + insert в alerts → Sentry alert
    END IF;
  END$check$;
$$);
```

**Архивация старых партиций:**
- Партиции старше 12 мес → `pg_dump` в S3
- `DROP TABLE channel_post_embeddings_p2025_01;` (после verify)
- При необходимости — restore через `pg_restore` обратно

### 19.7 PgBouncer + RLS — transaction pooling (D65)

> `SET LOCAL` гасится при commit/rollback. При session pooling одно соединение прибито к клиенту — безопасно, но дорого по соединениям. **Используем transaction pooling** + дисциплина «каждый HTTP-request = одна транзакция».

**Конфигурация:**

```ini
# pgbouncer.ini
[databases]
app = host=db.internal port=5432 dbname=app pool_size=50

[pgbouncer]
pool_mode = transaction      ; КРИТИЧНО: не session
max_client_conn = 2000
default_pool_size = 50
reserve_pool_size = 10
```

**FastAPI dependency-обёртка (упрощённо):**

```python
async def db_session_with_rls(
    user: User = Depends(current_user),
    active_workspace: Workspace = Depends(active_workspace),
):
    async with AsyncSessionLocal() as session:
        async with session.begin():  # одна транзакция = один HTTP request
            await session.execute(text(
                "SET LOCAL app.current_user_id = :uid; "
                "SET LOCAL app.current_tenant_id = :wid; "
                "SET LOCAL app.platform_role = :role;"
            ), {
                "uid": str(user.id),
                "wid": str(active_workspace.id),
                "role": user.platform_role,
            })
            yield session
        # commit или rollback → SET LOCAL гасится → соединение чистое в пуле
```

**Запреты (CI-линтер):**
- `SET app.*` без `LOCAL` — нельзя (`tools/lint_set_local.py` проверяет миграции и бизнес-код)
- Длинные транзакции (> 5 сек) — алерт
- `SELECT pg_sleep()` в проде — запрещено
- Cross-request shared `connection` — запрещено

**Запасной план для long-running операций (редко):** отдельный пул `pgbouncer-session` с `pool_mode = session` для admin-tasks (импорт, миграции).

---

## 20. Skill-based архитектура агентов (D68 / D69 / D70)

> **Принцип:** агенты **не имеют** монолитных system-промптов. Знание агента **декомпозируется на skills** — переиспользуемые, версионируемые модули в файловой системе. На каждый вызов LLM компилируется **минимально необходимый набор skills** под текущий контекст (`progressive disclosure`).

### 20.1 Зачем — монолитный промпт vs skills

| | Монолитный промпт | Skills |
| --- | --- | --- |
| Tokens на запрос | ~9500 (всё inline) | ~6800 (−28% в среднем) |
| Версионирование | строка `prompt_version='v17.2'` | JSONB `skills_used: [{name, version}, ...]` → bisect автоматический |
| Тестируемость | прогон агента целиком | каждый skill — отдельный pytest |
| Изменение TG-формата | редактируем 3 промпта в разное время | правим 1 файл, версия 1.4 → 1.5 для всех агентов |
| Per-niche customization | новые ветки в монолите | новый файл `medical-claims-safety/SKILL.md` |
| Override от Agency | major refactor 4–6 спринтов | новая таблица, 1 спринт |

Экономия на токенах при росте до сотни платящих юзеров составит существенную долю LLM-стоимости.

### 20.2 Структура `app/skills/`

```
app/skills/
├── content-agent-base/
│   └── SKILL.md
├── prompt-injection-defender/        # safety, всегда
│   └── SKILL.md
├── tg-markdownv2-formatter/
│   ├── SKILL.md
│   └── escape_rules.md               # supporting docs, lazy-load
├── brand-voice-applier/
│   ├── SKILL.md
│   └── tone_examples.md
├── 5-level-context-merger/
│   └── SKILL.md
├── sales-hooks-and-cta/              # post_type in [sales, product_launch]
│   ├── SKILL.md
│   └── proven_formulas.md
├── evergreen-soft-hooks/             # post_type in [educational, lifestyle, opinion]
│   └── SKILL.md
└── auto-rules-evaluator/             # if brand.auto_rules не пуст (safety)
    └── SKILL.md
```

### 20.3 Формат SKILL.md

```markdown
---
name: sales-hooks-and-cta
version: 2.1
description: Проверенные формулы hooks и CTA для продающих постов
when_to_use:
  - field: agent
    eq: content
  - field: post_type
    in: [sales, product_launch]
tags: [content, sales, conversion]
token_budget: 280
customizable:
  can_disable: true
  can_override: true       # L3 Agency может переопределить
  can_add_custom: true
owners: [founder, content-lead]
---

# Sales hooks и CTA

## Hooks (открывалки)
- «Только что вернулась с [событие]…»
…
```

**Pydantic-схема валидируется при старте FastAPI** через `SkillRegistry.load_all()`:

```python
class SkillCustomizability(BaseModel):
    can_disable: bool = True
    can_override: bool = False
    can_add_custom: bool = True

class SkillManifest(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9-]+$")
    version: str = Field(pattern=r"^\d+\.\d+$")
    description: str
    when_to_use: SkillCondition | Literal["always"]
    tags: list[str]
    token_budget: int = Field(le=2000)
    customizable: SkillCustomizability = SkillCustomizability()
    owners: list[str]
    body: str
    supporting_files: dict[str, str] = {}
```

**Safety-ограничения (хард-кодед, не переопределяются manifest'ом):**

| tag | can_disable | can_override |
| --- | --- | --- |
| `safety` | ❌ never | ❌ never |
| `system` | ❌ never | ❌ never |
| `content` | ✅ | ✅ (Agency tier) |
| `niche` | ✅ | ✅ |
| `experimental` | ✅ | ❌ |

Skill с тегом `safety` (`prompt-injection-defender`, `auto-rules-evaluator`, `content-agent-base`) **никогда** не отключается — даже если в manifest указано `can_disable: true`, runtime принудительно ставит `False`.

### 20.4 DSL для `when_to_use` (D69)

**Не Python (`eval` опасен), не Jinja (тяжёлый, плохо парсится в UI builder) — собственный YAML DSL.**

```yaml
# Сентинел «всегда»
when_to_use: always

# Простое условие
when_to_use:
  field: agent
  eq: content

# AND по умолчанию (массив = все условия истинны)
when_to_use:
  - field: agent
    eq: content
  - field: post_type
    in: [sales, product_launch]
  - field: brand.auto_rules
    not_empty: true

# OR / NOT — явные группировки
when_to_use:
  any_of:
    - field: agent
      eq: content
    - field: agent
      eq: repurpose
  all_of:
    - field: post_type
      not_in: [evergreen]
  not:
    field: brand.industry
    eq: legal
```

**Операторы:** `eq`, `neq`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `exists`, `not_empty`, `matches` (re2), `contains_any`. Группировки: `any_of`, `all_of`, `not`.

**Контекстные поля (dot-notation):** `agent`, `post_type`, `brand.industry`, `brand.content_language`, `brand.auto_rules`, `brand.disabled_skills`, `channel.subscriber_count`, `user.locale`, `user.platform_role`, `request.tone_override`, `tags`.

**Безопасность:**
- Парсер — `pydantic-yaml` (валидация на старте) + рекурсивный AST walker
- `matches` использует `google-re2` (защита от ReDoS)
- Глубина рекурсии ограничена (max 5 уровней `any_of/all_of/not`)
- Soft-limit на вычисление одного условия — 1ms (Sentry alert при превышении)

**Статический анализ на CI:**
- Для каждого skill прогоняется матрица типичных контекстов (10–20 сценариев)
- Если skill не активируется ни в одном — CI fail («dead skill»)
- Если skill активируется во всех — CI warning («условие фактически `always`»)

### 20.5 SkillCompiler

```python
class CompiledPrompt(BaseModel):
    text: str
    skills_used: list[dict]
    total_tokens: int
    eval_trace: list[dict]

class SkillCompiler:
    async def compile(self, agent: str, brand_id: UUID, context: dict) -> CompiledPrompt:
        ctx = {
            "agent": agent,
            **context,
            **(await self._enrich_brand_context(brand_id)),
        }

        selected = []
        trace = []
        all_skills = await self.registry.for_brand(brand_id)

        for skill in all_skills:
            result = skill.when_to_use.evaluate(ctx)
            trace.append({"skill": skill.name, "passed": result.passed})

            if not result.passed:
                continue
            if skill.name in ctx["brand.disabled_skills"] and "safety" not in skill.tags:
                continue
            selected.append(skill)

        selected.sort(key=lambda s: (s.tags, s.name))  # детерминизм

        body = "\n\n---\n\n".join(s.body for s in selected)
        used = [{"name": s.name, "version": s.version, "source": s.source} for s in selected]

        compiled = CompiledPrompt(
            text=body,
            skills_used=used,
            total_tokens=self._estimate_tokens(body),
            eval_trace=trace,
        )

        if compiled.total_tokens > self._budget_for(brand_id):
            raise SkillBudgetExceededError(...)

        return compiled
```

**Кэширование:**
- Глобальные skills загружаются в память при старте FastAPI (immutable)
- Brand overrides — Redis `brand:{id}:skills:overrides` TTL 1 час
- Compiled prompts **не** кэшируются (контекст меняется на каждый запрос); кэшируется только сам список skills

### 20.6 Кастомизация под бренд — 3 уровня (D70)

#### Level 1 — MVP, все тарифы: отключить не-safety skills

В `Settings → Brand → Skills` юзер видит список активных skills и для не-`safety` skills может переключить enable / disable.

```sql
ALTER TABLE brands
ADD COLUMN disabled_global_skills TEXT[] DEFAULT '{}';
-- e.g., ['evergreen-soft-hooks', 'engagement-hook-experimental']
```

#### Level 2 — Pro tier (v1.1): добавить кастомные skills бренда

Юзер Pro+ может загрузить **собственный skill** через UI. Запись создаётся в `brand_custom_skills`. Имя автоматически префиксуется `brand_{uuid}_<name>`, чтобы не пересекаться с глобальными.

```sql
CREATE TABLE brand_custom_skills (
    id              BIGSERIAL PRIMARY KEY,
    workspace_id    UUID NOT NULL,
    brand_id        UUID NOT NULL REFERENCES brands(id),
    name            TEXT NOT NULL,
    version         INTEGER NOT NULL,
    manifest_yaml   TEXT NOT NULL,
    body_md         TEXT NOT NULL,
    enabled         BOOLEAN DEFAULT true,
    is_override     BOOLEAN DEFAULT false,            -- L3 only
    overrides_skill TEXT,                              -- L3 only
    token_budget    INTEGER NOT NULL,
    created_by      UUID NOT NULL REFERENCES users(id),
    updated_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (brand_id, name, version)
);
CREATE INDEX ON brand_custom_skills (brand_id, enabled);
```

**Валидация при save:**
- Manifest — Pydantic-валидация
- Body — bleach-санитизация (удаление zero-width, control chars)
- `token_budget` — проверка через `tiktoken`: если фактический > `token_budget × 2` → 400 error
- Запрещены Jinja-includes / file references (только plain Markdown)
- Запрещены имена, совпадающие с глобальными skills (если `is_override=false`)

#### Level 3 — Agency tier (v1.2): переопределить глобальный skill

Agency может «форкнуть» глобальный skill для конкретного бренда (только те, у которых в manifest `can_override: true` и `tags` не содержит `safety` или `system`).

Создаётся запись с `is_override=true, overrides_skill='sales-hooks-and-cta'`. При компиляции для этого бренда глобальный skill пропускается, используется override.

**Audit для override:** каждое изменение custom skill пишет в `audit_log`: `(workspace_id, brand_id, user_id, action='skill_override_created'|'skill_override_updated', skill_name, version_from, version_to, diff_summary)`. Видно в админ-панели.

#### Сводка по тарифам

| Level | Базовый | Средний | Сетевой / Agency |
| --- | --- | --- | --- |
| L1 Disable не-safety skills | ✅ MVP | ✅ MVP | ✅ MVP |
| L2 Add custom brand skills | ❌ | ✅ v1.1 | ✅ v1.1 |
| L3 Override global skills | ❌ | ❌ | ✅ v1.2 |
| Approval-workflow (опционально) | ❌ | ❌ | ✅ v1.2 |
| **Disable safety skill** | ❌ never | ❌ never | ❌ never |
| **Override safety skill** | ❌ never | ❌ never | ❌ never |

### 20.7 Аудит и наблюдаемость

**`agent_runs.skills_used` (JSONB) — добавляется в схему `agent_runs` (§10.5):**

```sql
ALTER TABLE agent_runs
ADD COLUMN skills_used JSONB NOT NULL DEFAULT '[]'::jsonb;
-- e.g.: [{"name":"sales-hooks-and-cta","version":"2.1","source":"global"},
--        {"name":"brand_uuid_company-style","version":"3","source":"custom"}]
```

**Индекс для bisect при регрессии:**
```sql
CREATE INDEX agent_runs_skills_gin
ON agent_runs USING GIN (skills_used jsonb_path_ops);
```

**Pattern для дебага регрессии:**

```sql
-- Найти runs с конкретной версией skill (когда сломалось?)
SELECT id, created_at, quality_score, tokens_input
FROM agent_runs
WHERE skills_used @> '[{"name":"sales-hooks-and-cta","version":"2.1"}]'
  AND quality_score < 7
ORDER BY created_at DESC;

-- Сравнить runs до и после повышения версии
SELECT
    skills_used->0->>'version' AS sales_hooks_version,
    AVG(quality_score) AS avg_quality,
    COUNT(*) AS runs
FROM agent_runs
WHERE agent = 'content'
  AND skills_used @> '[{"name":"sales-hooks-and-cta"}]'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY 1;
```

**Метрики в Sentry / PostHog:**
- `skills.compile.latency_ms` (p50 / p95 / p99)
- `skills.compile.tokens_total` (распределение по агенту)
- `skills.dead_skill_detected` (CI alert)
- `skills.override_created` (admin notification)
- `skills.budget_exceeded` (per-brand)

### 20.8 Что меняется в других разделах

| Раздел | Что меняется |
| --- | --- |
| **§7 Агенты** | Каждый агент использует `SkillCompiler.compile()` вместо `_render_template('foo.jinja')`. Описание агента включает `default_skills: [...]` |
| **§10.5 agent_runs** | Колонка `prompt_version TEXT` → DEPRECATED, новая `skills_used JSONB` |
| **§17 Cost Observability** | Cost-per-agent дашборд показывает breakdown по skills (sales-hooks-and-cta дорогой → проверить рост tokens) |
| **§18 Admin Panel** | Новый раздел «Skills inspector»: список всех skills с usage stats, кнопка «промоутить custom skill из бренда в global» |
| **§18.5 Typed errors** | Новые error_codes: `SKILL_NOT_FOUND`, `SKILL_VALIDATION_FAILED`, `SKILL_OVERRIDE_FORBIDDEN`, `SKILL_BUDGET_EXCEEDED`, `SKILL_COMPILATION_TIMEOUT` |

---

## 21. Что мы НЕ строим в архитектуре MVP

❌ Микросервисы на каждый чих — на MVP это modular monolith (D27)
❌ Kubernetes (Docker Compose / managed VM достаточно)
❌ Сложный workflow engine (Celery / arq хватит до Temporal)
❌ Service mesh, Kafka, ClickHouse — пока нет нагрузки
❌ Мульти-регион — один регион
✅ Авторизация — самописная (D28), полный контроль
❌ Отдельный Vector DB — pgvector внутри Postgres (D29)
❌ **Hard budget caps** — на MVP observability-first, enforcement-later
❌ **Дополнительные ops-роли** (finance / compliance) — на MVP только `admin` / `support` / `user`
❌ **EN / USD / EUR** — в коде всё i18n-ready, но включаем только post-PMF
❌ **HTML-парсер каналов** — убран из архитектуры. Свои каналы → Bot API, чужие публичные → user-bot

---

## 22. Что закладываем «бесплатно» сейчас, чтобы не переписывать потом

| Закладываем | Зачем | Цена сейчас |
| --- | --- | --- |
| `parent_workspace_id` в схеме | Агентский режим в год 2 | 0 (одна колонка) |
| `Membership` как отдельная таблица | Команды / роли в v1.1 | 0 (правильная нормализация) |
| **`brands` как отдельная таблица** | Multi-brand (Денис), Brand Memory per brand | 1 день |
| **`brand_memory_overlays`** | Multi-network Brand Memory | 0.5 дня |
| RLS в Postgres | Невозможно случайно слить чужие данные | 1–2 дня настройки |
| `LLMProvider` абстракция | Смена провайдера / BYOK | 0.5 дня |
| **`ImageProvider` абстракция** | Смена Flux → DALL-E, stock fallback | 0.5 дня |
| **`PaymentProvider` абстракция** | Замена / добавление платёжных партнёров без переписывания биллинга | 0.5 дня |
| `SocialChannel` абстракция (Bot API + user-bot) | YouTube → IG → TikTok → VK; раздельный код для своих и чужих TG-каналов | 1 день |
| `TrendSource` абстракция | Любые внешние источники | 0.5 дня |
| Channel Registry с дедупликацией | Не парсить один канал N раз | 2 дня |
| `agent_runs` audit log | Объяснимость, биллинг по токенам, fine-tune | 1 день |
| **Event bus (Pydantic schemas)** | Inter-agent communication, замена Kafka | 1.5 дня |
| **`content_items` network-agnostic** | Multi-network без поля `tg_*` в общей таблице | 0.5 дня |
| **`media_assets` отдельная таблица** | Генерированные vs загруженные, cost tracking | 0.5 дня |
| Workspace.type ENUM | Расширение модели без миграции | 0 (одна колонка) |
| **`llm_calls` + `daily_cost_aggregates`** | Cost observability с первого вызова | 1 день |
| **`plan_prices` (multi-currency)** | RUB / BYN на MVP, USD / EUR post-PMF — ноль миграции | 0.5 дня |
| **`users.locale`, `users.timezone`** | i18n-ready: EN включится при необходимости | 0 |
| **`users.platform_role` + `audit_events`** (D35) | Admin Panel с первого дня без переделки auth; `admin` / `support` / `user` разделены уже на MVP | 0.5 дня |
| **`users.totp_secret` + `sessions`** | MFA для admin и session revocation | 0.5 дня |
| **`workspaces.agents_paused`** | Kill-switch без переделки LLM Guardian | 0 (одна колонка) |
| **`brand_memory_documents`** | RAG-источники и knowledge base без переделки BM-схемы | 0.5 дня |
| **`plans` + `tenant_limit_overrides` + `invoices`** | Billing-схема готова к VIP / promo overrides и multi-currency invoices | 0.5 дня |
| **`agent_runs.skills_used` JSONB + skill registry** | Skill-based архитектура агентов с дня 1 — экономия токенов и автоматическое версионирование | 1 день |

**Итого «инвестиции в будущее»** на старте: **~14–16 дней работы**, которые экономят месяцы переписывания через год.

---

## 23. Открытые вопросы

1. **Event bus транспорт:** Redis Pub/Sub vs Redis Streams? (Streams для critical events `publish` / `billing`, Pub/Sub для остальных.)
2. **Brand Memory storage:** Core Profile как JSONB или типизированные колонки? (На MVP — JSONB, типизация при стабилизации схемы.)
3. **Moderation Agent scope:** Только текст или текст + изображения? (На MVP — текст, image moderation требует отдельной модели.)
4. **Admin Panel роли:** Post-MVP разделять `support` на `ops` / `finance` / `compliance` или оставить одну роль?
5. **Когда включать hard budget caps:** при каком количестве тенантов enforcement перевешивает риск блокировки легитимных юзеров?
6. **EN-локаль:** когда включать второй язык — по Roadmap или по рынку?

---

## 24. Связанные документы

- `01-product-vision.md` — видение, 15 агентов, Brand Memory, принципы I1–I17
- `02-target-audience.md` — ICP, персоны, 13 JTBD, маппинг агентов
- `03-feature-scope.md` — EPIC A–M, MoSCoW, 8 MVP-агентов, F8 / F8b cost-логика
- `agent-cost-analysis.md` — стоимость LLM для каждого агента
- `05-tech-stack.md` — выбор технологий
- `06-roadmap.md` — поэтапный roadmap
- `07-monetization.md` — тарифы, монетизация


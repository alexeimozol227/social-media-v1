# Roadmap: от идеи до Operating System for SMM

> **Документ:** `06-roadmap.md`
> **Статус:** v1.6
> **Owner:** Мозоль Алексей
> **Дата последнего обновления:** 2026-05-14
> **Назначение:** Поэтапный план разработки и роста продукта — от первого спринта до 3-летнего горизонта. У каждой фазы — чёткая цель, deliverables и критерии завершения.

---

## 0. Если коротко (one-pager)

| Поле | Значение |
| --- | --- |
| **Сколько до публичного MVP** | 5–6 месяцев full-time для founder'а + AI-coding-ассистенты |
| **Структура движения** | Двигаемся по факту готовности фазы, не по календарю. Каждая 2-недельная итерация даёт работающий end-to-end сценарий |
| **8 агентов на MVP** | Content, Publisher, Analyst, Orchestrator, Brand Memory, Onboarding, Moderation, Notification. **Media — это пайплайн внутри Content, отдельным агентом не считается ни на MVP, ни post-MVP** |
| **Финальная картина** | **15 агентов** (8 MVP + 7 post-MVP: Strategist, Research, Engagement, Optimizer, Monitor, Repurpose + Media если выходит в самостоятельные — см. `04 §7.1`). RuleCompilerAgent — внутренний компилятор NL → DSL (v1.2), не считается отдельным агентом. CostGuardian — внутренний фоновый процесс (`04 §16.6`), не агент |
| **Концепт MVP** | 3 пилотных пользователя ведут свой Telegram-канал через нашу платформу ≥ 4 недели, подтверждают «≥ 3 часов сэкономлено / неделя» |
| **После MVP** | Сначала углубление Telegram (Engagement, Strategist, Research, Optimizer, Monitor, Repurpose, шаблоны, A/B, Agency Mode), потом — YouTube (только v2.0) |
| **Что не делаем** | YouTube/IG/TikTok раньше времени, native-mobile до 1000 платящих, видеогенерация, pay-per-use, свободный текст auto-rules |

---

## ⭐ Зафиксированные решения

> Нумерация продолжает сквозную линию: `01` (D1–D7), `02` (D8–D11), `03` (D12–D25), `04` (D26–D34, D56–D70), `05` (D35–D43). В `06` — **D44–D49**.

| # | Решение | Что это значит |
|---|---|---|
| D44 | **Срок до публичного MVP** | 5–6 месяцев при сольной работе full-time (founder + AI-coding ассистенты) |
| D45 | **Concierge MVP** | Фаза 5 — 2 месяца полировки с 3 пилотными пользователями, без новых фич. Критерий выхода — все 9 Acceptance Criteria из `03` пройдены |
| D46 | **Структура тарифов на MVP** | 3 тарифа: **Solo / Pro / Network** (детали и цифры — в `07-monetization.md`). Тарифы оперируют **брендами** (D25 из `03`), не каналами |
| D47 | **Скидки на старте** | Нет early-adopter скидок до публичного запуска. Beta-программа = первые 50 юзеров получают 50% скидку на год, но это **после** Фазы 6, не до |
| D48 | **Состав команды** | Сольный founder + AI-coding ассистенты (Devin / Cursor / Cline). Без 2-го инженера на MVP-фазах. Юрист / UX-дизайнер — на контракте по нужде |
| D49 | **Приоритет после MVP** | Сначала углубление Telegram, потом вторая соцсеть. **В первый месяц после публичного запуска (Фаза 6, месяц 7) — обязательный P1-пакет (S3–S10): Research v0, Engagement v0, дайджесты, шаблоны, UTM, A/B заголовков.** В v1.1–1.6 (месяцы 9–15) — углубление: Strategist, полный Research / Engagement, Optimizer, Monitor, Repurpose, RuleCompilerAgent, Agency Mode. **YouTube — только v2.0 (месяцы 16–18)**, при явном спросе или плато роста на TG |

---

## 1. Принципы roadmap

| Принцип | Что значит |
|---|---|
| **Фазы, а не дедлайны** | Движение по факту готовности фазы. Календарные даты — ориентировочные |
| **Vertical slices** | Каждая 2-недельная итерация даёт работающий end-to-end сценарий, который пользователь может пощупать |
| **Risk-first** | Самые рискованные технические гипотезы (качество AI-контента, лимиты Telegram Bot API, чтение каналов конкурентов) проверяем в первых спринтах |
| **Build → Measure → Learn** | После каждой фазы — sanity-check метрик и feedback от пилотов |
| **Concierge MVP перед публичным запуском** | Перед открытием регистрации работаем для 3 пилотов вручную там, где не успели автоматизировать (D45) |
| **Stop-the-bleeding** | Если на любой фазе пилоты не подтверждают North Star (≥ 3 ч/нед сэкономлено) — пересматриваем фичи, а не релизим |
| **Trust > Speed** | Лучше задержать фичу, чем выпустить AI, который может опубликовать что-то неприемлемое. Moderation Agent — в MVP, не post-MVP |
| **i18n / multi-currency / multi-tz — закладываем «бесплатно»** | Все user-facing строки — через `useTranslations()` с первого спринта. Поля `users.locale` / `users.timezone` / `*.preferred_currency` и таблица `plan_prices(plan, currency, period, effective_from)` заводятся сразу. EN-локаль включается без переписывания. См. `04 §18` |

---

## 2. Команда и допущения для оценки

> Все оценки ниже — для **минимальной команды**: 1 fullstack-инженер (founder) + AI-coding ассистенты (Devin / Cursor / Cline) для пар-программирования. Если присоединится 2-й инженер — сроки сокращаются на ~30–40%.

| Роль | Загрузка |
|---|---|
| Founder / fullstack-инженер | Full-time |
| AI-coding ассистенты (Devin / Cursor / Cline) | Постоянно |
| Product / Design (он же founder) | На брейнштормы и дизайн |
| Юрист (контракт) | ~5 часов перед публичным запуском |
| Дизайнер UX (контракт) | ~20 часов на лендинг + базовые UI-флоу |

**Если бюджета на дизайнера нет:** используем готовые блоки от shadcn/blocks + готовые лендинги от 21st.dev / Tailwind UI.

---

## 3. Итоговая шкала (high-level)

```
Месяц:    0    1    2    3    4    5    6    7    8    9   10   11   12   ...   24
          ├────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────────────┤
Фаза 0    [PRE]
Фаза 1         [FOUNDATIONS───────]
Фаза 2                   [CONTENT + MEDIA + MODERATION─]
Фаза 3                              [PUBLISH + ANALYTICS]
Фаза 4                                       [BILLING + POLISH]
Фаза 5 (MVP)                                          [CONCIERGE MVP─]
Фаза 6                                                       [v1.0 PUBLIC]
Фаза 7 (Post-MVP)                                                  [v1.1───v2.0──...]
```

**Срок до публичного MVP:** ~5–6 месяцев при полной загрузке (D44).

---

## 4. Фаза 0: Pre-development (недели 0–2)

**Цель:** Подготовить окружение, чтобы первый спринт был сразу про код, а не про настройку.

### Задачи

| # | Задача | Срок | Кто |
|---|---|---|---|
| P0.1 | Зарегистрировать юр. лицо (форма на выбор) | 5–10 раб. дней | Founder |
| P0.2 | Зарегистрироваться как оператор персональных данных у регулятора | 5 раб. дней | Founder + юрист |
| P0.3 | Открыть расчётный счёт | 1 неделя | Founder |
| P0.4 | Зарегистрироваться у платёжного провайдера (мерчант-аккаунт) | 1 неделя | Founder |
| P0.5 | Зарегистрироваться у LLM-шлюза (через который идут все вызовы LLM и Image-моделей) | 1 час | Founder |
| P0.6 | Зарегистрироваться у email-провайдера, верифицировать домен (SPF / DKIM / DMARC) | 2 часа | Founder |
| P0.7 | Зарегистрироваться в GitHub, создать приватный репозиторий (монорепо: `apps/backend/` + `apps/web/`) | 30 минут | Founder |
| P0.8 | Зарегистрироваться в Sentry (cloud-free-tier на старте) | 30 минут | Founder |
| P0.9 | Поднять локальный Docker Compose: Postgres 16 + pgvector + Redis 7 + MailHog + PostHog + Unleash + MinIO + PgBouncer | 1 день | Founder |
| P0.10 | Создать Telegram-бота через @BotFather (рабочий + dev) | 30 минут | Founder |
| P0.11 | Юр. документы: User Agreement, Privacy Policy, Согласие на обработку ПД | 5 часов | Юрист |
| P0.12 | Лендинг-черновик (одна страница: видение + waitlist) | 3 дня | Founder + дизайнер |
| P0.13 | Customer Discovery: 15–20 интервью с потенциальными юзерами (D11 из `02`) | 3 недели (параллельно) | Founder |

> **Хостинг и домен** (продакшен-VPS / DNS / S3) **в Фазе 0 не покупаем.** Всё разрабатываем локально (Docker Compose). Хостинг и домен заводим в Фазе 6 — когда продукт готов к публичному запуску.

### Deliverables Фазы 0

- Юр. лицо зарегистрировано, оператор ПД уведомлён
- Все аккаунты внешних сервисов готовы (LLM-шлюз, email-провайдер, платёжный провайдер)
- Локальное окружение поднято (Docker Compose)
- Чистый монорепо (`apps/backend/` + `apps/web/`) с базовой `docs/` (01–05) и `README.md`
- Лендинг с waitlist собирает заявки
- ≥ 15 проведённых интервью с инсайтами

### Критерий завершения

> «За один день можно начать писать первый продакшн-код, никаких блокеров со стороны инфраструктуры или юридики».

---

## 5. Фаза 1: Foundations (спринты 1–3, недели 2–8)

**Цель:** Заложить фундамент архитектуры из `04`. Внутренняя alpha с авторизацией, multi-tenancy, event bus и одним подключённым каналом.

### Спринт 1: Skeleton (недели 2–4)

**Что делаем:**

- **Backend:** FastAPI 0.115+ шаблон, SQLAlchemy 2.0 (async, asyncpg), Alembic, модульный монолит (см. `05 §2.1`)
- **Frontend:** Next.js 15 + Tailwind 4 + shadcn/ui начальный setup; TS strict; route-группы `(public)/`, `(auth)/`, `(app)/`, `(admin)/`
- **Postgres 16 + pgvector + pg_trgm + pg_partman + pg_cron** + миграции для базовых таблиц (`users`, `workspaces`, `workspace_memberships`, `brands`, `refresh_tokens`, **`audit_events`** с помесячным партиционированием через pg_partman)
- **pg_cron retention job `retention_audit_log_cold_archive`** (monthly) заводится **в той же миграции, что и `audit_events`** — со статусом `active=false` (`pg_cron.schedule(...; SELECT cron.alter_job(jobid, active := false)`). Активация — в Спринте 8 вместе с остальными 4 retention jobs (D57 в `04 §18.5`)
- **Postgres RLS + PgBouncer transaction pooling** (D65 из `04`) + FastAPI dependency-обёртка с `SET LOCAL app.current_tenant_id` / `app.current_user_id` / `app.platform_role` (см. `04 §18.7`, `05 §2.4.1`) + CI-линтер `tools/lint_set_local.py` запрещает `SET app.*` без `LOCAL`
- **Самописная авторизация** (D28 из `04`, D36 из `05`): JWT (15 мин access) + refresh-токен (30 дней, HttpOnly cookie, ротация + revoke семьи при reuse) + email/password + email verification + MFA (TOTP). Все эндпоинты `/v1/auth/...` в kebab-case (`/v1/auth/register`, `/v1/auth/login`, `/v1/auth/logout`, `/v1/auth/refresh`, `/v1/auth/forgot-password`, `/v1/auth/reset-password`, `/v1/auth/mfa/setup`, `/v1/auth/mfa/verify`)
- **JWT strict claims + Redis membership cache** (D64): JWT хранит только `user_id` / `platform_role` / `active_workspace_id` / `exp` / `jti`. Memberships → Redis `user:{id}:memberships` TTL 5 мин. WS-push `auth.refresh_required` при изменении ролей
- **Typed API errors** (D62): Pydantic `AppError` базовый класс + error registry (`LLM_BUDGET_EXCEEDED`, `MODEL_TIMEOUT`, `CIRCUIT_BREAKER_OPEN`, `MODERATION_BLOCKED`, `SKILL_NOT_FOUND`, `SKILL_VALIDATION_FAILED`, `SKILL_OVERRIDE_FORBIDDEN`, `SKILL_BUDGET_EXCEEDED`, `SKILL_COMPILATION_TIMEOUT`, …) + FastAPI exception handler → JSON `{error_code, message, suggested_action, retry_after_seconds}` (RFC 7807). Frontend `useApiError` хук для toast + CTA
- **Локаль агентов — system prompt всегда en** (D63 в `04 §18.2.1`): все агент-skills (см. ниже) пишутся с **english system prompt** — Claude / GPT лучше токенизируют, дешевле, точнее следуют инструкциям. Язык генерируемого поста — `brand.content_language` (директива в `content-agent-base` skill с подстановкой при компиляции). UI-уведомления — `user.locale`. CI-чек `scripts/check_system_prompt_lang.py` ловит кириллицу в `apps/backend/skills/**/SKILL.md` тело system prompt'а
- **Skill-инфраструктура** (D68 / D69 / D70 из `04`): директория `apps/backend/skills/` + Pydantic-схема `SkillManifest` (frontmatter с `when_to_use` YAML DSL, `tags`, `customizable`, `token_budget`) + `SkillRegistry.load_all()` при старте + `SkillCompiler.compile(agent, context)` с детерминированной сортировкой и проверкой token-budget. Миграция Alembic для `agent_runs.skills_used JSONB` + GIN-индекс. Миграция `brands.disabled_global_skills TEXT[]`. Зависимости: `pydantic-yaml`, `google-re2`, `python-frontmatter`, `tiktoken`. CI: `scripts/validate_skills.py` (pre-commit), `tests/skills/test_dsl.py`, `tests/skills/test_static_analysis.py`. См. `04 §19`, `05 §3.4`
- **i18n-инфраструктура** (`04 §18.1`): `next-intl@^3` подключён сразу. `apps/web/messages/ru.json` (основной) + пустой `apps/web/messages/en.json` (системные страницы 404/500). **Никаких хардкодед RU-строк в новых компонентах — только через `useTranslations()`.** Routes без locale-prefix на MVP (RU-only). Даты / числа — через `Intl.DateTimeFormat` / `Intl.NumberFormat` (хелперы в `apps/web/lib/i18n/format.ts`). Pre-commit lint-rule запрещает кириллицу в `*.tsx` вне `messages/*.json` и тестов. CI-чек `scripts/i18n_audit.ts` сверяет ключи `ru.json ⇄ en.json`
- **i18n / multi-currency / multi-tz схема БД** (`04 §18`, `04 §22`): поля `users.locale` (default `ru-RU`), `users.timezone` (default `Europe/Minsk`), `users.preferred_currency` (default `RUB`); `workspaces.preferred_currency`; `brands.content_language` (default `ru`), `brands.timezone`. **Все timestamp-поля — `TIMESTAMPTZ` UTC**; CI-чек `scripts/check_timestamptz.py` ловит `TIMESTAMP WITHOUT TIME ZONE`
- **Multi-currency billing skeleton** (`04 §9.6`): таблицы `plans` + `plan_prices(plan_id, currency, period, effective_from)` + `tenant_limit_overrides` + `invoices(... currency, exchange_rate, reference_amount_usd)` заводятся сразу (на MVP — только RUB / BYN row'ы, USD / EUR — post-PMF без миграций). Cost-валютная денормализация: `agent_runs.cost_rub`, `llm_calls.input_cost_usd` + `output_cost_usd` + `cost_rub`, `media_assets.cost_rub`
- **Event Bus skeleton** (D32, D41): `apps/backend/events/schemas.py` + `apps/backend/core/event_bus.py` (Redis Pub/Sub + Pydantic discriminated unions). Первое событие — `user.registered`
- **WebSocket skeleton** (D43, П9): FastAPI WS-route с JWT-auth + Next.js хук `useRealtime`. Первый use-case — toast «добро пожаловать»
- **Unleash client** (D42): wrapper в `apps/backend/core/feature_flags.py` + первый флаг `enable_auto_publish` (для проверки kill-switch)
- **Idempotency middleware** (П13): таблица `idempotency_keys` + декоратор для эндпоинтов
- **Sentry + structlog** настроены (П8, 12-Factor)
- **OpenTelemetry** — базовая инструментация FastAPI + SQLAlchemy + Celery
- **CI:** GitHub Actions с lint (ruff + biome) + typecheck (mypy strict + tsc) + test (pytest + Vitest) + build

**Vertical slice:**

> Юзер регистрируется → получает email-верификацию (шаблон в `users.locale`, время в `users.timezone`) → подтверждает → логинится → создаётся workspace + default brand (`content_language=ru`, `preferred_currency=RUB`) → видит пустой dashboard на русском. WebSocket показывает live-toast «добро пожаловать».

**Метрики:**

- 0 ошибок в Sentry на критическом флоу signup → verify → login
- Все интеграционные тесты на Auth + tenancy + idempotency зелёные
- CI `i18n_audit.ts` зелёный (нет хардкодед RU в `*.tsx`, ключи `ru.json ⇄ en.json` синхронны)

### Спринт 2: Channel Registry + Telegram (недели 4–6)

**Что делаем:**

- **aiogram 3.x** — Telegram-бот для каналов пользователя (бот = админ): подключение, права, чтение событий канала, публикация, callback-кнопки
- **Global Channel Registry** (D20 из `03`): схема `channels`, `channel_posts`, `workspace_channels`, embeddings. Дедупликация: один канал парсится один раз для всех тенантов
- **pg_cron retention job `retention_channel_posts_cold_archive`** (weekly) заводится **в той же миграции, что и `channel_posts`** — статус `active=false` до Спринта 8 (D57 в `04 §18.5`)
- **user-bot на Pyrogram** (D40 из `05`, `05 §5.2`): пул аккаунтов с `api_id` + `api_hash`, ротация, healthcheck. **Только для чтения публичных каналов конкурентов**, в которых наш бот не админ (Research, Monitor, Inspiration Board)
- **Партиционирование embeddings через pg_partman + HNSW** (D61 в `04 §18.6`): `channel_post_embeddings_template` со всеми индексами (включая HNSW), `partman.create_parent(...)` партиционирует помесячно. `pg_cron` safety-check 25-го числа → Sentry alert, если HNSW отсутствует в новой партиции
- API: `POST /v1/brands/{id}/channels` — добавление канала, проверка прав бота (Bot API `getChatMember` / `getChatAdministrators`)
- Парсинг истории канала (последние 100–500 постов через Bot API)
- Webhook на новые посты в подключённом канале + публикация события `channel.post_received` в event bus
- Frontend: «Подключить канал» — wizard с инструкцией добавить бота + проверка
- Workspace settings UI (переключатель брендов, активный brand в JWT claims + хедер `X-Active-Brand-Id`)

**Vertical slice:**

> Юзер подключает свой Telegram-канал → платформа парсит последние 100 постов → они видны в дашборде. Событие `channel.post_received` логируется в `agent_runs` (audit log).

**Метрики:**

- Подключение канала ≤ 3 минут (включая чтение инструкции) — часть TTFAA
- 100 постов загружены и сохранены с метриками (просмотры, реакции)
- Дедупликация работает: повторное подключение того же канала вторым юзером — instant (берём из Registry)

### Спринт 3: Brand Memory + Onboarding Agent + LLMProvider (недели 6–8)

**Что делаем:**

- **`LLMProvider`** абстракция (D22 из `03`) + первая реализация (через LLM-шлюз, D38 из `05`)
- **Brand Memory** — двухслойная схема (D33 из `04`): `brand_memory_core` (JSONB) + `brand_memory_overlays` (JSONB + FK на channel) + `brand_memory_examples` (text + pgvector). Привязка к **бренду**, не каналу
- **`OnboardingAgent`** (D24 из `03`, MVP):
  - Авто-экстракция Brand Memory из последних 50 постов (цель — ≤ 15 минут, D15 из `03`)
  - Cold-start wizard для каналов с < 10 постов (5–7 вопросов) — D18 из `03`
- **Указание конкурентов-референсов** (3–5 каналов, D23 из `03`) на onboarding — читаются user-bot'ом на Pyrogram
- **Семантический индекс** истории канала (pgvector embeddings через `text-embedding-3-small`)
- **`BrandMemoryService`** (single source of truth — П11): единственный интерфейс доступа агентов к BM
- **Audit Log** — таблицы `agent_runs` + `llm_calls` + базовый писатель (П5, П12): все вызовы `LLMProvider` логируются с `(workspace_id, brand_id, agent, model, prompt_tokens, completion_tokens, cost_usd, cost_rub, prompt_hash)`
- **pg_cron retention jobs `retention_chain_of_thought` (daily) и `retention_llm_calls_aggregate` (daily)** заводятся **в тех же миграциях, что и `agent_runs` / `llm_calls`** — статус `active=false` до Спринта 8 (D57 в `04 §18.5`)

**Vertical slice:**

> Юзер заполняет Brand Memory через Onboarding Agent (или wizard для cold-start) → добавляет 3 канала-конкурента → платформа читает их публичные посты (user-bot Pyrogram) → готов «5-уровневый контекст» (Brand Memory → История → Конкуренты → Тренды → Запрос). Промежуточный TTFAA-замер: BM extracted ≤ 15 минут.

**Метрики:**

- Brand Memory wizard / экстракция завершают ≥ 80% юзеров (на пилотных тестах)
- Чтение 5 каналов конкурентов выполняется ≤ 5 минут
- Embedding pipeline стоит ≤ $0.05 на канал из 500 постов

### Deliverables Фазы 1

- Скелет приложения с самописной авторизацией, multi-tenancy, RLS, Event Bus, WebSocket, Unleash, Idempotency
- Global Channel Registry с дедупликацией (D20)
- Telegram-интеграция: Bot API (свои каналы) + user-bot Pyrogram (публичные каналы конкурентов)
- Brand Memory (двухслойная — Core + Overlay) + Onboarding Agent (auto-extract + cold-start)
- `LLMProvider` + Audit Log

### Критерий завершения

Внутреннее demo: founder как юзер доходит от регистрации до состояния «Brand Memory готов (Core + TG-Overlay), история и конкуренты проиндексированы, audit log пишется». Это ещё не продукт, но уже фундамент для AI-агентов.

---

## 6. Фаза 2: Content + Media + Moderation (спринты 4–6, недели 8–14)

**Цель:** Главная ценность платформы — Content Agent с MarkdownV2, картинками и pre-publish модерацией. После этого пилоты могут давать обратную связь по качеству контента.

### Спринт 4: Content + Moderation + Orchestrator (недели 8–10)

**Что делаем:**

- **`BaseAgent`** абстракция (с tools, memory, audit) — общий контракт для всех 8 MVP-агентов
- **`ContentAgent` v0** — Claude Sonnet 4.6 (модель из `04 §2`). Генерация одного поста по теме. Использует `SkillCompiler.compile()` (D68) вместо монолитного промпта
- **8 базовых skills** (D68 из `04 §19`) — пишутся **сразу в skill-формате**, не как Jinja-шаблоны:
  1. `content-agent-base/SKILL.md` (общие правила Content Agent, `tags=[system]`)
  2. `prompt-injection-defender/SKILL.md` (always-on, `tags=[safety]`) — D58
  3. `tg-markdownv2-formatter/SKILL.md` (escape-правила, формат) — переиспользуется Publisher
  4. `brand-voice-applier/SKILL.md` (чтение Brand Memory Core + Overlay)
  5. `5-level-context-merger/SKILL.md` (BM → История → Конкуренты → Тренды → Запрос, D18)
  6. `sales-hooks-and-cta/SKILL.md` (когда `post_type ∈ {sales, product_launch}`)
  7. `evergreen-soft-hooks/SKILL.md` (когда `post_type ∈ {educational, lifestyle, opinion}`)
  8. `auto-rules-evaluator/SKILL.md` (когда `brand.auto_rules` не пуст, `tags=[safety]`) — D56
- **Локаль агентов** (D63 в `04 §18.2.1`): system prompt — всегда `en` (точнее instruction-following, дешевле токены); output language → `brand.content_language` (язык канала бренда); UI-уведомления → `user.locale`. Реализация: language directive в `content-agent-base` skill с подстановкой `{{ brand.content_language }}` при компиляции. `agent_runner.build_prompt(brand, output_lang_override=None)` — опциональный override для Inspiration Board L3 «адаптируй под мой ru-канал» из en-референса
- **Tool calling** через LangChain 0.3: `search_brand_memory`, `search_channel_history`, `search_competitors`
- **`ModerationAgent` v0** (D24, EPIC-H из `03`) — GPT-4o-mini. Pre-publish-фильтр: rule-based (regex по табу из BM + чёрный список) + LLM-judge для тонких случаев. Действия: `pass`, `flag_for_review`, `block`
- **Input sanitization pipeline** (D58 в `04 §17.4`) — `apps/backend/safety/input_sanitizer.py`. Любой текст из публичных каналов / комментариев / Inspiration Board, попадающий в LLM context, проходит:
  1. PII redact: URL → `[URL_REDACTED]`, phone → `[PHONE_REDACTED]`, email → `[EMAIL_REDACTED]`
  2. `bleach` (HTML-санитизация) + удаление zero-width / control chars
  3. Prompt-injection denylist: «ignore previous instructions», «system:», jailbreak-паттерны
  4. URL allow-list для Inspiration Board: только домены каналов из Channel Registry
  5. Snapshot-тесты `tests/safety/test_prompt_injection.py` — корпус из 50+ известных injection-атак
- **`OrchestratorAgent` v0** (D24, EPIC-I из `03`) — GPT-4o-mini. Координирует цепочку Content → Moderation → (Media в Спринте 6) → Publisher (в Спринте 7) через event bus
- API: `POST /v1/posts/generate` (асинхронно через Celery, возвращает `job_id`), `GET /v1/jobs/{id}` + WS-событие `post.draft_generated`
- Frontend: страница «Сгенерировать пост» с textarea + result + редактирование. Live-обновление через WebSocket

**Vertical slice:**

> Юзер вводит «напиши пост про X» → Orchestrator вызывает Content Agent → Moderation Agent проверяет → юзер получает черновик в MarkdownV2 с учётом тона канала через WS. Audit Log показывает chain-of-thought.

### Спринт 5: Editor + MarkdownV2 + Brand Memory Agent (недели 10–12)

**Что делаем:**

- **Tiptap 2** интеграция + кастомные extensions (D17 из `03`)
- TG MarkdownV2 сериализатор (Tiptap doc → TG syntax с escape `_ * [ ] ( ) ~ \` > # + - = | { } . !`)
- TG MarkdownV2 парсер (для импорта черновиков от Content Agent — B7 из `03`)
- Preview mode «как видит подписчик» с TG-стилизацией
- Spoiler extension, code-blocks, цитаты
- **Inline-improve:** «улучши», «короче», «другой тон» (Content Agent с `mode=refine`)
- API: `POST /v1/posts/{id}/refine` + `POST /v1/posts/{id}/save-draft`
- **`BrandMemoryAgent` v0** (D24, EPIC-K из `03`) — GPT-4o-mini. Фоновое обновление BM на основе свежих постов: каждую неделю Celery Beat обновляет статистики тона, длины, успешных тем

**Vertical slice:**

> Юзер получает пост → правит в редакторе → нажимает «улучши» → агент даёт новый вариант с сохранением форматирования. По ночам BrandMemoryAgent обновляет overlay свежими паттернами.

### Спринт 6: Media (под капотом Content) + План на неделю (недели 12–14)

**Что делаем:**

- **Media-пайплайн** (D13 из `03`, D40 из `05`) — работает **внутри Content Agent**, отдельным агентом на MVP не считается. Реализация: Content Agent после генерации текста вызывает `ImageProvider` (Flux-2 Pro по умолчанию, Nano Banana как cost-control). Анализирует Brand Memory `visual_guidelines` → формирует промпт → ImageProvider → result
- `ImageProvider` абстракция (`apps/backend/adapters/image/`) с двумя реализациями (Flux-2 Pro + Nano Banana) + routing по типу поста / тарифу
- **Media cache per-brand** (D60 в `04 §13.5`): Redis key `media:cache:{brand_id}:{sha256(prompt|size|lora|visual_version)} → s3_url`, TTL 30 дней. Шеринг между брендами запрещён (multi-tenancy + brand consistency). Инвалидация при изменении BM `visual_block` (через WS-событие → `DEL` всех ключей бренда)
- Загрузка результата в S3-совместимое хранилище (через адаптер) + public-link с long-cache headers
- Юзер может: принять, заменить (новая генерация), загрузить свой файл
- **«План на неделю»** — НЕ через Strategist Agent (он post-MVP), а через Content Agent в batch-режиме: 5–7 тем извлекаются из Brand Memory + истории + трендов (tool `get_weekly_topics()`); каждая тема разворачивается в полный пост (отдельная Celery-задача)
- API: `POST /v1/brands/{id}/weekly-plan` (`job_id` + WS-события по мере готовности каждого поста)
- Frontend: страница «План на неделю» с табами по дням + drag-and-drop для перестановки

**Vertical slice:**

> Юзер нажимает «План на неделю» → через 30–90 сек видит 7 тем + черновики + AI-визуалы → выбирает понравившиеся → правит → сохраняет. Каждый пост уже прошёл Moderation Agent.

### Deliverables Фазы 2

- 5 новых MVP-агентов работают: Content + Moderation + Orchestrator + Brand Memory + Onboarding (с Фазы 1) — итого 5 из 8 MVP-агентов
- **System prompt агентов — всегда `en`** (D63 в `04 §18.2.1`): все базовые skills (`content-agent-base`, `prompt-injection-defender`, `tg-markdownv2-formatter`, `brand-voice-applier`, `5-level-context-merger`, `sales-hooks-and-cta`, `evergreen-soft-hooks`, `auto-rules-evaluator`) написаны на английском; язык выхода — `brand.content_language`; UI-уведомления — `user.locale`. CI-чек `scripts/check_system_prompt_lang.py` вводится в pre-commit и не пропускает кириллицу в теле system prompt
- Media-пайплайн встроен в Content Agent (отдельным агентом не считаем, ни на MVP, ни post-MVP)
- Tiptap-редактор с TG MarkdownV2 (D17)
- Inline-improve flow
- Audit Log пишет все agent runs с cost-метаданными (П12)
- Media-генерация в green-zone unit-economics (≤ 5 ₽ / картинка)
- «План на неделю» собирается ≤ 90 сек на полный батч

### Критерий завершения

3 пилотных юзера могут сгенерировать пост + визуал и **подтвердить:** «Это близко к моему стилю» (≥ 6 / 10 по субъективной оценке) и **AI Acceptance Rate ≥ 60%** на этой фазе (доводим до 70%+ в Concierge MVP).

---

## 7. Фаза 3: Publishing + Notifications + Analytics (спринты 7–9, недели 14–20)

**Цель:** Замкнуть цикл — от черновика до опубликованного поста с измерением метрик и уведомлениями.

### Спринт 7: Publisher + Notification + Расписание (недели 14–16)

**Что делаем:**

- **Celery 5 + Celery Beat** настройка (D35 из `05`) — `apps/backend/workers/celery_app.py` с beat-schedule (см. `05 §2.5`)
- **`PublisherAgent`** (D24 из `03`) — GPT-4o-mini. Публикация поста в канал через Bot API. Идемпотентность по `idempotency_key = (post_id, channel_id, scheduled_at)` (П13)
- Очередь публикаций с retry-логикой (exponential backoff, `task_acks_late=True`)
- **Календарь публикаций** (EPIC-C, C6 из `03`) — полноценный UI: день / неделя / месяц, drag-and-drop постов на слоты через Zustand + dnd-kit. Списки запланированных постов > 200 виртуализированы через `@tanstack/react-virtual` (D43 из `05 §6.3.1`). Статусы `draft / scheduled / published / failed`, фильтры по бренду / каналу / автору. Tz-aware: `scheduled_at` — `TIMESTAMPTZ` UTC, на UI render через `Intl.DateTimeFormat(users.timezone)`. Drag-and-drop на mobile в Month-view деградирует на Week-view
- **Toggle Full-auto vs Approve mode** (D5 из `01`) — на уровне канала + per-post. Реализован через Unleash флаг `auto_publish_enabled` (per-brand) + safety-net Moderation Agent (П10)
- **Auto-rules preset UI** (D56 в `04 §8.6.2` / C4 в `03`) — закрытый каталог из ~12 чекбоксов в Settings → Publishing: `max-post-length`, `forbid-external-links`, `quiet-hours-start/end`, `daily-cap`, `auto-approve-evergreen`, `require-human-for-sales`, `require-human-for-news`, `min-sentiment-score`, `forbid-emoji-spam`, `weekend-mode`, `require-image-for-product-posts`, `auto-skip-on-holiday`. **Свободный текст правил запрещён** — interpretation drift; NL → DSL `RuleCompilerAgent` отложен на post-MVP (v1.2). Хранение: `brand.auto_rules JSONB` с Pydantic-схемой
- **Kill-switch:** глобальный флаг `kill_switch_publishing` в Unleash. При взведении — все запланированные публикации останавливаются
- **`NotificationAgent` v0** (D24, EPIC-J из `03`; routing matrix — `04 §8.6.1`) — GPT-4o-mini. Маршрутизация «анти-spam»:
  - **Оперативные события — только Telegram-бот (aiogram, inline-кнопки):** J1 «черновик готов», J2 «требуется approve», J3 «moderation заблокировал», J4 «опубликовано / ошибка»
  - **Email — только редкие системные письма:** верификация / password reset, биллинг (J5), еженедельный отчёт (J6). Шаблоны email — locale-aware (`04 §18.2`): локаль из `users.locale`, дата / число — `Intl.*`, время — в `users.timezone`. Шаблоны хранятся в `apps/backend/templates/email/{ru,en}/`
  - Telegram chat_id привязывается через `/start <user_token>` deep-link в onboarding; если не привязан — в UI CTA «Привяжите TG-бот для оперативных уведомлений»
- **Таблица `notifications`** + **pg_cron retention job `retention_notifications_hard_delete` (daily)** заводятся **в одной миграции** — статус `active=false` до Спринта 8 (D57 в `04 §18.5`)
- **Откат публикаций** — Bot API `deleteMessage`

**Vertical slice:**

> Юзер запланировал пост на завтра 10:00 → Orchestrator → Moderation → Publisher → пост опубликовался автоматически → NotificationAgent прислал TG-бот уведомление «J4 опубликовано в Канале X». Email не отправляется (не засоряем почту). Все 4 агента в цепочке отметились в audit log.

### Спринт 8: Analyst + Дашборд + Cost Guardian + Retention (недели 16–18)

**Что делаем:**

- **Сбор метрик** с TG: просмотры, реакции, шеры (Bot API)
- Celery Beat job: ежечасное обновление метрик за свежие посты, ежедневное за старые (1 неделя+)
- **Дашборд канала** (Recharts) — график постов, топ-3, средние просмотры, динамика подписчиков
- **`AnalystAgent`** (D24) — Gemini 2.5 Pro (модель из `04 §2`). Еженедельный AI-отчёт: что зашло, рекомендации; cron вс 9:00 (в `users.timezone`) через Celery Beat
- **Email-отчёт (J6)** — NotificationAgent доставляет шаблон с данными от AnalystAgent (редкий случай email-коммуникации)
- **North Star метрика:** расчёт «часов сэкономлено» = (постов × 15 мин на ручную работу) + (визуалов × 20 мин) + (отчёт × 60 мин). Видна пользователю в дашборде и в PostHog (admin property)
- **Cost Observability Dashboard — internal-only** (F8 из `03`, П12, D37 в `04`) — **только в админ-панели `/v1/admin/llm-calls` и `/v1/admin/tenants/{id}`**. В клиентском UI расходомера НЕТ — подписочная модель, не pay-per-use:
  - Cost per (brand × agent × month) — только для founder / ops
  - Token consumption по моделям
  - Images по провайдерам (Flux-2 Pro vs Nano Banana)
  - `daily_cost_aggregates` денормализует cost в USD (atomic-source-of-truth) + RUB (через `invoices.exchange_rate` snapshot)
  - Алерты в TG-чат команды при cost-spike → ручная реакция admin (kill-switch / impersonate)
- **CostGuardian (внутренний компонент, F8 из `03`)** (D59 + D66 в `04 §16.6` / `§16.7`) — НЕ из 8 MVP-агентов; внутренний фоновый процесс с автодействиями:
  - **Per-post triggers:** уровень T1 → admin alert; T2 → auto-downgrade модели (Sonnet 4.6 → Haiku 4.5) на 24 ч + TG-уведомление юзеру «временно используем эконом-модель»; T3 → throttle auto-publish 60 мин + strict Moderation; T4 → kill-switch full-auto → approve-only
  - **Per-brand monthly cap** (от подписочная цена × 0.6): 60% → admin alert (Sentry); 80% → TG-бот юзеру + upgrade CTA; 100% → `auto_publish_enabled=false`, реактивация — следующий месяц или upgrade
  - Реализация: `pg_cron` job каждые 30 мин для MTD-агрегата + Celery worker `cost_guardian_react` для real-time реакции на per-post events
- **Лимиты тарифа для пользователя (F8b из `03`)** — UI «Usage» в кабинете: прогресс по лимитам тарифа в **постах** и **AI-генерациях** (не в токенах, не в рублях). При достижении 100% по конкретному лимиту блокируется именно та фича, по которой исчерпан лимит (например, AI-генерации картинок), всё остальное продолжает работать. Автопубликация **не отключается** — это часть подписки. Эндпоинт `GET /v1/billing/usage`
- **Активация Data Retention Policy** (D57 в `04 §18.5`) — все 5 `pg_cron` retention jobs были заведены **вместе с соответствующими таблицами** в Спринтах 1, 2, 3, 7 со статусом `active=false`. В Спринте 8 — финальная активация и мониторинг:
  1. `retention_chain_of_thought` (daily 3:00, заведён в Спринте 3) — `UPDATE agent_runs SET chain_of_thought = NULL, retrieved_context = NULL WHERE created_at < now() - interval '30 days' AND opt_in_training = false`
  2. `retention_llm_calls_aggregate` (daily 3:30, заведён в Спринте 3) — агрегирование `llm_calls` в `llm_calls_daily` за день N-91 + delete raw
  3. `retention_channel_posts_cold_archive` (weekly Sun 4:00, заведён в Спринте 2) — экспорт партиций > 180 дней в cold-storage (S3), drop партиции из БД
  4. `retention_notifications_hard_delete` (daily 3:00, заведён в Спринте 7) — `DELETE FROM notifications WHERE created_at < now() - interval '30 days'`
  5. `retention_audit_log_cold_archive` (monthly, заведён в Спринте 1) — экспорт audit_log старше 2 лет в cold-storage
  - Активация: одна миграция `SELECT cron.alter_job(jobid, active := true) FOR ...` для всех 5 jobs + дашборд `/v1/admin/retention-jobs` со статусом и последним выполнением
  - **Opt-in для дообучения** (D67) — Settings → Privacy → toggle «Разрешить использовать мои данные для улучшения AI». По умолчанию `false`. При `true` — pgsql-trigger обходит retention для `agent_runs` (анонимизация: `user_id` → hash)
- **AI «Explainability» UI** (П5): для каждого поста / отчёта — модал «как это сделано» с моделью, prompt, retrieved context, cost

**Vertical slice:**

> Юзер ведёт канал неделю → в понедельник 9:00 получает email с AI-отчётом + дашборд показывает данные. Founder видит в админке: «Бренд X израсходовал 80% бюджета — Media-пайплайн» → throttle включён автоматически.

### Спринт 9: Multi-brand + TG-бот approve + Inspiration Board (недели 18–20)

**Что делаем:**

- **Multi-brand UI** (D12 из `03`, D25 терминология): юзер с тарифом Pro / Network может создать N брендов в одном workspace. У каждого бренда — своя Brand Memory (Core + Overlays). Multi-channel — отдельная грань (один бренд может иметь TG + IG + YT, но на MVP — только TG)
- Switcher брендов в навигации (хедер) + per-brand `X-Active-Brand-Id` хедер на API-запросах
- Сводный дашборд по всем брендам в workspace
- **TG-бот approve flow** (S5 из `03`) через aiogram FSM: inline-кнопки «Подробнее / Approve / Reject / Edit». Edit открывает MiniApp с Tiptap-редактором
- **Inspiration Board** (EPIC-L из `03`, MVP Must — все 4 фичи L1–L4) — доска подборок постов конкурентов:
  - **L1 — Сетка карточек** с лучшими постами конкурентов (из Channel Registry — читаются user-bot'ом на Pyrogram). Фильтры: ниша, формат, метрики, период. Виртуализация через `@tanstack/react-virtual` (D43 из `05 §6.3.1`) — плавная прокрутка даже на 10K карточек
  - **L2 — Сохранение в личную подборку**, теги. Таблицы `brand_inspiration_boards` (n штук подборок per бренд) + `brand_inspiration_items`
  - **L3 — Кнопка «Сгенерировать наш вариант»** → Content Agent с контекстом Brand Memory + референса (после прохода input_sanitizer pipeline) → черновик в нашем тоне (не копия)
  - **L4 — Еженедельный «свежий drop»** (MVP, не post-MVP): Celery Beat job (пн 09:00 в `users.timezone`) — `BrandMemoryAgent` отбирает 10–20 топ-постов конкурентов за неделю (по interaction-score + новизне) → попадают в раздел `is_fresh=true` доски с пометкой «новое»; `NotificationAgent` шлёт TG-бот push «J8: на доске вдохновения 15 свежих постов от конкурентов» (`scheduled_at` в `users.timezone`). Endpoint `GET /v1/brands/{id}/inspiration?is_fresh=true`
- Тариф «Network» (multi-brand 3–10 брендов) в биллинге (заготовка, наполнение в Спринте 10)

**Vertical slice:**

> Денис создаёт 5 брендов (5 разных тематик) → у каждого своя Brand Memory → видит на Inspiration Board топ-посты конкурентов → нажимает «Сгенерировать наш вариант» → получает черновик → одобряет в Telegram-боте через inline-кнопки или MiniApp.

### Deliverables Фазы 3

- 3 новых MVP-агента работают: Publisher + Notification + Analyst — итого **8 из 8 MVP-агентов закрыты** (D24 из `03`; Media — пайплайн Content, не считается отдельным)
- Multi-brand UI (D25)
- TG-бот approve через MiniApp
- Календарь публикаций C6 + очередь C6.1 (EPIC-C из `03`)
- **Inspiration Board полный набор L1–L4** (EPIC-L из `03`, MVP Must) — включая еженедельный «свежий drop» от Brand Memory + Notification (TG-бот push J8)
- Internal Cost Observability Dashboard в `/v1/admin/*` (F8) + лимиты тарифа в `/v1/billing/usage` (F8b)
- Audit Log читаемый через UI Explainability (П5)
- Kill-switch + Unleash флаги для всего auto-флоу (П10)

### Критерий завершения

Один пилот ведёт канал ≥ 1 недели **полностью через платформу** (без выхода в TG для редактирования) и подтверждает: «Часов сэкономлено: ≥ 3». **AI Acceptance Rate этой фазы ≥ 65%.**

---

## 8. Фаза 4: Billing + Polish (спринты 10–12, недели 20–24)

**Цель:** Подготовить продукт к биллингу, лимитам и публичному запуску.

### Спринт 10: Billing v0 (недели 20–22)

**Что делаем:**

- **`PaymentProvider`** абстракция (D21 из `03`, `05 §7`) + первый адаптер платёжного провайдера (выбор поставщика — в `07-monetization.md`)
- **3 тарифа** (детали и цены — в `07-monetization.md`, D46): Solo / Pro / Network. Тарифы оперируют **брендами** (D25 из `03`)
- **Multi-currency invoicing** (`04 §9.6`, `04 §18.3`): таблицы заведены в Спринте 1, в Спринте 10 — наполняем `plan_prices` row'ами `(plan, RUB, monthly | annual, effective_from)` и `(plan, BYN, ...)`; на момент charge `invoices.exchange_rate` фиксируется snapshot'ом (избегаем arbitrage), `invoices.reference_amount_usd` денормализуется для internal-reporting. USD / EUR row'ы — post-PMF без миграций
- **Free trial** на старте без карты (с картой — post-MVP, D14 из `03`)
- API + frontend: подписка, отмена, смена тарифа (`/v1/billing/plans`, `/v1/billing/subscription`, `/v1/billing/subscription/cancel`, `/v1/billing/invoices`, `/v1/billing/usage`)
- Webhook handler для платёжных событий с `Idempotency-Key` (П13)
- **Лимиты по тарифу** (бренды, каналы, посты/мес, Media-генерации/мес, LLM-токенов суточный cap) — энфорсятся в `apps/backend/core/quotas.py` middleware. `tenant_limit_overrides` — VIP / promo / pilot переопределения (NULL = use plan default)
- Email-уведомления о биллинге (через NotificationAgent), locale-aware шаблоны

**Vertical slice:**

> Юзер регистрируется → trial → подключает карту через платёжного провайдера → списание прошло → лимиты активированы.

### Спринт 11: Onboarding Polish + UX + Activation + Mobile / PWA + Cmd+K (недели 22–24)

**Что делаем:**

- **Onboarding wizard** (5 экранов): подключение → Brand Memory (через OnboardingAgent) → конкуренты → первый пост (Content → Media → Moderation → Publisher) → trial activated. **Цель — TTFAA < 2 часов** (D15 из `03`)
- **Activation events** в PostHog (signup → channel-connected → brand-memory-done → first-post-generated → first-post-published)
- Empty-states, error-states, loading-states по всему UI (с WS-индикаторами прогресса агентов)
- Базовая документация: FAQ, гайд по подключению, гайд по Brand Memory, гайд по тарифам (отдельная страница в `(public)/docs/`)
- Telegram-канал поддержки (тикеты приходят через `@our_support_bot`)
- Lighthouse audit ≥ 80 по основным страницам
- **Mobile / PWA scope** (S4 из `03`): user-facing страницы (Dashboard / Posts / Editor / Approval / Settings / Brand Memory / Inspiration / Analytics) — full-mobile, **Calendar Month-view — desktop-only** с graceful degradation на mobile (Week-view + warning «Month-режим лучше на десктопе»), `(admin)/*` — desktop-only с заглушкой на mobile. PWA: `manifest.json` + service-worker (`next-pwa` или ручной workbox) для offline-friendly статики и установки на homescreen. Bottom nav на mobile: 4–5 главных разделов (Dashboard / Posts / Calendar / Inspiration / Settings). Breadcrumb-адаптация на узких экранах
- **Cmd+K Global Command Palette**: `cmdk` пакет, индексирует posts (через pg_trgm), channels, brands, settings-страницы, quick-actions («Сгенерировать пост», «План на неделю», «Перейти в Approval mode»). MVP — простой fuzzy-поиск; **semantic search через pgvector** — post-MVP (v1.1)
- **i18n-ready Definition of Done** (`04 §18.4`): pre-commit / CI чек-лист для любых новых фичей — (1) все user-facing строки в `messages/ru.json` (нет хардкода), (2) даты / числа через `Intl.*`, (3) timestamp-поля — `TIMESTAMPTZ` UTC, (4) денежные поля — пары `(amount, currency)` или `reference_amount_usd`, (5) backend errors — с `error_code`, (6) email-templates через `users.locale`. Скрипты `scripts/i18n_audit.ts` + `scripts/check_timestamptz.py` запускаются в CI

### Спринт 12: Settings + Admin Panel + Doc + QA + Final Kill-switches (недели 24–24+1)

**Что делаем:**

- **Settings:**
  - Профиль — селекторы `language` / `timezone` / `preferred_currency` поверх полей из Спринта 1
  - Безопасность — смена пароля + MFA (`pyotp`), MFA обязательна для `admin` / `support`
  - Экспорт данных — `GET /v1/workspaces/{id}/export` (полный архив JSON + media)
  - Удаление аккаунта — `DELETE /v1/users/me` (right to be forgotten)
  - Privacy → opt-in для дообучения собственных моделей (D67 в `04 §18.5`) — по умолчанию `false`, юзер может явно разрешить использовать его агентские логи в анонимизированной форме для улучшения промптов
- Конфиг расписания постов
- Конфиг Brand Memory (просмотр / редактирование Core + Overlay)
- Конфиг конкурентов
- Конфиг режима auto / approve (D5 из `01`) — toggle Full-auto / Human-approves
- **Brand Settings** — селекторы `content_language` / `timezone` бренда. Изменение `content_language` инвалидирует `media:cache:{brand_id}:*`
- **Settings → Brand → Skills tab** (D70 L1, F13 в `03`): список активных skills бренда с источником (`system` / `global` / `custom`), для не-safety skills — toggle Enable / Disable (`brands.disabled_global_skills TEXT[]`), token-budget meter (текущий vs лимит для бренда), preview-визуализатор «активен в N из 12 типичных сценариев». L2 (add custom) и L3 (override) — на post-MVP
- **Internal Admin Panel** (EPIC-M из `03`, `04 §17`) — route-группа `(admin)/` в `apps/web/`, бэкенд-эндпоинты `/v1/admin/*`, доступ только `platform_role IN ('admin','support')` (D16 из `03`, D35 из `04`):
  - **M1**: Global dashboard — total LLM cost, top expensive tenants, NSM, anomaly alerts
  - **M2**: Управление тенантами — pause / suspend / refund / impersonate / reset-rate-limit
  - **M3**: `/v1/admin/llm-calls` — searchable log всех LLM-вызовов, виртуализирован через `@tanstack/react-virtual` (D43 в `05 §6.3.1`)
  - **M4**: `/v1/admin/agent-runs` — chain-of-thought viewer с моделями и retrieved context
  - **M5**: Moderation queue (manual override)
  - **M6**: Plans editor — CRUD тарифов, `plan_prices` per currency, feature flags
  - **M7**: Audit log (sensitive actions)
  - **M8**: Управление ролями `admin` / `support` / `user` (D16 из `03`, `04 §17.2`) + **allow-list точечных действий `support`**:
    - Назначение / снятие ролей (только `admin`), лог изменений в `audit_events` (`severity='critical'`)
    - **Что может `support` — точечные write-операции из allow-list** (полная матрица — `04 §18.2`): pause / unpause бренда, reset password, resend verify, snooze алертов, reset rate-limit, acknowledge алертов, пометить пост на ручной разбор и эскалировать `admin`
    - **Что НЕ может `support`:** impersonate, plans editor, refund, suspend / freeze, изменение `tenant_limit_overrides`, управление ML-opt-in
    - Middleware `require_platform_role('admin' | 'support')` + `enforce_support_allow_list()` для write-операций (любая попытка write вне allow-list → 403 + audit)
  - **M9**: Skills inspector (D68 в `04 §19`): список всех global skills с usage stats (как часто грузятся, средний contribution to tokens, средняя `compile.latency_ms`), version history, кнопка «промоутить custom skill из бренда в global» (post-MVP), bisect-helper для регрессий (SQL по `agent_runs.skills_used` JSONB GIN), статус CI-проверок (validate / dead-skill / token-budget)
- **Безопасность админ-панели** (`04 §17.3`): MFA обязательна, JWT TTL 15 мин, re-auth на destructive actions, impersonate — только `admin`, отдельный аудит impersonate-сессий
- **Финал kill-switches в Unleash** (П10): глобальные `kill_switch_publishing`, `kill_switch_auto_publish`, `kill_switch_media_generation` + admin-UI для оператора
- Полный QA-прогон по всем флоу
- **E2E тесты** на критические сценарии (Playwright):
  - signup → email-verify → connect channel → BM extract → first post (Content → Media → Moderation → Publisher) → publish
  - billing: trial → подключение карты → списание → лимиты
  - admin: `support` read-only access, `admin` impersonate flow, role assignment

### Deliverables Фазы 4

- Биллинг работает (`PaymentProvider` + первый адаптер)
- Лимиты активны (квоты на бренды, каналы, посты, media, токены)
- Полный onboarding flow с TTFAA-замером
- Базовая документация опубликована
- E2E тесты зелёные
- Глобальные kill-switches доступны оператору
- Internal Admin Panel (M1–M9) полностью функциональна
- Платформенные роли `admin` / `support` / `user` (D16 из `03`, D35 из `04`) активны; `support` может работать read-only

### Критерий завершения

Готовы запустить Concierge MVP — 3 пилота начинают платить (или получают бесплатный тестовый месяц для feedback-программы).

---

## 9. Фаза 5: Concierge MVP (недели 24–32, ~2 месяца) — D45

**Цель:** 3 пилота используют продукт ≥ 4 недель, вручную закрываем дыры, измеряем метрики, готовим к публичному запуску.

### 9.1 Что делаем

- **Активная поддержка пилотов** в личном чате / звонках (1 раз в неделю)
- Логируем все pain points и баги
- Каждую неделю — патч-релиз с фиксами
- **Итерируемся над промптами** для AI-агентов (главное — качество контента) — A/B-варианты через Unleash флаги `prompt_variant`
- **A/B тесты моделей по агентам:**
  - Content Agent: Sonnet 4.6 vs Gemini 2.5 Pro (опц. GPT-5-mini)
  - Analyst Agent: Gemini 2.5 Pro vs Sonnet 4.6
  - Onboarding Agent: Haiku 4.5 vs GPT-4o-mini
  - Media: Flux-2 Pro vs Nano Banana 2
- Метрика выбора — AI Acceptance Rate + стоимость
- **Оптимизация LLM-стоимости** (кэширование одинаковых промптов в Redis, батчинг embeddings)
- Сбор отзывов и кейсов для маркетинга (Фаза 6)

### 9.2 Метрики MVP

| Метрика | Цель | Источник |
|---|---|---|
| Активность пилотов | Все 3 публикуют ≥ 3 поста/неделю через платформу | PostHog |
| **NSM:** Autonomous Actions / Active Brand / Week | ≥ 25 (D6 из `01`) | Audit Log + PostHog |
| **Headline KPI:** % Brand Operations Automated | ≥ 70% | Audit Log |
| **AI Acceptance Rate** | ≥ 70% (на старте 50%, доводим до 80%) | `agent_runs.accepted_by_user` |
| **TTFAA** | < 2 часов (D15 из `03`) | PostHog funnel |
| **North Star (customer outcome):** часов сэкономлено | ≥ 3 ч/нед каждым пилотом | Self-report + расчёт |
| Стоимость LLM / пилота / мес | ≤ $5 | Cost Observability |
| Стоимость Media / пилота / мес | ≤ $3 | Cost Observability |
| Activation rate (пилоты как ICP) | ≥ 66% (2 из 3) | PostHog |
| NPS / готовность рекомендовать | ≥ 7 / 10 | Опрос |
| Bug reports (critical) | < 5 за всю фазу | Sentry + ручной лог |

### 9.3 Параллельно (pre-launch marketing)

- Расширяем waitlist (через посты в Telegram, выступления в нишевых сообществах)
- Готовим Demo Day / стрим / запись «как мы делали продукт»
- Подготавливаем Beta-программу (первые 50 юзеров с 50% скидкой на год — D47)

### 9.4 Deliverables Фазы 5

- Все Acceptance Criteria из `03` достигнуты (см. § 9.5)
- 3 кейс-стади (с цифрами) для маркетинга
- Лендинг обновлён реальными результатами
- Готов к публичному анонсу

### 9.5 Acceptance Criteria для «MVP готов» (из `03 §2`)

Все 9 критериев должны быть подтверждены 3 пилотами:

1. Регистрация → подключение канала → **Onboarding Agent** экстрактит Brand Memory из 50 постов ≤ 15 минут (включая cold-start)
2. 5 черновиков постов на неделю через клик «План на неделю» с **MarkdownV2**
3. Каждый черновик имеет AI-сгенерированный визуал (через Media-пайплайн внутри **Content Agent**)
4. **Moderation Agent** проверил контент перед публикацией
5. Опубликовать пост из платформы (approve или auto), координация через **Orchestrator Agent**
6. Уведомления от **Notification Agent** (черновик готов, требуется approve, алерт)
7. Через 7 дней — базовый отчёт от **Analyst Agent**
8. Подтверждено: сэкономлено ≥ 3 часа работы за неделю
9. **TTFAA < 2 часов** (от регистрации до первого autonomous action)

---

## 10. Фаза 6: v1.0 Public Launch (~месяц 7)

**Цель:** Открыть регистрацию для всех желающих в waitlist. Достичь 100 платящих юзеров.

### Что делаем

- **Покупка хостинга / домена / SSL и деплой в продакшен.** До этой фазы разработка — локально (Docker Compose); инфраструктуру покупаем перед публичным запуском
- **Открытие waitlist** — постепенно, по 50 юзеров в неделю
- **Beta-программа** (D47): первые 50 юзеров — 50% скидка на год + доступ в private Telegram-чат
- Должны быть готовы:
  - Multi-brand UI и Network-тариф
  - Базовая документация
  - Mobile / PWA (Спринт 11): Dashboard / Editor / Approval / Settings — full-mobile + установка на homescreen
- **MVP Should (P1) — раскатка в первый месяц после публичного запуска** (`03 §2`):
  - S3 — Опросы и TG-нативные реакции в постах (Publisher + Bot API `sendPoll` / `setMessageReaction`)
  - S4 — Респонсивный мобильный UI / PWA (закрывается в Спринте 11)
  - S5 — Telegram-бот для approve вместо веба (закрыт в Спринте 9)
  - S6 — Ссылки / UTM с автоподстановкой в постах (Content Agent читает `brand.utm_defaults`)
  - S7 — Утренний дайджест по всем брендам (Notification + Analyst, JTBD-11; ежедневный cron 8:00 в `users.timezone`)
  - **S8 — `ResearchAgent` v0** (Gemini 2.5 Pro, P1 из `03`): web-search через провайдер + extract тем из публичных каналов конкурентов (user-bot Pyrogram). Результат — 10–15 trending тем с краткими описаниями. Endpoint `POST /v1/brands/{id}/research/topics`. Интеграция в «План на неделю»: tool `get_weekly_topics()` Content Agent (Спринт 6) расширяется — подбор тем идёт через Research, а не только Brand Memory + история
  - **S9 — `EngagementAgent` v0** (Haiku 4.5, P1 из `03`): автоответы на комменты в привязанном group chat в тоне бренда. Маршрутизация по `confidence`: high → авто-ответ; mid → черновик в TG-бот (J7  «коммент ждёт ответа»); low → эскалация human. Endpoint `POST /v1/comments/{id}/reply-draft`. Kill-switch `kill_switch_engagement_auto` в Unleash. **DM auto-reply** — не в этом релизе (полный Engagement с DM + историей диалога — v1.1 / C2 из `03`)
  - S10 — Еженедельный аналитический дайджест (закрыт в Спринте 8)
  - **Шаблоны постов** (C1 из `03` — рубрики: Пятничный дайджест, Кейс, Мем, сторителлинг, анонс) — Content Agent учитывает формат через новый skill `post-templates-router`
  - **A/B заголовков** — облегчённый прообраз `OptimizerAgent`: для каждого поста генерируется 2–3 варианта первой строки, юзер выбирает вручную; авто-выбор победителя — в v1.2 (полный `OptimizerAgent`)
- **PR-активности:** статья на профильных площадках, выступления на meetup'ах

### Метрики (к месяцу 9)

| Метрика | Цель |
|---|---|
| MAU (monthly active users) | 200+ |
| Платящих юзеров | 100 |
| Churn (месячный) | < 10% |
| Activation rate (от signup до first post) | ≥ 50% |
| LLM-стоимость / юзер | ≤ $3 / мес |
| Media-стоимость / юзер | ≤ $2 / мес |
| Net retention | > 100% (через апгрейды Solo → Pro → Network) |
| **AI Acceptance Rate** (вся платформа) | ≥ 75% |
| **% Brand Operations Automated** | ≥ 80% |

> MRR / выручка / unit-economics в деньгах — в `07-monetization.md`.

---

## 11. Фаза 7: Post-MVP — Сначала углубление TG, потом новые соцсети (месяцы 9–18+) — D49

> Вторую соцсеть подключаем только когда выжали максимум из Telegram. Фокус + глубина сильнее «всего понемногу» на этапе роста второй сотни юзеров.

### v1.1 (месяцы 9–10): Strategist + полный Engagement (DM) + Командная работа + EN-keys baseline

> Engagement v0 и Research v0 уже выпущены в Фазе 6 (месяц 7, P1-пакет). На v1.1 — углубление и новые агенты.

- **`StrategistAgent`** (post-MVP, Gemini 2.5 Pro): глубокий weekly / monthly content plan на основе целей канала, аудитории, трендов. Заменяет batch-режим Content Agent для weekly-plan
- **`EngagementAgent` v1** (C2 в `03`, расширение S9 из Фазы 6, Haiku 4.5): полный режим — комменты + **DM auto-reply** (личные сообщения бота: FAQ + эскалация в human-mode) с глубоким контекстом (история бренда + персональная история диалога с подписчиком)
- **Командная работа:** Editor / Viewer / Agency роли внутри workspace (D16 из `03`)
- **Skill customization L2** (D70, F13 в `03`): Pro+ тариф может добавлять custom brand skills через Settings → Brand → Skills → «+ Add custom skill». Таблица `brand_custom_skills`, валидация manifest через Pydantic, bleach санитизация body, token-budget enforcement, audit_log запись. Имя auto-prefix `brand_{uuid}_<name>`
- **EN-keys baseline** (internal): `messages/en.json` зеркально дополняется ключами из `ru.json` с RU-fallback. Цель — к v1.6 наполнить переводы для лендинга без переписывания компонентов. Включается только при `?locale=en` (внутреннее тестирование), публичный en-route — v1.6
- **Semantic search через pgvector в Cmd+K**: для постов и Inspiration Board — natural-language запрос «найди про X»

### v1.2 (месяцы 10–11): полный Research + Optimizer + Auto-rules NL→DSL + Мониторинг конкурентов

- **`ResearchAgent` v1** (C12 в `03`, расширение S8 из Фазы 6, Gemini 2.5 Pro): полный поиск тем — web-search через провайдер, deep-analysis конкурентного контента, trending topics + выявление angles («чего нет у конкурентов»). Интеграция с `StrategistAgent` для weekly content plan
- **`OptimizerAgent`** (Gemini 2.5 Pro): полный A/B-тест — заголовки / время выхода / форматы с автовыбором победителя (через Unleash как стратегия эксперимента) — S2 / C14 в `03`. Расширяет A/B заголовков из Фазы 6
- **`RuleCompilerAgent`** (D56 + `04 §8.6.2`, post-MVP из `03`): компилятор NL → DSL для auto-rules. Пользователь пишет «авто-одобрять посты длиннее 1000 символов и без эмодзи» → агент компилирует в DSL `(post.length > 1000) AND NOT post.has_emoji` → сохраняется в `auto_rules.compiled_dsl` и исполняется **тем же детерминированным движком**, что и preset-чекбоксы из MVP. Свободный текст «как есть» в проде запрещён (interpretation drift). UI: новое поле «Своё правило естественным языком» в Settings → Publishing → Auto-rules с preview скомпилированного DSL до сохранения. Безопасность: bleach + sanitization NL-ввода через `input_sanitizer` pipeline (D58)
- **Мониторинг конкурентов** (D23 из `03` → Must в v1.2): виджет «что виралило за неделю» + AI-рекомендации
- **Опросы аналитика автоанализ** (TG-poll результатов) — расширение S3 на агентскую логику
- **Reply-chains:** агент предлагает «prolong» хорошо зашедших постов (развитие в комментах)
- **Skill customization L3** (D70, F13 в `03`): Agency tier может override global skills для конкретного бренда (`is_override=true`, `overrides_skill='...'`). UI: «Right-click на skill → Override for this brand» создаёт fork с visual builder для `when_to_use` DSL (D69). Запрещено для `tags: [safety]` / `[system]`. Audit-trail с diff в `audit_log` (M3 admin panel)

### v1.3 (месяцы 11–12): Monitor + Deep Analytics

- **`MonitorAgent`** (GPT-4o-mini): отслеживает упоминания бренда в Telegram, тональность, тренды
- **Глубокая аналитика:** cohort retention по подписчикам, воронки «просмотр → реакция → репост»
- **Тренды в темах:** интеграция с источниками статистики поисковых запросов для подбора релевантных тем
- **Экспорт отчётов:** PDF / Notion / Google Docs — критично для Дениса (отчёт клиенту)

### v1.4 (месяц 13): TG Stories + Premium-фичи + Advanced Media

- **TG Stories в каналах** — планирование, генерация идей, редактор
- **Reactions analytics:** какие emoji-реакции работают лучше в нише
- **TG Boost:** отслеживание boost'ов от подписчиков
- **Custom emoji и stickers** в редакторе
- **Forwards monitoring:** кто репостит ваш контент и сколько это даёт referral-подписчиков
- **Advanced Media** (C16 в `03`): кастомные стили (`brand.visual_style_profile`), карусели (TG album), cover images для статей с длинным текстом, batch-генерация для альбомных постов

### v1.5 (месяцы 13–14): Public API + Webhooks + Marketplace бета + BYOK

- **Public API:** юзеры могут вызывать наши агенты из n8n / Make / своих систем (rate-limited per tariff) — C10 в `03`
- **Webhooks:** публикации / отчёты / биллинг (наружу) — C10 в `03`
- **Marketplace бета:** юзеры делятся Brand Memory-пресетами и шаблонами (free + paid)
- **Bring Your Own LLM Key (BYOK)** (C17 в `03`): Pro+ юзеры могут подключить свой OpenAI / Anthropic ключ через Settings → Integrations (зашифрованное хранение + envelope encryption, ротация). `LLMProvider` (D22) выбирает между общим шлюзом и user-key по `brand.llm_routing_preference`. У юзера на BYOK у нас не списывается LLM-стоимость, но и Cost Guardian — на его стороне (у нас только rate-limit)

### v1.6 (месяцы 14–15): Repurpose + Agency Mode v0 + Public EN landing

- **`RepurposeAgent`** (post-MVP, Haiku 4.5): адаптация контента между соцсетями (TG → IG Story preview, TG → YT Short description) — пока в read-only режиме до запуска YT — C15 в `03`
- **Parent / child workspaces** включаем в UI (в схеме БД заложены сразу — D16 из `03`)
- **`Membership.role`** расширяем (Owner / Admin / Editor / Viewer / Agency)
- **Изоляция данных** между клиентами внутри агентства
- **Agency-тариф** в биллинге (детали в `07-monetization.md`)
- **Handoff workspace** клиенту при уходе на самостоятельную подписку
- **Public EN landing**: `/en/*` route для маркетинговых страниц (`/`, `/pricing`, `/features`, `/about`) + `<link rel="alternate" hreflang="en">` для SEO. App остаётся RU-only до v2.0

### v2.0 (месяцы 16–18): YouTube — вторая соцсеть + Public EN full app

> Вторую соцсеть подключаем здесь — когда либо явно просят, либо видно плато роста на TG (~ 300–500 платящих юзеров).

- `YouTubeChannel` адаптер (`apps/backend/adapters/social/youtube.py`)
- Импорт истории + метрики (views, watch time, retention, subscribers)
- Content Agent учится формату: заголовки под SEO + CTR, описания с тайм-кодами, теги
- Кросспостинг TG → YouTube Shorts (автоописания) — активация RepurposeAgent
- Brand Memory расширяется YT-Overlay отдельно от TG-Overlay (D33 архитектура уже поддерживает)
- **Public EN full app**: переключатель локали в UI + `/en/*` routes для авторизованной зоны, дополняем `messages/en.json` переводами для `(app)/*`, валидация форм / error toasts / email-templates локализованы. USD / EUR row'ы в `plan_prices` (post-PMF включение)

### v2.1+ (месяцы 18+): Multi-network и финал

- Instagram-адаптер (при реальном спросе от юзеров — D4 из `01`)
- TikTok-адаптер
- Marketing-рассылки (расширение email-каналов сверх системных писем)

### Метрики к концу v2.0 (месяц 18)

| Метрика | Цель |
|---|---|
| Платящих юзеров | 500–1000 (преимущественно TG-юзеры) |
| Доступные соцсети | 2 (TG «всё-включено» + YouTube базовый) |
| Команда | 2–3 человека (подключаем инженера + маркетолога по мере роста) |

> Финансовая динамика (MRR, выручка) — в `07-monetization.md`.

---

## 12. Год 2+: Operating System for SMM

### v3.0 (год 2): Marketplace + Platform

- VK-адаптер (по спросу, D4 из `01`)
- Marketplace кастомных агентов (юзеры публикуют свои промпт-шаблоны / агенты) — composable principle (V4 из `01`)
- Public API для разработчиков
- White-label для агентств
- Enterprise tier (SOC 2, dedicated instance, кастомные SLA)

### v4.0+ (год 3): International

- Запуск в дополнительных гео — каждый новый регион добавляет: локализацию (`messages/<locale>.json`), валютные row'ы в `plan_prices`, локального платёжного провайдера через адаптер
- Расширение в Латам / SEA — `es-ES` / `pt-BR` локали зеркально

---

## 13. Критические риски и план Б

| Риск | Mitigation | План Б |
|---|---|---|
| **Качество AI-постов недостаточно** (AI Acceptance Rate < 60%) | Итерации над промптами (через Unleash A/B), A/B-тесты моделей (Sonnet 4.6 / Gemini 2.5 Pro), fine-tuning Brand Memory | Pivot к «AI-помощник для редактирования», а не «AI-автор» |
| **Telegram ограничивает Bot API** для нашего use case | Тщательный rate-limit, Global Channel Registry как буфер; user-bot Pyrogram — только для чтения публичных каналов конкурентов (не для приватных) | Перевести часть фич в «approve-only» режим, увеличить долю пользовательских действий |
| **LLM-шлюз становится дороже / закрывается** | `LLMProvider` абстракция (D22) → switch на OpenAI / Anthropic напрямую. То же для `ImageProvider` (D40) | На MVP бюджет позволяет переключиться без боли |
| **Пилоты не подтверждают North Star (≥ 3 ч/нед)** | Глубокая итерация, customer development, расширение пилотной базы до 5 | Pivot к более узкому use case (только «генерация постов» как утилита) |
| **Moderation Agent ошибочно блокирует валидный контент** | Двухуровневая логика: rule-based + LLM-judge (П10); UI «оспорить блок»; `flag_for_review` для mid-confidence | Временный bypass-флаг для бренда после ручной проверки |
| **Стоимость Media-генерации выходит из unit-economics** | Routing Flux-2 Pro vs Nano Banana по тарифу (Solo → Nano, Pro+ → Flux), throttle (П12), кэш «похожих» промптов | Отключить Media-пайплайн на Solo, оставить только для Pro / Network |
| **Конкуренты выходят с похожим продуктом** | Speed-to-PMF, бренд, кейсы; концентрация на Brand Memory как USP | Увеличить инвестиции в маркетинг / PR |
| **Недостаточный рост waitlist** | PR, контент-маркетинг, гостевые посты | Прямые продажи в нишевых сообществах |
| **Не успеваем к срокам** | Жёстко режем Should-фичи в Could; D49 защищает от YouTube preempt | Выпускаем «Concierge-only beta» |

---

## 14. Что мы НЕ делаем на этом roadmap

- Не выпускаем YouTube раньше месяца 16 (D49 — фокус на Telegram)
- Не выпускаем Instagram / TikTok до месяца 18+
- Не строим native-mobile до 1000 платящих юзеров (PWA достаточно — закрывается в Спринте 11, S4 в `03`)
- Не делаем видеогенерацию вообще (out-of-scope из `03`)
- Не выходим на англоязычный рынок до года 2 (D4 из `01`) — но архитектурно i18n-ready с первого спринта
- Не делаем pay-per-use UI / расходомер (`03` Won't, D37 в `04`) — только подписочная модель
- Не разрешаем свободный текст auto-rules в MVP (D56 — только preset чекбоксы)
- Не даём early-adopter скидок до публичного запуска (D47)
- Не переключаемся на Kubernetes до post-MVP (`05` anti-stack)
- Не используем `dict[str, Any]` в межагентных сообщениях (`04 D34`, П6)
- Не используем сторонние auth-библиотеки и hosted-auth (`04 D28`, `05 §4`) — авторизация своя
- Не используем HTML-парсер `t.me/s/...` для чтения каналов — публичные каналы читаются user-bot'ом на Pyrogram (`05 §5.2`)
- Не используем TDLib

---

## 15. Связанные документы

- `01-product-vision.md` — видение, NSM (Autonomous Actions / Active Brand / Week), инварианты I1–I17
- `02-target-audience.md` — ICP, персоны Анна / Денис / Мария, JTBD, UC-1 .. UC-12
- `03-feature-scope.md` — MoSCoW MVP, 8 MVP-агентов (D24), EPIC-A..M, F8 / F8b cost-логика, F9–F13
- `04-architecture.md` — архитектура, принципы П1–П13, Brand Memory двухслойная (D33), platform roles (D35), D56–D70, §18 Internationalization & Multi-currency, §19 Skill-based agent architecture
- `05-tech-stack.md` — самописная авторизация (D36), Celery, ImageProvider, Event Bus stack, `@tanstack/react-virtual`, pg_partman + pg_cron + PgBouncer, `next-intl`, монорепо `apps/backend/` + `apps/web/`, REST `/v1/` + kebab-case
- `07-monetization.md` — тарифы (Solo / Pro / Network), unit-economics, multi-currency invoicing
- `11-information-architecture.md` — sitemap, навигация, URL contract (D98), Cmd+K (D99), Mobile vs Desktop scope (D100), i18n stages
- `12-key-screens-and-patterns.md` — wireframe-спецификации экранов, component catalogue, tokens

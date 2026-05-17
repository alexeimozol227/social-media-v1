# План: Фаза 1 Спринт 3 — первый PR (LLMProvider + Audit Log foundation)

## Контекст

`06-roadmap.md §5 Фаза 1 Спринт 3` (недели 6–8) — это связка из крупных кусков, на которых дальше строится вся работа AI-агентов: `LLMProvider` абстракция (D22 в `03`, `05 §3.3`) с первой реализацией через LLM-шлюз (D38 в `05`), двухслойная **Brand Memory** (D33 в `04 §8` — `brand_memory_core` JSONB + `brand_memory_overlays` JSONB + `brand_memory_examples` с pgvector), **`OnboardingAgent` v0** (D24 в `03`, EPIC-K в `03`) с авто-экстракцией BM из последних 50 постов канала (цель — TTFAA-замер ≤ 15 минут, D15 в `03`) и cold-start wizard'ом (5–7 вопросов для каналов < 10 постов, D18 в `03`), указание конкурентов-референсов (3–5 каналов, D23 в `03`) через user-bot на Pyrogram (заделка которого приехала в PR #18 Спринта 2), семантический индекс истории канала (pgvector embeddings через `text-embedding-3-small`) поверх партиционированных таблиц из PR #17 Спринта 2, `BrandMemoryService` (single source of truth по П11 в `04`) как единственный интерфейс агентов к BM, и **Audit Log** — таблицы `agent_runs` + `llm_calls` с базовым писателем (П5 «AI Explainability», П12 «Cost telemetry» в `04`).

Фундамент Фазы 0 + Спринта 1 (см. `reports/phase0-phase1-sprint1-report.md`) и Спринта 2 уже на месте: монорепо `apps/backend/` + `apps/web/`, FastAPI + SQLAlchemy 2.0 async + Alembic, самописная авторизация (JWT 15 мин + refresh-семьи + email verification + password reset + MFA TOTP), RLS-контекст и RLS-policies на tenant-таблицах, PgBouncer transaction pooling, `audit_events` + pg_partman + retention pg_cron skeleton (`active=false`), Skill-инфраструктура (`SkillManifest` / `SkillRegistry` / `SkillCompiler` + CI-проверки `validate_skills` / `skill-token-budget` / `check_system_prompt_lang`), Event Bus (Redis Pub/Sub + Pydantic discriminated unions), WebSocket + `useRealtime` хук, Unleash client + флаг `enable_auto_publish`, Idempotency middleware + таблица `idempotency_keys`, OpenTelemetry для FastAPI / SQLAlchemy / Celery, billing skeleton (`plans` / `plan_prices` / `invoices` / `tenant_limit_overrides`), мультивалютная денормализация (`agent_runs.cost_rub`, `llm_calls.input_cost_usd` / `output_cost_usd` / `cost_rub`, заложенная в Спринте 1). Из Спринта 2 — `channels` + `workspace_channels` + `channel_posts` (партиционированные помесячно через `pg_partman`), `aiogram 3.x` Bot API клиент с верификацией прав, history-backfill (PR #15), webhook + dispatcher с публикацией `channel.post_received` в event bus (PR #16), `channel_post_embeddings_template` + HNSW индексы + `pg_cron` safety-check (PR #17), `pyrogram_sessions` + пул user-bot аккаунтов с ротацией и healthcheck (PR #18), `workspace_channels.role='competitor'` + API `POST /v1/brands/{id}/competitors` (PR #18), workspace settings UI (PR #19).

Я предлагаю разбить Спринт 3 на несколько PR — первый PR (этот) будет «**LLMProvider + Audit Log foundation**»: абстракция `LLMProvider` с первой реализацией через LLM-шлюз, mock-провайдер для тестов и dev-окружения, таблицы `agent_runs` + `llm_calls` с базовым писателем, retention pg_cron jobs со статусом `active=false`, базовая `BaseAgent` обвязка с инъекцией `LLMProvider`, и Pydantic-схемы для будущих агентских сообщений в event bus. Всё остальное (`BrandMemoryService` + миграции для двухслойной BM, `OnboardingAgent` с авто-экстракцией, cold-start wizard, embedding pipeline на проде, добавление конкурентов в onboarding) — следующими PR этого же Спринта 3.

## Что в PR #20 (этот PR — «llm-provider-and-audit-log»)

### Бэкенд — модели и миграции

- **`agent_runs`** (П5 «AI Explainability», П12 «Cost telemetry», `04 §10.4` / `05 §10.3`):
  - `id` UUID PK
  - `workspace_id` FK → `workspaces.id` (RLS-scope)
  - `brand_id` FK → `brands.id` NULL (некоторые runs не привязаны к бренду — например, healthcheck)
  - `channel_id` FK → `channels.id` NULL
  - `agent` VARCHAR(32) — `content` / `publisher` / `analyst` / `orchestrator` / `brand_memory` / `onboarding` / `moderation` / `notification` (enum валидируется в Pydantic, в БД — `CHECK` constraint)
  - `agent_version` VARCHAR(16) NULL — semver навыкового пакета (для bisect-helper из M9 в `04 §17`)
  - `trigger_source` VARCHAR(32) — `api` / `celery_beat` / `webhook` / `event_bus` / `manual_admin`
  - `correlation_id` UUID NULL — связь между runs в одной цепочке (Orchestrator → Content → Moderation → Publisher)
  - `parent_run_id` UUID NULL → `agent_runs.id` — для tree-view цепочки в Explainability UI
  - `status` VARCHAR(16) — `pending` / `running` / `succeeded` / `failed` / `cancelled`
  - `error_code` VARCHAR(64) NULL (`MODEL_TIMEOUT`, `LLM_BUDGET_EXCEEDED`, `CIRCUIT_BREAKER_OPEN`, `SKILL_NOT_FOUND`, … — справочник из `04 §15.2`)
  - `error_message` TEXT NULL
  - `chain_of_thought` TEXT NULL — если модель вернула reasoning (anth `extended_thinking`, gemini `thoughtSummary`); зануляется retention job на 31-й день
  - `retrieved_context` JSONB NULL — что подгрузили в prompt (BM Core hash, BM Overlay hash, последние N постов канала, конкуренты, тренды); зануляется retention job
  - `raw_output` JSONB NULL — финальный ответ модели после tool-calling loop
  - `skills_used` JSONB NULL — массив `{name, source: 'system'|'global'|'custom', tokens}` (D68 в `04 §19`)
  - `prompt_tokens` INT NULL, `completion_tokens` INT NULL, `total_tokens` INT NULL
  - `cost_usd` NUMERIC(12, 6) NULL — atomic source-of-truth (`04 §18.3`)
  - `cost_rub` NUMERIC(12, 4) NULL — денормализация через `invoices.exchange_rate` snapshot (`04 §18.3`)
  - `latency_ms` INT NULL
  - `accepted_by_user` BOOLEAN NULL — заполняется позже (когда user примет / отклонит черновик)
  - `opt_in_training` BOOLEAN — снимок `users.opt_in_training` на момент run'а (D67 в `04 §18.5`); влияет на retention `chain_of_thought` (если `true` — не зануляем)
  - `started_at` TIMESTAMPTZ, `finished_at` TIMESTAMPTZ NULL
  - `created_at` TIMESTAMPTZ
  - Индексы: `(workspace_id, brand_id, started_at DESC)`, `(agent, started_at DESC)`, `(correlation_id)`, GIN на `skills_used`
- **`llm_calls`** (`04 §10.4` / `05 §10.3`):
  - `id` UUID PK
  - `agent_run_id` FK → `agent_runs.id` (один run = N llm-calls в случае tool-calling loop)
  - `workspace_id` / `brand_id` — денормализация (для retention queries и admin-фильтров без JOIN'а на `agent_runs`)
  - `provider` VARCHAR(32) — `polza` / `openai_direct` / `anthropic_direct` / `mock` / `byok_user_<workspace_id>` (BYOK — post-MVP, столбец заводим сразу)
  - `model` VARCHAR(64) — `claude-sonnet-4.6` / `claude-haiku-4.5` / `gpt-4o-mini` / `gemini-2.5-pro` / `text-embedding-3-small` / …
  - `call_type` VARCHAR(16) — `completion` / `chat` / `embedding` / `tool_call`
  - `prompt_hash` CHAR(64) — SHA-256 финального промпта после `SkillCompiler.compile()` (для дедупликации в админке + future prompt-cache hit-rate)
  - `prompt_full` TEXT NULL — храним только если `opt_in_training=true`; иначе `NULL` сразу
  - `tools_called` JSONB NULL — `[{name, input, output_summary}]`
  - `raw_output` JSONB NULL
  - `input_cost_usd` / `output_cost_usd` / `cost_usd` NUMERIC(12, 6) NULL
  - `cost_rub` NUMERIC(12, 4) NULL
  - `prompt_tokens` / `completion_tokens` INT NULL
  - `latency_ms` INT NULL
  - `circuit_breaker_state` VARCHAR(16) NULL — `closed` / `half_open` / `open` на момент вызова (для отчётов в admin-панели)
  - `retries` SMALLINT — счётчик retry внутри одного call (для tenacity-decorator из `04 §6.1.3`)
  - `created_at` TIMESTAMPTZ
  - Индексы: `(workspace_id, brand_id, created_at DESC)`, `(model, created_at DESC)`, `(prompt_hash)`, `(provider, model)`
- **pg_cron retention jobs `retention_chain_of_thought` (daily 03:00 UTC) и `retention_llm_calls_aggregate` (daily 03:30 UTC)** заводятся **в той же миграции, что и `agent_runs` / `llm_calls`** со статусом `active=false` (`SELECT cron.alter_job(jobid, active := false)`). Тела jobs реализованы (psql-функции `retention_chain_of_thought_run()` и `retention_llm_calls_aggregate_run()`), но не запускаются — активация всей пятёрки retention jobs единой миграцией в Спринте 8 (D57 в `04 §18.5`). Smoke-тест в pytest вручную дёргает функции на seed-данных и проверяет, что `chain_of_thought IS NULL` для записей старше 30 дней при `opt_in_training=false`, и что raw-`llm_calls` агрегируются в `llm_calls_daily` для записей старше 90 дней
- **`llm_calls_daily`** (агрегатная таблица, целевая для `retention_llm_calls_aggregate`):
  - PK `(date, workspace_id, brand_id, provider, model)`
  - `total_calls` / `total_prompt_tokens` / `total_completion_tokens` BIGINT
  - `total_cost_usd` / `total_cost_rub` NUMERIC(14, 4)
  - `p50_latency_ms` / `p95_latency_ms` / `p99_latency_ms` INT
  - `error_count` / `circuit_open_count` INT
  - Партиционируется помесячно через `pg_partman` (как `channel_posts` из PR #17 Спринта 2) — в админ-панели M3 фильтр обычно идёт за последний месяц или квартал
- **Миграция:** одна Alembic-миграция `0020_audit_log_agent_runs_llm_calls.py` со всеми тремя таблицами + индексы + RLS-policy `workspace_isolation` на `agent_runs` / `llm_calls` / `llm_calls_daily` (`workspace_id = app.current_tenant_id`, для роли `support` — read-only через WHERE-bypass из `04 §17.2`) + два пустых `cron.schedule(...)` со статусом `active=false`

### Бэкенд — `LLMProvider` абстракция

- **`apps/backend/adapters/llm/base.py`** — абстракция (`04 §6.1` / `05 §3.3`):
  - `class LLMProvider(ABC)` с методами:
    - `async def chat(self, messages: list[ChatMessage], model: str, *, tools: list[ToolSpec] | None = None, response_format: type[BaseModel] | None = None, max_tokens: int | None = None, temperature: float | None = None, idempotency_key: str | None = None) -> ChatResponse`
    - `async def embed(self, texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]]`
    - `async def health_check(self) -> ProviderHealth` — для circuit breaker + Sentry alert
  - Типы: `ChatMessage`, `ToolSpec`, `ChatResponse`, `Usage`, `ProviderHealth` — все Pydantic-модели; `dict[str, Any]` запрещён (П6, D34 в `04`)
  - Универсальные ошибки: `LLMRateLimitError`, `LLMTimeoutError`, `LLMBudgetExceededError`, `LLMProviderUnavailableError`, `LLMContextLengthError`, `LLMContentFilterBlockedError` — мапятся в наши `AppError` с `error_code` (D62 в `04 §15.2`)
- **`apps/backend/adapters/llm/polza.py`** — первая реализация (D38 в `05 §3.3`):
  - `class PolzaProvider(LLMProvider)` — OpenAI-совместимый клиент через `httpx.AsyncClient` (без официальных SDK Anthropic / OpenAI / Google, чтобы не плодить зависимости и не упираться в их rate-limits)
  - Маршрутизация модели → внутренний gateway-endpoint (`claude-sonnet-4.6` → `/v1/chat/completions` с `model="anthropic/claude-sonnet-4.6"`; `gemini-2.5-pro` → то же endpoint с `model="google/gemini-2.5-pro"`; `text-embedding-3-small` → `/v1/embeddings`)
  - Pricing-таблица в `apps/backend/adapters/llm/pricing.py` — статически зашитые цены `(provider, model) → (input_cost_per_1k_tokens, output_cost_per_1k_tokens, embedding_cost_per_1k_tokens)` (источник — публичный прайс LLM-шлюза, ручное обновление 1 раз в квартал); `cost_usd` считается на нашей стороне после ответа (для аудита, даже если gateway пришлёт свой `usage.cost`)
  - **Retry / circuit breaker** (`04 §6.1.3`):
    - `tenacity` retry: `retry_if_exception_type(LLMRateLimitError | LLMTimeoutError | LLMProviderUnavailableError)`, `stop_after_attempt(3)`, exponential backoff `wait_random_exponential(min=1, max=10)`, `before_sleep` → structlog warning
    - `pybreaker` circuit breaker per `(provider, model)`: `fail_max=5`, `reset_timeout=60s`; состояние сохраняется в Redis (`circuit:llm:{provider}:{model}` → `closed|half_open|open`) — чтобы шарилось между worker процессами; экспозиция в `llm_calls.circuit_breaker_state`
  - **Idempotency** (П13 в `04`): `idempotency_key` пробрасывается в HTTP header `Idempotency-Key` к gateway'у; внутренне cache `(prompt_hash, model)` → `response` в Redis с TTL 24ч (опционально включается через флаг `LLM_PROMPT_CACHE_ENABLED` в Unleash — на Спринте 3 включён в dev/test, выключен в prod)
  - **Streaming НЕ поддерживается на MVP** — `chat()` возвращает финальный ответ целиком. SSE-streaming в UI («слово за словом») будет в post-MVP v1.1 вместе с editor-improvements
  - `apps/backend/.env.example` дополняется: `LLM_GATEWAY_BASE_URL`, `LLM_GATEWAY_API_KEY`, `LLM_PROMPT_CACHE_TTL_SECONDS=86400`
- **`apps/backend/adapters/llm/mock.py`** — `MockLLMProvider` для unit / integration тестов и для `make dev` без реального ключа:
  - Читает фикстуры из `tests/fixtures/llm_responses/{agent}/{scenario}.json` — каждый файл содержит `{prompt_regex, response, usage, latency_ms}`
  - Если ни одна фикстура не подошла — возвращает детерминированный заглушечный ответ + warning в Sentry (`mock_llm_fallback_used`)
  - В CI это эквивалентно `respx` для остальных HTTP-моков (`05 §12.1`)
- **`apps/backend/adapters/llm/factory.py`** — выбор провайдера:
  - В Спринте 3 — только `polza` / `mock`, выбор через `pydantic-settings` (`LLM_PROVIDER=polza|mock`)
  - Будущие провайдеры (`openai_direct`, `anthropic_direct`, `byok_user_<workspace_id>` для C17 / BYOK в v1.5) добавляются без изменения вызывающего кода

### Бэкенд — Audit Log writer + `BaseAgent` skeleton

- **`apps/backend/services/audit/agent_run_writer.py`** (П5, П12):
  - `class AgentRunWriter` с методами:
    - `async def start_run(self, *, workspace_id, brand_id, channel_id, agent, trigger_source, correlation_id, parent_run_id) -> AgentRun` — INSERT `status='running'`
    - `async def record_llm_call(self, agent_run_id, *, provider, model, call_type, prompt_hash, prompt_full, tools_called, raw_output, usage, latency_ms, circuit_breaker_state, retries) -> LLMCall` — INSERT в `llm_calls`, инкремент денормализованных `agent_runs.prompt_tokens / completion_tokens / cost_usd / cost_rub`
    - `async def attach_skills(self, agent_run_id, skills_used: list[SkillUsage]) -> None`
    - `async def finish_run(self, agent_run_id, *, status, error_code=None, error_message=None, raw_output=None, chain_of_thought=None, retrieved_context=None, accepted_by_user=None) -> AgentRun` — UPDATE `finished_at`, `latency_ms`
  - **Cost-конверсия USD → RUB**: берём snapshot курса из последнего `invoices.exchange_rate` для workspace'а (если в workspace ещё не было invoice — статический fallback `USD_TO_RUB_FALLBACK=92.0` из настроек, обновляется вручную раз в квартал)
  - **`opt_in_training` snapshot**: читаем `users.opt_in_training` владельца workspace'а **в момент `start_run()`** и сохраняем в `agent_runs.opt_in_training`; даже если юзер потом выключит opt-in, retention job уже не зачистит chain-of-thought этого run'а (по дизайну — мы согласовали использование на момент создания)
- **`apps/backend/agents/base.py`** — общий контракт для всех 8 MVP-агентов (готовим заранее, чтобы Спринт 4 / Content Agent уже наследовался):
  - `class BaseAgent(ABC)` с инъецированными зависимостями: `llm_provider: LLMProvider`, `skill_compiler: SkillCompiler`, `audit_writer: AgentRunWriter`, `event_bus: EventBus`
  - `async def run(self, context: AgentContext) -> AgentResult` — оборачивает: `start_run()` → `skill_compiler.compile()` → `llm_provider.chat()` (или `embed()`) → `attach_skills()` + `record_llm_call()` → `finish_run()`; ошибки маппятся в `AppError` + `finish_run(status='failed', error_code=…)`
  - `agent_name: ClassVar[str]` — обязательное поле; используется в `agent_runs.agent`
- **`HealthCheckAgent`** — единственный «настоящий» агент в этом PR, минимальный (наследник `BaseAgent`):
  - `agent_name = "healthcheck"`
  - Метод `run(context)` дёргает `llm_provider.chat()` с фикстурным prompt'ом «Reply with the word OK» (max_tokens=10) — проверяет, что вся цепочка `BaseAgent` + `LLMProvider` + `AgentRunWriter` собирается end-to-end
  - Скрипт `apps/backend/scripts/run_healthcheck_agent.py` — для smoke-теста локально и в CI

### Бэкенд — Pydantic-схемы для будущих агентских событий event bus

- **`apps/backend/events/schemas.py`** дополняется (D34 в `04`, П6 — Pydantic discriminated unions, никаких `dict[str, Any]`):
  - `AgentRunStartedEvent` — `{event_id, event_type='agent.run.started', workspace_id, brand_id, channel_id, agent_run_id, agent, trigger_source, correlation_id, parent_run_id, timestamp}`
  - `AgentRunFinishedEvent` — `{… agent_run_id, status, error_code, latency_ms, cost_usd, cost_rub, timestamp}`
  - `LLMCallFailedEvent` — `{… llm_call_id, provider, model, error_code, retries, circuit_breaker_state, timestamp}` — для будущего CostGuardian (Спринт 8) и для admin-алертов
  - `CircuitBreakerOpenedEvent` — `{… provider, model, opened_at, timestamp}` — для admin-алерта в TG-чат команды (admin panel M1)
- Publish этих событий — на текущем PR без подписчиков; обработчики появятся в Спринте 8 (CostGuardian) и в админ-панели Спринта 12 (M1 dashboard)

### Бэкенд — API эндпоинты (минимум)

Все эндпоинты — с префиксом `/v1/`, kebab-case, plural nouns (`05 §3.1`); доступ — только `platform_role IN ('admin','support')` для всех `/v1/admin/*` (`04 §17.2`, D35 в `04`):

- `GET /v1/admin/agent-runs` — list с пагинацией (`limit` / `offset` / `cursor`) и фильтрами `workspace_id` / `brand_id` / `agent` / `status` / `correlation_id` / `started_after` / `started_before`; ответ только тех run'ов, к которым у роли есть доступ:
  - `admin` — все run'ы всех workspace'ов
  - `support` — read-only, видит все run'ы (для расследований), но НЕ видит `chain_of_thought` / `retrieved_context` / `prompt_full` (PII-leak risk)
  - `user` — 403 на `/v1/admin/*`; для пользовательского UI Explainability будет отдельный endpoint `/v1/agent-runs/{id}/explainability` в следующих PR (см. ниже)
- `GET /v1/admin/agent-runs/{id}` — детальный view с `chain_of_thought` / `retrieved_context` / linked `llm_calls` (только `admin`)
- `GET /v1/admin/llm-calls` — list с пагинацией + фильтры `model` / `provider` / `prompt_hash` / `created_after` / `created_before` (только `admin`)
- `POST /v1/admin/healthcheck/llm` — синхронный `HealthCheckAgent.run()`, возвращает `{provider, model, latency_ms, cost_usd, status}` — для админа, чтобы вручную проверить, что LLM-gateway отвечает. Доступ — только `admin`

> **Пользовательский Explainability UI** (П5 — модал «как агент это сделал» из `04 §17.4`) появляется только в Спринте 12 (вместе с админ-панелью M1–M9). В Спринте 3 — только admin-only эндпоинты, потому что у пользователя ещё нет ни одного агентского run'а (Content / Publisher / Analyst — Спринты 4, 7, 8).

### Фронтенд — минимум для тестов

В этом PR пользовательского UI **не добавляем** — Спринт 3 пользователь увидит только в следующих PR (когда appear cold-start wizard и Brand Memory editor). Здесь только:

- **`/admin/llm-healthcheck`** — внутренняя страница для founder'а / support'а с кнопкой «Прогнать healthcheck»:
  - Доступ — `platform_role IN ('admin','support')` (server-side check на API + redirect на `/login` для остальных, как `(admin)/*` route group из `04 §17.3`)
  - Показывает результат последнего healthcheck: provider, model, latency_ms, cost_usd, статус (OK / FAIL с error_code)
  - Никакой полировки — таблица + кнопка
- **`/admin/agent-runs`** v0 — простой список последних 50 agent runs (server-side rendered, без виртуализации; виртуализация через `@tanstack/react-virtual` — в Спринте 12 для M3 / M4)
- Все user-facing страницы — без изменений (`/`, `/login`, `/register`, `/dashboard`, `/dashboard/channels` из Спринта 2)

### i18n / event bus

- Все новые admin-страницы — через `useTranslations()` (`04 §18.1`); ключи добавляются в `apps/web/messages/ru.json` + зеркальные пустые ключи в `en.json` (CI `i18n_audit.ts` ловит расхождения)
- Backend ошибки `LLMBudgetExceededError` / `LLMRateLimitError` / `LLMTimeoutError` / `LLMProviderUnavailableError` / `CircuitBreakerOpenError` маппятся в типизированные `AppError` с `error_code` (D62) → фронт через `useApiError` хук показывает toast с `suggested_action` (например, «Попробуйте через минуту» для `LLMRateLimitError`)
- **Новые события event-bus** (`apps/backend/events/schemas.py`):
  - `agent.run.started` / `agent.run.finished` — publish из `AgentRunWriter` (для будущей подписки Cost Guardian + WS-уведомления юзеру «черновик готов» в Спринте 4)
  - `llm.call.failed` / `circuit.breaker.opened` — publish из `PolzaProvider`
  - В PR #20 без подписчиков, только publish + smoke-тест в pytest, что Pydantic-схема валидируется

### Тесты

- **Backend** (pytest + pytest-asyncio + httpx + respx + fakeredis):
  - `test_llm_provider_polza.py` — `respx` мокает gateway endpoint; проверяем: успешный chat, успешный embed, retry при 429 (rate-limit), circuit-breaker открывается на 5-й 5xx-ошибке и закрывается через 60с (`freezegun` для контроля времени), idempotency-key пробрасывается, cost рассчитывается по pricing-таблице
  - `test_llm_provider_mock.py` — `MockLLMProvider` подбирает фикстуру по regex; неподходящий промпт → fallback с warning
  - `test_agent_run_writer.py` — `start_run` → `record_llm_call` (x2) → `attach_skills` → `finish_run`; денормализованные суммы `prompt_tokens` / `cost_usd` корректные; `opt_in_training=true` сохраняется в run даже если юзер потом отключает opt-in; RLS — юзер другого workspace'а не видит agent_runs первого
  - `test_base_agent.py` — `HealthCheckAgent.run()` end-to-end на `MockLLMProvider`; ошибка маппится в `AppError` + `agent_runs.status='failed'`; событие `agent.run.finished` публикуется в event bus
  - `test_retention_jobs_smoke.py` — `psql` вызовы функций `retention_chain_of_thought_run()` и `retention_llm_calls_aggregate_run()` на seed-данных; `chain_of_thought IS NULL` для записей старше 30 дней при `opt_in_training=false`; raw-`llm_calls` агрегируются в `llm_calls_daily` для записей старше 90 дней; **сами cron-jobs остаются `active=false`** (проверяем в `pg_cron.job` view)
  - `test_admin_agent_runs_endpoint.py` — `admin` видит `chain_of_thought`; `support` НЕ видит (поле зануляется в response через Pydantic-сериализатор `exclude` + middleware-флаг); `user` → 403
  - `test_circuit_breaker_redis.py` — состояние шарится между двумя инстансами `PolzaProvider` через Redis (fakeredis)
- **Frontend** (Vitest + Testing Library):
  - `admin-llm-healthcheck.test.tsx` — кнопка дёргает endpoint, успех показывает зелёный badge, ошибка — красный + `suggested_action`
  - `admin-agent-runs.test.tsx` — таблица рендерится с моками, кликнуть row → детальная страница

### CI

- Те же чеки, что в Спринтах 1 / 2: `lint` (ruff + ruff-format + biome) + `typecheck` (mypy strict + tsc) + `test` (pytest + Vitest) + `tools/lint_set_local.py` + `scripts/check_timestamptz.py` + `scripts/i18n_audit.ts` + `scripts/check_system_prompt_lang.py` + `validate_skills` + `skill-token-budget` + `openapi-diff` + `pydantic-to-zod` (синхронизация схем backend ↔ frontend, в т.ч. для `AgentRunDetail`, `LLMCallDetail`)
- Новые env-переменные документируются в `apps/backend/.env.example`: `LLM_GATEWAY_BASE_URL`, `LLM_GATEWAY_API_KEY`, `LLM_PROVIDER=polza|mock`, `LLM_PROMPT_CACHE_TTL_SECONDS=86400`, `USD_TO_RUB_FALLBACK=92.0`

### Документация

- `apps/backend/README.md` — раздел «LLM Provider»: как переключать `polza` ↔ `mock`, где лежат фикстуры (`tests/fixtures/llm_responses/`), как добавить новую модель в pricing-таблицу (1 файл, manual quarterly update)
- `apps/backend/README.md` — раздел «Audit Log»: схема `agent_runs` / `llm_calls`, как читать через admin endpoint, как тестируется retention smoke
- `apps/web/README.md` — раздел «Admin pages»: что лежит в `(admin)/llm-healthcheck` и `(admin)/agent-runs`

## Что НЕ в PR #20 (выносим в следующие PR этого же Спринта 3)

| Тема | PR |
|---|---|
| **Brand Memory schema** — `brand_memory_core` (JSONB, attached to brand), `brand_memory_overlays` (JSONB + FK на channel), `brand_memory_examples` (text + pgvector с partition-через-`pg_partman` для каждого бренда отдельной партицией или общим helper'ом из PR #17 Спринта 2) + RLS-policy `workspace_isolation` | PR #21 |
| **`BrandMemoryService`** (П11 single source of truth) — единственный интерфейс агентов к BM: `get_core(brand_id)` / `get_overlay(brand_id, channel_id)` / `search_examples(brand_id, query_embedding, k=5)` / `update_core(brand_id, patch)` / `update_overlay(...)` + Redis-кеш `bm:{brand_id}:core` TTL 5 мин + WS-инвалидация при update | PR #21 |
| **API CRUD `/v1/brands/{id}/memory`** — GET / PATCH core, GET / PATCH overlay per channel, list examples | PR #21 |
| **`OnboardingAgent` v0** (D24 в `03`, EPIC-K) — `agents/onboarding/agent.py` + первый skill `apps/backend/skills/onboarding-agent-base/SKILL.md` (en system prompt, `tags=[system]`) + Celery-задача `auto_extract_brand_memory(channel_id)` читает последние 50 постов из `channel_posts` → суммирует через `Claude Haiku 4.5` → пишет в `brand_memory_core` через `BrandMemoryService` → publish `onboarding.brand_memory_extracted` + WS-toast юзеру | PR #22 |
| **Cold-start wizard backend** — endpoint `POST /v1/brands/{id}/onboarding/cold-start` принимает 5–7 ответов (tone, audience, taboos, post_types, post_frequency, …) → OnboardingAgent компонует BM Core без LLM-экстракции из истории (D18 в `03`) | PR #22 |
| **Cold-start wizard UI** — 5–7 экранов wizard'а на `/onboarding/brand-memory` с прогресс-баром, валидацией, autosave в `localStorage` | PR #23 |
| **Brand Memory editor UI** — `/settings/brand/{id}/memory` с табами «Core» / «Overlay per channel» / «Examples»; Tiptap-based editor для текстовых блоков; read-only для всех ролей кроме owner/admin | PR #23 |
| **TTFAA-измерение** в PostHog (signup → channel-connected → brand-memory-done) + ручной dashboard в PostHog: верификация цели «BM extracted ≤ 15 минут» (D15 в `03`) на dev-окружении | PR #23 |
| **Embedding pipeline на проде** — Celery-задача `embed_channel_post` использует `LLMProvider.embed(model="text-embedding-3-small")` → пишет в `channel_post_embeddings_<YYYY_MM>` (партиции из PR #17 Спринта 2); подписывается на event `channel.post_received` из PR #16 Спринта 2; backfill для каналов, у которых уже есть `channel_posts` без embeddings | PR #24 |
| **`channel_post_embeddings` semantic search API** — `POST /v1/brands/{id}/channels/{id}/search` (HNSW + ANN); используется `BrandMemoryService.search_examples()` и будущим Content Agent | PR #24 |
| **Конкуренты-референсы в onboarding** (3–5 каналов, D23 в `03`) — UI шаг wizard'а «Укажите 3–5 каналов-конкурентов» → POST `/v1/brands/{id}/competitors` (endpoint из PR #18 Спринта 2) → user-bot Pyrogram (PR #18 Спринта 2) читает их публичные посты → embedding pipeline индексирует через PR #24 | PR #25 |
| **Метрика «чтение 5 каналов конкурентов ≤ 5 минут»** (метрика приёмки Спринта 3 из `06 §5`) — замер через `agent_runs.latency_ms` + Sentry distributed trace | PR #25 |

## Технические решения

| Решение | Источник / Обоснование |
|---|---|
| **`LLMProvider` — наш собственный интерфейс, не LangChain `ChatModel`** | `05 §3.3`. Мы используем LangChain 0.3 только для tool-calling и structured outputs внутри агентов; абстракцию провайдера держим под своим контролем, чтобы не зависеть от breaking changes upstream и иметь полный control над retry / circuit breaker / cost telemetry. LangChain `ChatModel` инициализируется внутри агента поверх нашего `LLMProvider` (через `RunnableLambda`-обёртку) |
| **`PolzaProvider` через `httpx.AsyncClient`, без официальных SDK** | `05 §3.3`, D38. Один HTTP-клиент проще, чем три SDK (Anthropic / OpenAI / Google) с разными версионными политиками; OpenAI-compat endpoint у LLM-шлюза покрывает всё, что нам нужно на MVP |
| **Pricing-таблица — статически зашитая, ручное обновление 1 раз в квартал** | `04 §16.3`. На MVP объёмы небольшие; автоматическое подтягивание прайса из gateway API — отдельная сложность, пока не нужная. Стоимость в `llm_calls` считаем сами после ответа (даже если gateway пришлёт свой `usage.cost`), потому что нам нужны и input/output split, и RUB-конверсия |
| **Circuit breaker — состояние в Redis, не in-process** | `04 §6.1.3`. У нас N инстансов backend + Celery workers; если один из них «увидел» 5 ошибок подряд, мы хотим, чтобы все остальные тоже остановили вызовы. Redis — естественный shared state |
| **Streaming НЕ на MVP** | `04 §6.1`. Editor «слово за словом» — это nice-to-have, который не входит в 9 acceptance criteria (`03 §2`). Простой `await chat()` проще тестировать и логировать; streaming добавляется в v1.1 одновременно с inline-improve улучшениями |
| **`agent_runs` + `llm_calls` партиционируем НЕ сразу, `llm_calls_daily` — сразу через `pg_partman`** | `04 §10.4`, `04 §18.5`. У `agent_runs` будет ~1 строка на каждый чих агента (~100 в день на пилота) — управляемо без партиций до Спринта 8 / Concierge MVP. У `llm_calls` будет хуже (5–10 calls per run из-за tool-calling) — но мы зачищаем raw через retention в Спринте 8. `llm_calls_daily` партиционируем сразу, потому что это admin-dashboard query target и она будет жить вечно |
| **`opt_in_training` snapshot в момент `start_run`, retention не зачищает run'ы с `true`** | D67 в `04 §18.5`. Согласование на использование данных даётся **в момент** создания контента — если юзер потом выключит opt-in, это не должно отменять согласие на уже созданные run'ы (или мы можем явно ввести «отозвать согласие» как отдельную операцию в v1.2) |
| **`HealthCheckAgent` как первый «настоящий» агент в этом PR** | П12 «Cost telemetry». Нам нужен какой-то агент, чтобы протестировать всю цепочку `BaseAgent` + `LLMProvider` + `AgentRunWriter` end-to-end. Healthcheck — самый дешёвый (10 токенов, копейки) и не зависит от Brand Memory / Skill Compiler конфигурации, которой ещё нет |
| **`AgentRunWriter` + `BaseAgent` собираем уже в PR #20** | П11 «Service layer». Чтобы Спринт 4 / Content Agent сразу собирался на готовом контракте; иначе придётся в Спринте 4 одновременно делать и Content Agent, и `BaseAgent`-обвязку, и Audit Log — слишком много в один PR |
| **Cost-конверсия USD → RUB через `invoices.exchange_rate` snapshot + fallback `USD_TO_RUB_FALLBACK=92.0`** | `04 §18.3`. На раннем MVP у workspace'ов ещё нет invoices, нужен fallback. Когда юзер платит первый раз — последний invoice становится источником truth для всех будущих agent_run cost-денормализаций |
| **`pydantic-to-zod` синхронизация для `AgentRunDetail` / `LLMCallDetail`** | `05 §11.1`. У нас admin-страницы потребляют эти схемы; sync через CI ловит drift |
| **`MockLLMProvider` как первый класс citizen, не «костыль для тестов»** | `05 §13`. У нас в `06 §4 P0.5` зарегистрирован LLM-шлюз, но мы хотим, чтобы `make dev` поднимался **без** этого ключа (для onboarding новых разработчиков и для случая, когда gateway лежит). `MockLLMProvider` — это runtime-провайдер, не testkit |
| **`retention_*` jobs со статусом `active=false` с самого начала** | D57 в `04 §18.5`. Сейчас уже все 5 jobs заведены (3 — в Спринтах 1/2, 2 — в Спринте 3); активация — одной миграцией в Спринте 8 единым `cron.alter_job(..., active := true)` |

## Чего НЕ делаем в Спринте 3 вообще

- **Реальную работу агентов на тестовых данных** — `BaseAgent` + `LLMProvider` собираем, но реальные Content / Publisher / Analyst / Moderation / Orchestrator / Brand Memory / Notification — это Спринты 4, 5, 6, 7, 8 (`06 §6 / §7`). Из 8 MVP-агентов в Спринте 3 закрывается только **`OnboardingAgent` v0**; ещё 6 в очереди
- **Skill-customization L1 / L2 / L3** (D70, F13 в `03`) — `brands.disabled_global_skills` уже заведена в Спринте 1, но UI «Settings → Brand → Skills» — это Спринт 12 (вместе с Settings polish и Admin Panel M9). В Спринте 3 все skills работают как `system`-defaults
- **Cost Guardian реакции (T1–T4 + monthly cap)** — F8 в `03`, D59 / D66 в `04 §16.6 / §16.7`. Сами таблицы для cost-телеметрии (`agent_runs.cost_*`, `llm_calls.cost_*`) собираем в Спринте 3; реакции (auto-downgrade, throttle, kill-switch) и алерты в TG-чат команды — Спринт 8 (вместе с retention activation, dashboard `/v1/admin/llm-calls`, `cost_guardian_react` Celery worker)
- **Inspiration Board L1–L4** (EPIC-L в `03`) — это Спринт 9. Сейчас в Brand Memory `examples` мы пишем только посты **бренда** (из `channel_posts` для каналов с `role='owned'`), не конкурентов
- **Опт-ин «использовать мои данные для дообучения» в Settings** — D67. Колонку `users.opt_in_training` заводим в Спринте 1 (default `false`); UI toggle в Settings → Privacy — Спринт 12. В Спринте 3 значение читаем, но не предлагаем юзеру поменять
- **BYOK (Bring Your Own Key)** — C17 в `03`, post-MVP v1.5. Колонка `llm_calls.provider` поддерживает значение `byok_user_<workspace_id>` (заведена сразу, чтобы не делать миграцию через 9 месяцев), но `LLMProviderFactory` BYOK не выбирает
- **SSE / Streaming в UI** — post-MVP. На MVP UI получает черновики целиком через WS-event `post.draft_generated` (Спринт 4)
- **Полный Brand Memory schema validation** — JSON Schema для `brand_memory_core` / `brand_memory_overlays` появится в PR #21 (отдельно от `LLMProvider`-фундамента). В PR #20 эти таблицы ещё не существуют
- **Семантический поиск через pgvector в Cmd+K** — `06 §8 Спринт 11` для базового Cmd+K (fuzzy через `pg_trgm`); semantic search — post-MVP v1.1
- **Per-tenant LLM rate-limit** — на MVP мы ограничены глобальным rate-limit LLM-шлюза + circuit breaker; per-tenant quotas — это Спринт 10 (Billing v0) + `quotas.py` middleware из `06 §8`
- **Distributed tracing через OpenTelemetry context propagation в LLM calls** — базовая инструментация FastAPI / SQLAlchemy / Celery уже в Спринте 1; для LLM-вызовов добавим custom span'ы в Спринте 8 вместе с CostGuardian (там это критично для debugging cost spikes)

## Метрики приёма PR #20

- `make migrate` — миграция `0020_audit_log_agent_runs_llm_calls.py` применяется на чистой БД; `pg_cron.job` view показывает 2 новых jobs (`retention_chain_of_thought`, `retention_llm_calls_aggregate`) со статусом `active=false`
- `make dev` запускает backend + web + Postgres + Redis + MailHog — **без `LLM_GATEWAY_API_KEY`** (берётся `MockLLMProvider` по умолчанию)
- `make test` — все backend и frontend тесты зелёные; покрытие критических путей `llm-provider-polza` / `agent-run-writer` / `base-agent` / `retention-jobs-smoke` ≥ 80%
- `make lint` / `make typecheck` зелёные — ruff strict, mypy strict (включая `apps/backend/adapters/llm/*`, `apps/backend/agents/base.py`), biome, tsc strict
- 0 Sentry-ошибок на flow «зарегистрироваться → войти → admin зашёл в `/admin/llm-healthcheck` → нажал «Прогнать healthcheck» → mock-провайдер ответил `OK` → строка появилась в `/admin/agent-runs`»
- **Vertical slice (admin-only)** работает целиком: founder в роли `admin` идёт в `/admin/llm-healthcheck` → нажимает «Прогнать healthcheck» → видит latency + cost; идёт в `/admin/agent-runs` → видит новую строку `agent='healthcheck'`, `status='succeeded'`, linked `llm_calls` с `provider='mock'`, `model='claude-haiku-4.5'`
- RLS подтверждён интеграционным тестом: запуск healthcheck в workspace A → юзер workspace'а B через `/v1/admin/agent-runs?workspace_id=<A>` либо 403 (для `user`), либо корректно фильтруется (для `support`, который не видит `chain_of_thought`)
- Все новые backend errors (`LLM_RATE_LIMIT`, `LLM_TIMEOUT`, `LLM_BUDGET_EXCEEDED`, `LLM_PROVIDER_UNAVAILABLE`, `CIRCUIT_BREAKER_OPEN`, `LLM_CONTEXT_LENGTH`, `LLM_CONTENT_FILTER_BLOCKED`) корректно мапятся в JSON и подхватываются `useApiError` хуком на admin-страницах
- Pricing-таблица для всех 4 моделей MVP (`claude-sonnet-4.6`, `gpt-4o-mini`, `gemini-2.5-pro`, `claude-haiku-4.5`) + `text-embedding-3-small` зашита и покрыта тестом `test_pricing_table.py` (расчёт стоимости на известном `usage`)
- `pydantic-to-zod` сгенерировал актуальные TS-типы для `AgentRunDetail` / `LLMCallDetail` / `AgentRunStartedEvent` / `AgentRunFinishedEvent` / `LLMCallFailedEvent` / `CircuitBreakerOpenedEvent`
- Smoke-тест retention функций зелёный: `chain_of_thought IS NULL` для записей старше 30 дней при `opt_in_training=false`; raw `llm_calls` агрегируются в `llm_calls_daily` для записей старше 90 дней; **сами cron-jobs остаются выключенными**

---

Если согласен — приступаю. Если что-то нужно подвинуть (например, перенести `BaseAgent` + `HealthCheckAgent` в PR #21 вместе с `BrandMemoryService`, или сразу добавить `EmbeddingService` чтобы не разделять PR #20 и PR #24, или ужать pricing-таблицу до 2 моделей и докрутить в Спринте 4) — скажи.

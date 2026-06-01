# Архитектура TelegramDigest

Документ описывает реализованную архитектуру: слои, модель данных, потоки
обработки, систему промптов и механизмы отказоустойчивости.

## Обзор

TelegramDigest — асинхронный self-hosted сервис на Python 3.12. Он состоит из двух
режимов работы поверх общего ядра:

- **daemon** (`tgdigest run`) — Telethon-клиент слушает новые сообщения и догружает
  пропущенные, APScheduler запускает дайджесты по времени и по количеству;
- **CLI** (`tgdigest <command>`) — управление чатами, промптами, ручной запуск.

Зависимости направлены внутрь: `CLI/Scheduler → Services → Repositories/Providers
→ DB/Telegram/LLM`. Инфраструктура (БД, Telegram, HTTP-провайдеры) не знает о
бизнес-логике; бизнес-логика (предобработка, анализ) не знает о деталях транспорта.

```
        ┌──────────────┐   ┌───────────────┐
        │  CLI (Typer) │   │ Scheduling    │   входные точки
        └──────┬───────┘   └──────┬────────┘
               │                  │
        ┌──────▼──────────────────▼───────┐
        │   Summarization (DigestService) │   preprocess → stage1 → stage2
        └───┬──────────┬──────────┬───────┘   → render → send
            │          │          │
   ┌────────▼───┐ ┌────▼─────┐ ┌──▼─────────┐
   │ Telegram   │ │  LLM     │ │  Storage   │
   │ (Telethon) │ │(provider)│ │(SQLAlchemy)│
   └────────────┘ └──────────┘ └────────────┘
        ┌──────────────────────────────────┐
        │  Configuration (pydantic-settings)│
        └──────────────────────────────────┘
```

## Слои и модули

| Слой | Модули | Ответственность |
|------|--------|------------------|
| Configuration | `config/settings.py` | `Settings` из env/.env, секции App/Telegram/DB/LLM/Preprocess/Defaults |
| Logging | `logging.py` | structlog (JSON/console), приглушение шумных логгеров |
| Storage | `db/base.py`, `db/models.py`, `db/enums.py`, `db/repositories/*` | async-движок, ORM, репозитории, Alembic |
| Telegram | `telegram/{client,dialogs,mapper,ingest,sender,flood}.py` | auth/сессия, диалоги, ингест, маппинг, отправка, FloodWait |
| LLM | `llm/{base,errors,retry,tokens,json_utils,ollama,openai_compatible,claude,factory}.py` | абстракция провайдера, ретраи, токены/чанкинг, JSON |
| Summarization | `summarization/{preprocess,prompts,schemas,stage1_importance,stage2_digest,render,jsonio,service}.py` | предобработка, промпты, два этапа, рендер, оркестрация |
| Scheduling | `scheduling/{scheduler,triggers}.py` | APScheduler, триггеры по времени/количеству, reconcile, локи |
| CLI / App | `cli/main.py`, `app.py`, `container.py` | команды, daemon, composition root |

**DI:** `Container` (`container.py`) — явная конструкторная инъекция: владеет
движком БД и LLM-провайдером как синглтонами, создаёт сервисы по требованию. Без
DI-фреймворка. Async-сессия открывается per-unit-of-work через
`Database.session()` (commit/rollback в контексте).

## Модель данных (PostgreSQL)

- **chats** — идентичность + настройки мониторинга (enabled, target, интервал,
  min/max, importance_threshold, промпты чата, send_empty). UNIQUE по
  `telegram_chat_id`.
- **chat_states** (1:1) — состояние обработки: `last_seen_message_id` (catch-up),
  `last_processed_message_id` (граница дайджестов), временные метки.
- **messages** — сырые сообщения; UNIQUE `(chat_id, telegram_message_id)` гарантирует
  отсутствие дублей; индекс `(chat_id, date)`.
- **importance_events** — результат Этапа 1: тип, summary, reason, confidence,
  связанные id, ссылка на `digest_run`.
- **digest_runs** — запуски: триггер, статус, окно, метрики (raw/blocks/important),
  модель, `prompt_snapshot` (версии/хэши промптов), токены.
- **digests** — результат Этапа 2: summary, `structured` (JSONB), `body_markdown`,
  is_empty, sent.
- **prompts** — версии глобальных промптов; частичный уникальный индекс «одна
  активная версия на scope».
- **processing_errors** — журнал ошибок по стадиям.

Все enum хранятся как нативные типы PostgreSQL (значения в нижнем регистре).
Начальная миграция (`migrations/versions/0001_initial.py`) строит схему напрямую
из метаданных ORM (`Base.metadata.create_all`) — она всегда соответствует моделям;
требует online-режим (живое подключение), как и делает entrypoint.

## Потоки обработки

### Ингест (без потери состояния)
1. **Catch-up на старте** (`MessageIngestor.catchup_chat`): для нового чата
   подгружаются последние N сообщений; для известного — всё новее
   `last_seen_message_id` (`iter_messages(min_id=…, reverse=True)`). Вставка
   `INSERT … ON CONFLICT DO NOTHING`.
2. **Live** (`events.NewMessage`/`MessageEdited`): фильтр по in-memory watched-set,
   маппинг (`mapper.map_message`), upsert, обновление `last_seen`, вызов
   count-триггера.

### Предобработка (`summarization/preprocess.py`)
Локально, без LLM: отбрасывание service/тривиального/малоинформативного медиа,
дедупликация (с подсчётом повторов), склейка подряд идущих сообщений одного автора,
сегментация на треды по паузам. Результат — `list[Block]` с реальными
`telegram_message_id` как референсами и компактной сериализацией для промпта.

### Этап 1 — выявление значимого (`stage1_importance.py`)
Блоки чанкуются под контекст модели (`llm/tokens.py`). На каждый чанк — строгий
JSON `{"events":[…]}` (`schemas.Stage1Output`). Невалидный JSON чинится одним
повторным запросом (`jsonio.complete_json`); галлюцинированные `message_id`
(вне `known_refs`) отбрасываются; события дедуплицируются по `(id, type)`.

### Этап 2 — дайджест (`stage2_digest.py` + `render.py`)
События с `confidence ≥ importance_threshold` → `DigestContent` (summary,
key_events, attention, open_questions, links, conclusion) → детерминированный
рендер в Telegram-markdown. Перманентные ссылки на сообщения генерируются из
`chat` + `message_id` (`t.me/c/<internal>/<id>` или `t.me/<username>/<id>`), а не
из ответа модели. Пустой результат → «За указанный период значимых событий не
обнаружено» (отправляется только при `send_empty_digest`).

### Оркестрация (`service.py::DigestService.run`)
Окно → создание `digest_run` → preprocess → stage1 → запись событий → фильтр по
порогу → stage2 → рендер → запись `digest` → отправка → продвижение состояния
(`last_processed_message_id`). Защита от параллельных запусков одного чата —
PostgreSQL advisory-lock (`pg_try_advisory_lock`). `--dry-run` не двигает
состояние и не отправляет.

### Планирование (`scheduling/scheduler.py`)
- **По времени**: `IntervalTrigger` на чат; запуск при `unprocessed ≥ min` (или
  пустой при `send_empty`).
- **По количеству**: проверка на каждом сохранённом сообщении; при `≥ max` —
  немедленный запуск.
- **Reconcile** каждые 60с синхронизирует задачи и watched-set с БД (изменения
  через CLI применяются на лету).
- Per-chat `asyncio.Lock` от пересечений в процессе; «зависшие» `running` на старте
  помечаются `failed`.

### Восстановление после рестарта
Durable-состояние в PostgreSQL, сессия Telethon — в volume. На старте: catch-up +
пересборка планировщика. Идемпотентность — через `ON CONFLICT` и состояние чата.

## Система промптов (`summarization/prompts.py`)

Четыре глобальных слоя (`global_system`, `global_digest`, `stage1_instructions`,
`stage2_instructions`) версионируются в таблице `prompts` и сидируются из
`src/tgdigest/prompts/*.md`. Плюс per-chat: контекст (`chat_context_prompt`) и
инструкции дайджеста (`chat_summary_prompt`). `PromptBuilder` собирает финальные
сообщения для каждого этапа и пишет `prompt_snapshot` (версии/хэши) в `digest_run`
для воспроизводимости.

## LLM-абстракция (`llm/`)

`LLMProvider.complete()` оборачивает `_request()` ретраями
(`retry.call_with_retry`, экспонента + jitter, учёт `Retry-After`). Реализации:
- **Ollama** — нативный `/api/chat`, `format=json` (по умолчанию);
- **OpenAI-compatible** — `/chat/completions`, `response_format=json_object`;
- **Claude** — `/v1/messages`, JSON через assistant-prefill `{`.

Ошибки делятся на терминальные (`LLMError`) и повторяемые (`RetryableLLMError`,
для 429/5xx/таймаутов). Контекст модели — реестр в `tokens.py` (по префиксу),
оценка токенов — tiktoken для OpenAI-семейства, иначе эвристика. Новый провайдер
(например, Claude Code CLI) добавляется как ещё одна реализация без изменения
остальных слоёв.

## Отказоустойчивость

- LLM-ретраи с backoff; чанкинг под лимит контекста; починка JSON.
- FloodWait-обёртка для Telethon (`telegram/flood.py`).
- Дедуп через UNIQUE + `ON CONFLICT`; catch-up после рестарта.
- Advisory-lock от параллельных дайджестов; reaping «зависших» запусков.
- Все ошибки пишутся в `processing_errors`; один сбойный чат не валит daemon.

## Тестирование

`pytest` + `pytest-asyncio`. Юнит-тесты (без БД): preprocess, tokens, json_utils,
schemas, prompts, render, mapper, провайдеры (respx), триггеры. Интеграционные
(помечены `integration`, используют `TEST_DATABASE_URL`, иначе пропускаются):
репозитории, `DigestService`, планировщик — на реальном PostgreSQL.

```bash
pytest                       # юнит-тесты
TEST_DATABASE_URL=postgresql+asyncpg://… pytest    # + интеграционные
ruff check src tests && mypy src
```

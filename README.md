# TelegramDigest

Self-hosted сервис, который мониторит ваши Telegram-чаты через **пользовательский
аккаунт** (Telethon, без Bot API) и с помощью LLM формирует короткие дайджесты
**только важных** событий: решений, договорённостей, задач, дедлайнов,
предупреждений, документов, полезных ссылок и вопросов без ответа.

> Принцип: **не пересказывать и не суммировать чат**, а извлекать практически
> ценную информацию и отбрасывать шум.

Ключевые идеи качества и экономии:

1. **Локальная предобработка** до обращения к модели — дедупликация, склейка
   подряд идущих сообщений одного автора, отсев тривиального («ок», «+», 👍),
   тредирование. Для больших чатов это сокращает объём данных в 5–20 раз.
2. **Двухэтапный анализ** — сначала LLM выделяет значимые сообщения
   (структурный JSON), затем по ним строит дайджест. Без «слепой» суммаризации.

---

## Содержание

- [Архитектура](#архитектура)
- [Быстрый старт](#быстрый-старт)
- [Веб-админка](#веб-админка)
- [Конфигурация](#конфигурация)
- [CLI](#cli)
- [Как это работает](#как-это-работает)
- [LLM-провайдеры](#llm-провайдеры)
- [Разработка](#разработка)

---

## Архитектура

Чистое разделение слоёв; зависимости направлены внутрь
(CLI/Scheduler → Services → Repositories/Providers → DB/Telegram/LLM):

| Слой | Назначение |
|------|------------|
| **Configuration** | `pydantic-settings`, `.env` |
| **Storage** | SQLAlchemy 2.x (async), репозитории, Alembic |
| **Telegram** | Telethon: авторизация, ингест, отправка, FloodWait |
| **LLM** | `LLMProvider`: Ollama / OpenAI-compatible / Claude |
| **Summarization** | предобработка, промпты, stage1/stage2, рендер, оркестрация |
| **Scheduling** | APScheduler: триггеры по времени и по количеству |
| **CLI** | Typer: управление и daemon (offline-first) |
| **Web** | FastAPI + HTMX/Jinja: веб-админка (работает только с БД) |

**Daemon — единственный владелец Telegram-сессии.** Веб-админка и CLI работают
только с PostgreSQL и не открывают второй Telethon-клиент: список диалогов берётся
из кэша, который наполняет daemon, а ручные дайджесты ставятся в очередь, которую
daemon исполняет. Поэтому управление чатами работает на лету, без остановки daemon.

Подробности — в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Быстрый старт

Требуется Docker + Docker Compose. Понадобятся `api_id` и `api_hash` с
<https://my.telegram.org>.

```bash
# 1. Конфигурация
cp .env.example .env
#   укажите TG_API_ID, TG_API_HASH и WEB_PASSWORD (и при желании настройте LLM)

# 2. Одноразовый вход в Telegram (введёте код из Telegram, при необходимости 2FA).
#    Сессия сохранится в Docker volume `session`.
docker compose run --rm app tgdigest login

# 3. Запуск всего стека: daemon + веб-админка + БД
docker compose up -d
```

Откройте **веб-админку**: <http://localhost:8080> (вход по `WEB_PASSWORD`). Через
неё удобнее всего добавлять чаты, настраивать пороги/промпты, смотреть дайджесты и
запускать их вручную.

Локальная модель через Ollama (приоритетный, бесплатный сценарий):

```bash
docker compose --profile ollama up -d
docker compose exec ollama ollama pull llama3.1:8b
```

Те же действия из CLI (работают **при запущенном daemon** — без остановки):

```bash
docker compose run --rm app tgdigest list-dialogs
docker compose run --rm app tgdigest watch-chat --interval 180 --max 300 -- -100123456789
docker compose run --rm app tgdigest run-digest @username --dry-run
```

> Отрицательные id (`-100…`) отделяйте `--` от опций, иначе CLI примет их за флаг.

Дайджесты по умолчанию приходят в «Избранное» (Saved Messages). Чтобы слать в
другой чат — `--target <chat_id>` у `watch-chat` или поле «Целевой чат» в админке.

> «Одной командой `docker compose up -d`» работает после одноразового `login`
> (или если задан `TG_STRING_SESSION` в `.env`). Это ограничение Telethon:
> код подтверждения нужно ввести один раз интерактивно.

---

## Веб-админка

Лёгкая панель на FastAPI + HTMX (сервис `web`, порт `8080`), вход по паролю
(`WEB_PASSWORD`). Возможности v1:

- список отслеживаемых чатов со статусом и быстрыми действиями;
- добавление чата из **списка диалогов** (его наполняет daemon) или по id/@username;
- настройка интервала, порогов, целевого чата, промптов чата;
- кнопки **«Сделать дайджест»** и «Сухой прогон» (ставят запрос в очередь);
- просмотр дайджестов, запусков анализа и журнала ошибок.

Админка не подключается к Telegram напрямую: данные диалогов берутся из кэша, а
запуски дайджестов исполняет daemon. Поэтому всё работает, пока daemon запущен,
и не конфликтует за сессию.

---

## Конфигурация

Все параметры — через переменные окружения (см. [`.env.example`](.env.example)).
Основное:

- **Telegram:** `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_PATH`, `TG_STRING_SESSION?`
- **LLM:** `LLM_PROVIDER` (`ollama`/`openai`/`claude`), `LLM_MODEL`, `LLM_BASE_URL`,
  `LLM_API_KEY?`, `LLM_MAX_TOKENS`, `LLM_CONTEXT_WINDOW?`, `LLM_STAGE1_MODEL?`
- **Предобработка:** `PRE_MERGE_GAP_SECONDS`, `PRE_MIN_MEANINGFUL_LEN`,
  `PRE_TRIVIAL_TOKENS`, `PRE_THREAD_GAP_MINUTES`
- **Дефолты чатов:** `DEFAULT_INTERVAL_MINUTES`, `DEFAULT_MIN_MSGS`,
  `DEFAULT_MAX_MSGS`, `DEFAULT_IMPORTANCE_THRESHOLD`, `DEFAULT_SEND_EMPTY`
- **Веб-админка:** `WEB_PASSWORD`, `WEB_SECRET_KEY`, `WEB_PORT`,
  `WEB_DIALOG_REFRESH_MINUTES`, `WEB_REQUEST_POLL_SECONDS`

Настройки конкретного чата хранятся в БД и меняются через CLI (`watch-chat`,
`set-chat-prompt`, `show-chat-config`).

---

## CLI

```
tgdigest login                      # одноразовая авторизация в Telegram
tgdigest run                        # daemon: ингест + планировщик + кэш диалогов + очередь
tgdigest web                        # веб-админка (uvicorn)
tgdigest list-dialogs [--limit N] [--live]   # из кэша daemon; --live — напрямую
tgdigest list-chats                 # чаты под мониторингом
tgdigest watch-chat <chat> [--interval --min --max --threshold --target] [--resolve]
tgdigest unwatch-chat <chat> [--purge]
tgdigest run-digest <chat> [--dry-run]        # без --dry-run ставит запрос в очередь daemon
tgdigest set-chat-prompt <chat> [--context TEXT|@file] [--summary TEXT|@file]
tgdigest set-global-prompt [--system|--digest|--stage1|--stage2 TEXT|@file]
tgdigest show-chat-config <chat>
tgdigest reprocess-messages <chat> --since <ISO> --until <ISO> [--no-send]
tgdigest seed-prompts               # засеять дефолтные промпты (идемпотентно)
tgdigest healthcheck                # проверка БД/сессии (для healthcheck контейнера)
```

`<chat>` — это `telegram_chat_id` (например `-100123456789`) или `@username`.

**Offline-first:** команды управления работают только с БД и не открывают второй
Telethon-клиент — их можно выполнять **при запущенном daemon**. Telegram нужен
только для `login`, `run`, а также `watch-chat --resolve` и `list-dialogs --live`
(их запускайте при остановленном daemon). `run-digest` без `--dry-run` ставит
запрос в очередь, который исполняет daemon; `--dry-run` считает локально и печатает
результат, ничего не отправляя.

---

## Как это работает

1. **Ингест.** Telethon-клиент получает новые сообщения (live) и при старте
   догружает пропущенное (catch-up). Каждое сообщение сохраняется в PostgreSQL
   с дедупликацией (`UNIQUE(chat_id, telegram_message_id)`).
2. **Триггер дайджеста.** Срабатывает по любому из условий: по времени
   (`summary_interval_minutes`) или по количеству новых сообщений
   (`max_messages_before_digest`).
3. **Предобработка.** Локально, без LLM: отсев service/тривиального, дедуп,
   склейка по авторам, тредирование. На выходе — компактный транскрипт.
4. **Этап 1 — поиск важного.** Транскрипт чанкуется под контекст модели; LLM
   возвращает строгий JSON со значимыми событиями (тип, описание, причина,
   confidence, связанные сообщения). Результат сохраняется.
5. **Этап 2 — дайджест.** По событиям с `confidence ≥ importance_threshold`
   строится короткий структурированный дайджест и отправляется в целевой чат.
   Если важного нет — «За указанный период значимых событий не обнаружено»
   (отправляется только при `send_empty_digest`).

Вся аналитика (события, confidence, типы, запуски, тексты дайджестов, ошибки,
версии промптов) хранится в БД для последующего улучшения алгоритмов.

---

## LLM-провайдеры

За единым интерфейсом `LLMProvider`:

- **Ollama** (по умолчанию) — локально, бесплатно. `LLM_BASE_URL=http://ollama:11434`.
- **OpenAI-compatible** — OpenAI, OpenRouter, LM Studio, vLLM, groq и т.п.
- **Claude API** — Anthropic.

Архитектура позволяет позже добавить Claude Code CLI / Codex CLI как ещё одну
реализацию провайдера без изменений в остальных слоях.

---

## Разработка

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check src tests
mypy src
pytest                      # юнит-тесты
pytest -m integration       # репозитории на Postgres (нужен Docker/testcontainers)
```

Миграции:

```bash
alembic upgrade head
alembic revision --autogenerate -m "описание"
```

# tg_jobs_parser

Локальное **Windows-first** MVP-приложение на Python: парсинг Telegram-каналов с IT-вакансиями через **Telethon** (ваш аккаунт, MTProto), правиловое извлечение полей, **опциональный AI** (OpenRouter), фильтры, дедуп, метрики, экспорт **Excel/JSON**, CLI (**Typer**) и **Streamlit** + **Plotly**.

Каналы задаются в `config.yaml` (или `--channels`). Опционально в Streamlit доступен best-effort **поиск кандидатов каналов** через DuckDuckGo (не заменяет ручной список источников).

## Telethon: что подготовить локально

**Не передавайте никому** `api_hash`, коды SMS и файл сессии. Держите их только в `.env` и в `sessions/` (каталог в `.gitignore`).

1. Зарегистрируйте приложение на [https://my.telegram.org/apps](https://my.telegram.org/apps) и получите **API ID** (целое число) и **API hash**.
2. Создайте `.env` рядом с `config.yaml` (образец в `.env.example`):

```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

3. Установите проект через Poetry (см. ниже). Дополнительно для ускорения Telethon на Windows: `poetry install --extras telethon` (ставит **cryptg**).
4. В `config.yaml` укажите `telegram.telethon_session_file` (путь к файлу сессии).
5. **Первый запуск** удобнее из консоли: Telethon запросит телефон, код из SMS и при необходимости пароль 2FA — так создаётся файл сессии. В Streamlit интерактивная авторизация обычно неудобна; сначала выполните хотя бы один успешный `parse` через CLI.

```powershell
python -m tg_jobs_parser.cli parse --config config.yaml
# или
poetry run python -m tg_jobs_parser.cli parse --config config.yaml
```

### Сообщения в консоли при первом запуске

- **`Failed to load SSL library` / `cryptg module not installed`** — часто появляется на Windows: Telethon ищет нативный OpenSSL для ускорения; при этом соединение с `149.154.x.x` всё равно устанавливается (как у вас: `Connection ... complete`). Для более быстрого шифрования можно поставить **`cryptg`** (он уже в extra `telethon` в `pyproject.toml`).
- **`Please enter your phone`** — первая авторизация: сессии ещё нет. Введите номер в формате **`+79991234567`**, затем код из Telegram/SMS; при 2FA — пароль облака. Чтобы не набирать номер руками каждый раз при новой сессии, добавьте в `.env` переменную **`TELEGRAM_PHONE`** (см. `.env.example`). Код из SMS всё равно нужно будет ввести в консоли один раз (или положите **`TELEGRAM_2FA_PASSWORD`** только на своей машине, если включена двухфакторка).

## Структура проекта

```text
tg_jobs_parser/
  README.md
  pyproject.toml
  poetry.lock
  config.yaml
  .env.example
  .gitignore

  tg_jobs_parser/
    __init__.py
    main.py
    parse_runner.py
    channel_search.py

    cli/          — Typer CLI
    web/          — Streamlit UI
    config/       — YAML + Pydantic-схема
    parsers/      — Telethon, channel_username
    extraction/   — нормализация, vacancy score, специализации, зарплата...
    ai/           — OpenRouter (OpenAI SDK), промпты, кэш, слияние с rules
    models/       — Vacancy, метрики, ParseResult
    filters/      — фильтры по конфигу/CLI/UI
    deduplication/- хэши + rapidfuzz
    analytics/    — метрики, matplotlib/plotly
    exporters/    — Excel, JSON
    utils/        — логгер, даты, текст

  tests/
```

## Установка (Poetry)

Проект **Poetry-first**: зависимости и extras задаются в `pyproject.toml` / `poetry.lock`.

```powershell
cd путь\к\tg_jobs_parser
poetry install
# опционально ускорение Telethon (cryptg):
poetry install --extras telethon
# опционально AI (OpenRouter + openai SDK):
poetry install --extras ai
```

Проверка окружения:

```powershell
poetry run python -c "import telethon; print(telethon.__version__)"
```

Важно: **голый** `python` в консоли часто указывает на другой интерпретатор, не на Poetry-venv.
Используйте **`poetry run python -m tg_jobs_parser.cli …`** или **`poetry shell`**, либо активируйте `.venv\Scripts\Activate.ps1`.

## Конфигурация

Основной файл: `config.yaml` (структура соответствует `tg_jobs_parser/config/schema.py`).

Специализации и синонимы объединяются с предустановками из схемы; словарь технологий и исключений расширяется через `dictionaries`.

### Отбор постов как вакансий (`parser`)

- **`vacancy_score_threshold`** — минимальный общий счёт правил из `extraction/vacancy_detector.py` (маркеры вакансии, разработческие формулировки, намёки на зарплату, грейд, компания, **email или @**, слова формата remote/office…).
- **`vacancy_min_substantive_score`** — минимальный счёт **без** бонуса за email/`@username`. Значение **1** и выше отсекает короткие обсуждения, где сработало только упоминание контакта, но нет хотя бы одного другого признака (например зарплаты, офис/hybrid или явного «vacancy»/developer и т.д.).

### Секция `ai` (опционально)

По умолчанию **`ai.enabled: false`** — работают только эвристики из `extraction/`. При включении каждый пост (до дедупликации) дополнительно разбирается через **OpenRouter**; HTTP-клиент — официальный пакет **[`openai`](https://github.com/openai/openai-python)** (OpenAI-compatible API, тот же протокол, что у OpenRouter).

| Поле | Назначение |
|------|------------|
| **`enabled`** | Включить LLM-разбор. |
| **`mode`** | `assist` — AI уточняет поля поверх rules; `primary` — AI основной источник (rules — fallback при ошибке). |
| **`model`** | Id основной модели из **`llm_models.yaml`**. |
| **`fallback_models`** | Запасные id (порядок = приоритет); пусто — `default_fallbacks` из реестра. |
| **`concurrency`** | Параллельные LLM-запросы. |
| **`requests_per_minute`** | Темп запросов (пауза между стартами вызовов). |
| **`stop_llm_when_rate_limited`** | При `X-RateLimit-Remaining: 0` — **отключить LLM** до конца прогона (только rules). |
| **`max_retries_on_429`** | Повторы при 429. |
| **`max_completion_tokens`** | Лимит выходных токенов на запрос (`max_tokens` в API). |
| **`parser.progress_batch_posts`** | Обновление UI / partial JSON каждые N постов. |
| **`output.save_progress_batches`** | Сохранять `output/partial/partial_*.json` по ходу. |
| **`max_posts_per_run`** | Лимит LLM-вызовов за прогон (`0` = без лимита). |
| **`use_cache`** | Дисковый кэш в `.cache/ai_extractions/`. |
| **`reasoning_enabled`** | Для reasoning-моделей; для стабильного JSON лучше **`false`**. |

Подробный план и архитектура: **`zaddai.md`**.

#### Что извлекает AI (RU + EN)

Приоритет полей: **зарплата**, **валюта**, **специализация** (в т.ч. новые роли, не только словарь конфига), **формат работы**, **локация**, **ссылки на отклик** (`application_urls` — hh.ru, careers и т.д.).

Посты «резюме / курсы / не вакансия» отсекаются флагом **`is_job_post`** и не попадают в выдачу.

#### Подготовка OpenRouter

1. Ключ: [https://openrouter.ai/keys](https://openrouter.ai/keys) → в `.env`:

```env
OPENROUTER_API_KEY=sk-or-v1-...
```

2. Установите extra с OpenAI SDK:

```powershell
poetry install --extras ai
```

3. Каталог моделей — **`llm_models.yaml`** (id, название, цена). В `config.yaml` — только выбор id:

```yaml
ai:
  enabled: true
  model: deepseek/deepseek-v4-flash
```

4. Запуск:

```powershell
poetry run python -m tg_jobs_parser.cli parse --ai
```

В Streamlit: sidebar → **«Использовать LLM для разбора текста»**.

В JSON-отчёте (`run_params`) появляется блок **`ai_stats`**: число вызовов, кэш, отсечённые не-вакансии, токены.

> **Приватность:** текст постов уходит на серверы OpenRouter/провайдера модели. Для полностью локального разбора — см. roadmap в `zaddai.md` (Ollama, итерация 2).

#### Лимиты API

При **429** / `X-RateLimit-Remaining: 0` срабатывает `stop_llm_when_rate_limited` — LLM отключается до конца прогона (дальше rules). Темп задаётся `requests_per_minute` и `concurrency`. Повторный прогон с **`use_cache: true`** не вызывает API для уже разобранных постов.

#### OpenAI SDK vs «голый» httpx

Используется **OpenAI SDK**, потому что OpenRouter совместим с OpenAI API: меньше своего кода, встроенные retries и таймауты, нативный `response_format` для JSON. Пакет `httpx` остаётся в проекте для других задач (Telethon/web), но LLM-вызовы идут через `openai`.

## CLI

Из корня проекта (рядом с `config.yaml`). Предпочтительно (**без предупреждения runpy**):

```powershell
python -m tg_jobs_parser.cli parse --config config.yaml
python -m tg_jobs_parser.cli parse --ai
python -m tg_jobs_parser.cli analyze --from-json output/vacancies.json
python -m tg_jobs_parser.cli export --from-json output/vacancies.json --out reexport.xlsx
python -m tg_jobs_parser.cli run-web
```

Вариант ``python -m tg_jobs_parser.cli.app ...`` тоже работает, но может выдать ``RuntimeWarning`` при старте.

После `poetry install` доступна короткая команда:

```powershell
poetry run tg-jobs-parser parse --config config.yaml
```

Логи: консоль + `logs/app.log` (прогресс LLM: `AI LLM: 100/1500 — @channel [запрос API]`).

### Параметры `work_formats` в конфиге

Значения: `remote`, `hybrid`, `office`, `relocation`, **`any`** (нет фильтра), **`exclude_office`** — отбросить строки с определённым офисным форматом.

## Streamlit UI

Из корня проекта:

```powershell
streamlit run tg_jobs_parser/web/streamlit_app.py
```

Поддерживается ввод каналов вручную, выбор специализаций/исключений/дат, **опциональный LLM (OpenRouter)**, best-effort поиск каналов (HTML DuckDuckGo), таблица результатов, графики Plotly, скачивание Excel/JSON. После прогона с AI в sidebar показывается сводка `ai_stats`.

## Результаты

По умолчанию директория `output/`:

- `vacancies.xlsx` — листы `vacancies_all`, `vacancies_unique`, `duplicates`, `metrics_*`.
- `vacancies.json` — параметры запуска (как сохранены), посты→вакансии, метрики, поле `parse_warnings` (раньше могло быть `web_limitation_warning` в старых файлах).

Графики **matplotlib** (если включены в CLI без `--skip-charts`): каталог `output/charts/` (или `output/<charts_directory>` из конфига).

## Тесты

```powershell
pytest
```

## Архитектура

```text
Telethon → RawPost → extraction/ (rules) → [ai/ OpenRouter] → filters → dedup → export
```

- **`extraction/`** — быстрые эвристики (всегда).
- **`ai/`** — опционально: OpenAI SDK → OpenRouter, JSON-схема, кэш, `merge_rules_and_ai`.
- **`parsers/base.py`** — интерфейс источника; реализация — **Telethon**.

## Roadmap / следующие шаги

- Расширение **Telethon**: `joinchat`/каналы без `@username`, rate-limit, неинтерактивный вход.
- Альтернатива **Pyrogram**.
- **Локальные LLM** (Ollama / LM Studio) через тот же OpenAI-compatible endpoint.
- **SQLite/PostgreSQL** для хранения прогонов.
- **Docker**, планировщик задач (Task Scheduler / cron), **бот** для алертов.
- Расширенный поиск каналов (несколько запросов DuckDuckGo + слияние).
- **FastAPI + React**-дашборд.

# tg_jobs_parser

Локальное **Windows-first** MVP-приложение на Python: парсинг Telegram-каналов с IT-вакансиями через **Telethon** (ваш аккаунт, MTProto), без LLM: правиловое извлечение полей, фильтры, дедуп, метрики, экспорт **Excel/JSON**, CLI (**Typer**) и **Streamlit** + **Plotly**.

Каналы задаются в `config.yaml` (или `--channels`). Опционально в Streamlit доступен best-effort **поиск кандидатов каналов** через DuckDuckGo (не заменяет ручной список источников).

## Telethon: что подготовить локально

**Не передавайте никому** `api_hash`, коды SMS и файл сессии. Держите их только в `.env` и в `sessions/` (каталог в `.gitignore`).

1. Зарегистрируйте приложение на [https://my.telegram.org/apps](https://my.telegram.org/apps) и получите **API ID** (целое число) и **API hash**.
2. Создайте `.env` рядом с `config.yaml` (образец в `.env.example`):

```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

3. Установите проект: `pip install -e .` — в зависимости уже входят **telethon** и **python-dotenv**. Дополнительно для ускорения шифрования на Windows: `pip install ".[telethon]"` (extra ставит **cryptg**).
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
  requirements.txt
  pyproject.toml
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
    models/       — Vacancy, метрики, ParseResult
    filters/      — фильтры по конфигу/CLI/UI
    deduplication/- хэши + rapidfuzz
    analytics/    — метрики, matplotlib/plotly
    exporters/    — Excel, JSON
    utils/        — логгер, даты, текст

  tests/
```

## Установка на Windows

Откройте PowerShell или **cmd**:

```powershell
cd путь\к\tg_jobs_parser
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Или установка пакета в editable-режиме:

```powershell
pip install -e ".[dev]"
```

### Poetry

**Telethon входит в optional extra** `telethon`: при обычном `poetry install` пакет **не** ставится, поэтому получается предупреждение про fallback на web.

```powershell
cd путь\к\tg_jobs_parser
poetry install
# опционально ускорение Telethon (cryptg): poetry install --extras telethon
```

Проверка, что Telethon в активном venv: `poetry run python -c "import telethon; print(telethon.__version__)"`.

Важно: **голый** `python` в консоли часто указывает на другой интерпретатор, не на Poetry-окружение.
Тогда Telethon «есть» при `poetry run python -c ...`, но пропадает при `python -m tg_jobs_parser.cli`.
Используйте **`poetry run python -m tg_jobs_parser.cli …`** или один раз выполните **`poetry shell`**, либо активируйте `.venv\\Scripts\\activate`.

## Конфигурация

Основной файл: `config.yaml` (структура соответствует `tg_jobs_parser/config/schema.py`).

Специализации и синонимы объединяются с предустановками из схемы; словарь технологий и исключений расширяется через `dictionaries`.

### Отбор постов как вакансий (`parser`)

- **`vacancy_score_threshold`** — минимальный общий счёт правил из `extraction/vacancy_detector.py` (маркеры вакансии, разработческие формулировки, намёки на зарплату, грейд, компания, **email или @**, слова формата remote/office…).
- **`vacancy_min_substantive_score`** — минимальный счёт **без** бонуса за email/`@username`. Значение **1** и выше отсекает короткие обсуждения, где сработало только упоминание контакта, но нет хотя бы одного другого признака (например зарплаты, офис/hybrid или явного «vacancy»/developer и т.д.).

## CLI

Из корня проекта (рядом с `config.yaml`). Предпочтительно (**без предупреждения runpy**):

```powershell
python -m tg_jobs_parser.cli parse --config config.yaml
python -m tg_jobs_parser.cli analyze --from-json output/vacancies.json
python -m tg_jobs_parser.cli export --from-json output/vacancies.json --out reexport.xlsx
python -m tg_jobs_parser.cli run-web
```

Вариант ``python -m tg_jobs_parser.cli.app ...`` тоже работает, но может выдать ``RuntimeWarning`` при старте.

После `pip install -e .` доступна короткая команда:

```powershell
tg-jobs-parser parse --config config.yaml
```

Логи: консоль + `logs/app.log`.

### Параметры `work_formats` в конфиге

Значения: `remote`, `hybrid`, `office`, `relocation`, **`any`** (нет фильтра), **`exclude_office`** — отбросить строки с определённым офисным форматом.

## Streamlit UI

Из корня проекта:

```powershell
streamlit run tg_jobs_parser/web/streamlit_app.py
```

Поддерживается ввод каналов вручную, выбор специализаций/исключений/дат, best-effort поиск каналов (HTML DuckDuckGo), таблица результатов, графики Plotly, скачивание Excel/JSON.

## Результаты

По умолчанию директория `output/`:

- `vacancies.xlsx` — листы `vacancies_all`, `vacancies_unique`, `duplicates`, `metrics_*`.
- `vacancies.json` — параметры запуска (как сохранены), посты→вакансии, метрики, поле `parse_warnings` (раньше могло быть `web_limitation_warning` в старых файлах).

Графики **matplotlib** (если включены в CLI без `--skip-charts`): каталог `output/charts/` (или `output/<charts_directory>` из конфига).

## Тесты

```powershell
pytest
```

## Архитектура (расширение под AI)

- Извлечение вынесено в модуль `extraction/`: можно заменить/обернуть эвристики **единым интерфейсом** без изменения экспорта/метрик.
- `parsers/base.py` — общий интерфейс источника постов; реализация чтения каналов — **Telethon**.

## Roadmap / следующие шаги

- Расширение **Telethon**: `joinchat`/каналы без `@username`, rate-limit, неинтерактивный вход.
- Альтернатива **Pyrogram**.
- **AI/NLP**-модуль структурирования текста поверх этого MVP.
- **SQLite/PostgreSQL** для хранения прогонов.
- **Docker**, планировщик задач (Task Scheduler / cron), **бот** для алертов.
- Расширенный **поиск каналов**, **нормализация валют**, **FastAPI + React**-дашборд.

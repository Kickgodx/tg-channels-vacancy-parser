# tg_jobs_parser

Локальное **Windows-first** приложение для парсинга Telegram-каналов с IT-вакансиями: **Telethon** (ваш аккаунт, MTProto), правиловое извлечение полей, опциональный **AI** (OpenRouter), фильтры, дедуп, метрики, экспорт **Excel/JSON**.

**Основной способ работы** — веб-интерфейс на **React** + **FastAPI** backend + **PostgreSQL** (Docker Compose). Парсинг запускается из UI, прогресс идёт по SSE, вакансии сохраняются в БД.

Дополнительно — **CLI (Typer)** для первой авторизации Telethon и отладки без UI.

---

## Быстрый старт (Docker Compose)

### Требования

- Docker Desktop (Windows / macOS / Linux)
- Файл `.env` рядом с `config.yaml` (см. [Telethon](#telethon-что-подготовить-локально))
- Каталог `sessions/` для файла MTProto-сессии (монтируется в backend)

### Запуск

```powershell
cd путь\к\tg_jobs_parser
# Скопируйте .env.example → .env и заполните TELEGRAM_API_ID / TELEGRAM_API_HASH
docker compose up --build
```

| Сервис   | URL |
|----------|-----|
| **Web UI** | http://localhost:5173 |
| **API** (OpenAPI) | http://localhost:8000/docs |
| **PostgreSQL** | `localhost:5432` (user/pass/db: `tgjobs` / `tgjobs` / `tgjobs`) |

### Первая авторизация Telethon

В контейнере backend интерактивный ввод телефона и кода удобнее, чем в браузере:

```powershell
docker compose exec backend python -m tg_jobs_parser.cli parse --config /app/config.yaml
```

Введите номер (`+79991234567`), код из Telegram/SMS и при необходимости пароль 2FA. Файл сессии появится в `sessions/` на хосте (volume).

После этого парсинг запускайте из UI (**Запуск** → настройте фильтры → **Запустить парсинг**).

### Переменные backend (`.env`)

```env
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
OPENROUTER_API_KEY=...          # если ai.enabled в config.yaml

# Опционально (для docker compose уже задано в compose-файле):
# DATABASE_URL=postgresql+psycopg://tgjobs:tgjobs@postgres:5432/tgjobs
# CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
# API_KEY=                        # если задан — заголовок X-API-Key для API
```

---

## Web UI (React)

Стек: **Vite + TypeScript + React Router + TanStack Query + Recharts**.

| Страница | Описание |
|----------|----------|
| **Запуск** | Форма парсинга (каналы, даты, специализации, формат работы, AI, пороги score), поиск каналов (DuckDuckGo) |
| **Прогон** | Прогресс (SSE), метрики, графики, блок AI (токены и стоимость input/output/total), таблица вакансий, экспорт Excel/JSON |
| **Вакансии** | Все уникальные вакансии из PostgreSQL: фильтры, пагинация, сортировка по клику на заголовок |

Возможности таблицы: сортировка asc/desc по дате, зарплате, валюте, формату, грейду, score; выбор видимых колонок; модальное окно с полным текстом.

Локальная разработка frontend без Docker:

```powershell
cd frontend
npm install
# VITE_API_BASE_URL=http://localhost:8000/api/v1
npm run dev
```

Backend отдельно:

```powershell
poetry install
# DATABASE_URL=postgresql+psycopg://tgjobs:tgjobs@localhost:5432/tgjobs
uvicorn backend.main:app --reload --port 8000
```

---

## Backend API (FastAPI)

Префикс: `/api/v1`.

| Метод | Путь | Назначение |
|-------|------|------------|
| `GET` | `/health` | Статус API и подключения к БД |
| `GET` | `/config/defaults` | Значения по умолчанию для формы |
| `GET` | `/config/llm-models` | Каталог моделей из `llm_models.yaml` |
| `POST` | `/channels/search` | Поиск кандидатов каналов |
| `POST` | `/runs` | Запуск парсинга (фоновый поток) |
| `GET` | `/runs/{id}` | Статус и метрики прогона |
| `GET` | `/runs/{id}/events` | SSE: прогресс в реальном времени |
| `POST` | `/runs/{id}/cancel` | Отмена |
| `GET` | `/runs/{id}/vacancies` | Вакансии прогона (`sort_by`, `sort_order`) |
| `GET` | `/runs/{id}/export.xlsx` / `.json` | Скачивание отчёта |
| `GET` | `/vacancies` | Глобальный список вакансий (фильтры, сортировка, пагинация) |

Ядро парсинга (`tg_jobs_parser.parse_runner`) вызывается из `backend/services/run_service.py`; результаты пишутся в PostgreSQL (UPSERT по `id` вакансии).

---

## Структура проекта

```text
tg_jobs_parser/
  README.md
  pyproject.toml
  config.yaml
  llm_models.yaml
  docker-compose.yml
  Dockerfile.backend

  backend/                 — FastAPI, SQLAlchemy, RunService, SSE
    api/routes/            — health, config, channels, runs, vacancies
    db/                    — модели, репозиторий, UPSERT вакансий
    services/              — run_service, progress_bus, registry

  frontend/                — React UI (Vite)
    src/pages/             — Dashboard, Run, Vacancies
    src/components/        — ParseForm, VacancyTable, MetricsCharts

  tg_jobs_parser/          — ядро парсинга (общее для API и CLI)
    parse_runner.py
    cli/                   — Typer CLI
    config/                — YAML + Pydantic
    parsers/               — Telethon
    extraction/            — rules
    ai/                    — OpenRouter, кэш, token cost
    filters/, deduplication/, analytics/, exporters/

  sessions/                — Telethon session (не в git)
  .cache/ai_extractions/   — дисковый кэш LLM
  output/                  — Excel/JSON при экспорте
  tests/
```

---

## Telethon: что подготовить локально

**Не передавайте никому** `api_hash`, коды SMS и файл сессии. Держите их только в `.env` и в `sessions/` (каталог в `.gitignore`).

1. Зарегистрируйте приложение на [https://my.telegram.org/apps](https://my.telegram.org/apps) и получите **API ID** и **API hash**.
2. Создайте `.env` рядом с `config.yaml` (образец в `.env.example`).
3. В `config.yaml` укажите `telegram.telethon_session_file` (например `sessions/tg_jobs.session`).
4. **Первый запуск** — интерактивная авторизация (см. [Docker](#первая-авторизация-telethon) или CLI ниже).

### Сообщения в консоли при первом запуске

- **`Failed to load SSL library` / `cryptg module not installed`** — на Windows Telethon может работать без cryptg; для ускорения: `poetry install --extras telethon`.
- **`Please enter your phone`** — первая авторизация. В `.env` можно задать **`TELEGRAM_PHONE`**; код из SMS вводится один раз (или **`TELEGRAM_2FA_PASSWORD`** при 2FA).

---

## Установка без Docker (Poetry)

```powershell
cd путь\к\tg_jobs_parser
poetry install
poetry install --extras telethon   # cryptg (опционально)
poetry install --extras ai         # OpenRouter
```

Проверка:

```powershell
poetry run python -c "import telethon; print(telethon.__version__)"
```

Используйте **`poetry run python -m tg_jobs_parser.cli …`** или активируйте `.venv`.

---

## Конфигурация

Основной файл: `config.yaml` (схема: `tg_jobs_parser/config/schema.py`).

### Отбор постов (`parser`)

- **`vacancy_score_threshold`** — минимальный общий score правил.
- **`vacancy_min_substantive_score`** — минимум без бонуса за email/@ (отсекает «пустые» контакты).

### Секция `ai` (опционально)

По умолчанию **`ai.enabled: false`**. При включении — разбор через **OpenRouter** (OpenAI-compatible SDK).

| Поле | Назначение |
|------|------------|
| `enabled` | Включить LLM |
| `mode` | `assist` / `primary` |
| `model` | Id из `llm_models.yaml` |
| `fallback_models` | Запасные модели |
| `concurrency`, `requests_per_minute` | Темп и параллелизм |
| `stop_llm_when_rate_limited` | Отключить LLM при 429 до конца прогона |
| `max_completion_tokens` | Лимит выходных токенов |
| `use_cache` | Дисковый кэш `.cache/ai_extractions/` |

Ключ в `.env`: `OPENROUTER_API_KEY=sk-or-v1-...`

В UI и в `run_params` доступен блок **`ai_stats`**: вызовы, кэш, токены, **стоимость USD** (input / output / total).

> **Приватность:** текст постов уходит на серверы OpenRouter/провайдера модели.

### `work_formats` в фильтрах

`remote`, `hybrid`, `office`, `relocation`, **`any`**, **`exclude_office`**.

---

## CLI (отладка и авторизация Telethon)

Повседневный парсинг — через **React UI** → API. CLI нужен в основном для **первого входа в Telegram** (см. [Docker](#первая-авторизация-telethon)) и для работы без БД (пишет в `output/`).

```powershell
python -m tg_jobs_parser.cli parse --config config.yaml
python -m tg_jobs_parser.cli parse --ai
python -m tg_jobs_parser.cli analyze --from-json output/vacancies.json
python -m tg_jobs_parser.cli export --from-json output/vacancies.json --out reexport.xlsx
```

Короткая команда после `poetry install`: `poetry run tg-jobs-parser parse`

Логи: консоль + `logs/app.log`. Графики CLI: `output/charts/` (matplotlib).

---

## Результаты и экспорт

- **PostgreSQL** — постоянное хранение уникальных вакансий и журнал прогонов (`parse_runs`).
- **Экспорт из UI** — `GET /runs/{id}/export.xlsx` и `.json` (листы и метрики как в `exporters/`).
- **CLI** — при локальном `parse` пишет в `output/` (`vacancies.xlsx`, `vacancies.json`, `output/charts/`).

---

## Тесты

```powershell
pytest
```

В том числе: `tests/test_api_health.py`, `tests/test_upsert_vacancies.py`, тесты ядра extraction/AI.

---

## Архитектура

```text
[React UI] ──REST/SSE──► [FastAPI backend] ──► parse_runner
                              │                    │
                              ▼                    ├── Telethon
                         PostgreSQL               ├── extraction/ (rules)
                              ▲                    ├── ai/ (OpenRouter)
                              │                    ├── filters → dedup
                         UPSERT vacancies          └── metrics → export
```

- **`extraction/`** — быстрые эвристики (всегда).
- **`ai/`** — опционально: OpenRouter, JSON-схема, кэш, `merge_rules_and_ai`.
- **`backend/`** — API, фоновые прогоны, персистентность.

---

## Roadmap

- [x] FastAPI + PostgreSQL + React UI + Docker Compose
- [ ] Alembic-миграции, восстановление прогресса после рестарта backend
- [ ] Расширение Telethon: joinchat, rate-limit
- [ ] JWT / multi-user (сейчас — один локальный оператор, опциональный `API_KEY`)

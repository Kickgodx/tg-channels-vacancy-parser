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
| **Prometheus** | http://localhost:9090 |
| **Grafana** | http://localhost:3000 (login: `admin` / `admin`) |
| **Loki** | http://localhost:3100 (API; логи смотрятся в Grafana → Explore) |
| **Alloy** | http://localhost:12345 (UI пайплайна сбора логов и трасс) |
| **Tempo** | http://localhost:3200 (API; трассы смотрятся в Grafana → Explore → Tempo) |
| **PostgreSQL** | `localhost:5432` (user/pass/db: `tgjobs` / `tgjobs` / `tgjobs`) |

Prometheus, Grafana, стек логов (Loki + Alloy) и трассировка (Tempo) поднимаются вместе с основным стеком. Backend отдаёт метрики HTTP на `http://localhost:8000/metrics` (для scrape из Docker-сети; в проде лучше не публиковать наружу).

### Первая авторизация Telethon

В контейнере backend интерактивный ввод телефона и кода удобнее, чем в браузере:

```powershell
docker compose exec backend python -m tg_jobs_parser.cli parse --config /app/config.yaml
```

Введите номер (`+79991234567`), код из Telegram/SMS и при необходимости пароль 2FA. Файл сессии появится в `sessions/` на хосте (volume).

После этого парсинг запускайте из UI (**Парсинг** → настройте фильтры → **Запустить парсинг**).

### Переменные backend (`.env`)

```env
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
OPENROUTER_API_KEY=...          # если ai.enabled в config.yaml
HH_API_TOKEN=...                # Bearer-токен HH (admin sync); без client_credentials
HH_CONTACT_EMAIL=you@example.com  # для User-Agent HH API

# Опционально (для docker compose уже задано в compose-файле):
# DATABASE_URL=postgresql+psycopg://tgjobs:tgjobs@postgres:5432/tgjobs
# CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
# API_KEY=                        # если задан — заголовок X-API-Key для API
# JWT_SECRET=                     # подпись access-токенов; без него — эфемерный (сессии сгорают при рестарте)
# JWT_TTL_MINUTES=60              # TTL access-токена
# AUTH_REGISTRATION_OPEN=true     # false — регистрация закрыта после первого пользователя
# HTTP_LOG_BODIES=true            # тела запросов/ответов (JSON, form, text) в HTTP-логе
# HTTP_LOG_BODY_LIMIT=4096        # макс. символов тела в логе, дальше — «…[truncated, N bytes]»
# HTTP_LOG_EXCLUDE_PATHS=/metrics,/api/v1/health,/docs,/docs/oauth2-redirect,/redoc,/openapi.json,/favicon.ico
#                                  (те же пути не трассируются)
# OTEL_ENABLED=false              # OpenTelemetry-трассировка (в compose — true)
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # куда слать OTLP (в compose — http://alloy:4318)
# OTEL_TRACES_SAMPLE_RATIO=1.0    # доля сохраняемых трасс (head sampling)
# OTEL_SERVICE_NAME=tg-jobs-parser-backend
```

Первый вход в UI — через регистрацию на странице `/login`; подробнее — в
разделе [Аутентификация и per-user данные](#аутентификация-и-per-user-данные).

---

## Web UI (React)

Стек: **Vite + TypeScript + React Router + TanStack Query + Recharts**.

| Страница | Описание |
|----------|----------|
| **Вход** | `/login` — вход и регистрация; без токена остальные страницы недоступны |
| **Парсинг** | Форма парсинга (каналы, даты, специализации, формат работы, AI, пороги score), поиск каналов (DuckDuckGo) |
| **Прогон** | Прогресс (SSE), метрики, графики, блок AI (токены и стоимость input/output/total), таблица вакансий, экспорт Excel/JSON |
| **Импорт** | `/import` — вставка текста вакансии вручную (например, с hh.ru): rules + LLM-разбор и запись в общую базу; ниже — последние импортированные |
| **HH** | `/hh` — только для админа: sync вакансий с HH.ru (поиск AQA/Python), без LLM; кэш listings + skip detail |
| **Вакансии** | Все уникальные вакансии из PostgreSQL: поиск, фильтры, пресеты, пагинация, сортировка по клику на заголовок; вкладка **Аналитика** |
| **Резюме** | Список резюме пользователя: просмотр текста, создание, редактирование, удаление |

Возможности таблицы: сортировка asc/desc по дате, зарплате, валюте, формату, грейду, score, match; выбор видимых колонок; модальное окно с полным текстом, оценкой соответствия резюме и ссылками на пост/канал.

---

## Возможности

### Поиск и пресеты фильтров

Поиск по тексту — полнотекстовый (Postgres FTS): функциональный GIN-индекс
`to_tsvector('russian', cleaned_text)`, запрос через `websearch_to_tsquery`,
сортировка по `ts_rank`. Со стеммингом: «python разработчик» находит
«python-разработку». На SQLite (тесты) откатывается на `ILIKE`.

Набор фильтров сохраняется как именованный пресет (`saved_filters`, JSON с
полями `VacancyQuery`) и применяется одной кнопкой. Пресеты приватные —
привязаны к пользователю.

### Аналитика

Вкладка **Аналитика** на странице вакансий: агрегация в SQL, без выгрузки
строк, с теми же фильтрами, что и список.

- `GET /analytics/salaries` — медиана/квартили/распределение
  `detected_salary_avg_rub` по грейдам, специализациям и формату работы +
  помесячная динамика.
- `GET /analytics/sources` — качество канала: число вакансий, доля с
  зарплатой, средний `vacancy_score`, доля уникальных.
- `GET /analytics/companies` — топ компаний.

Зарплаты приводятся к рублям по курсам ЦБ (`exchange_rates`), метка
`salary_display` показывает дату курса.

### Матчинг резюме к вакансиям

Резюме хранятся текстом и управляются на вкладке **Резюме** (создание,
просмотр, редактирование, удаление); на странице «Вакансии» резюме только
выбирается в панели над таблицей. Дальше оценка идёт
**по одной вакансии**: открываете карточку → кнопка «Оценить резюме» рядом с
«Открыть пост» / «Канал» → синхронный вызов LLM. В карточке появляются
overall score, разбивка по измерениям (роль, навыки, грейд, домен, формат),
confidence оценки, пояснение и списки совпадающих / недостающих навыков
с приоритетом (critical/important/secondary) и evidence, а также сильные
стороны и риски кандидата.

- LLM отвечает только за оценку измерений и объяснение: итоговый
  `overall_score` считает backend детерминированно — взвешенная сумма
  breakdown (роль 30%, навыки 35%, грейд 20%, домен 10%, формат 5%) плюс
  penalty за пробелы по critical-требованиям (1 → cap 69, 2+ → cap 59).
- Оценка сохраняется парой `(resume_id, vacancy_id)`: повторное открытие
  карточки отдаёт её из БД, токены не тратятся. Кнопка «Переоценить резюме»
  (`force=true`) запускает LLM заново.
- Посчитанные скоры видны в колонке **Match**, по ним же работает сортировка
  (`sort_by=match_score` при выбранном резюме).
- Модель матчинга задаётся отдельно от модели парсинга: `ai.match_model`
  (+ `ai.match_reasoning_enabled` для reasoning-моделей) — качество здесь
  важнее скорости, вызовов на порядок меньше. Глубина рассуждений —
  `ai.match_reasoning_effort` (`high`, `xhigh`, …), одна на все модели:
  OpenRouter сам сводит уровень к ближайшему, который поддерживает модель.

### Сопроводительные письма

Из карточки вакансии — кнопка «Сопроводительное» рядом с «Оценить резюме»:
LLM пишет черновик отклика под конкретную вакансию по выбранному резюме
(120–200 слов, язык — по языку вакансии, без выдуманного опыта). Тон —
выбором рядом с кнопкой (нейтральный / дружелюбный / формальный). Если пара
уже оценена, в промпт подмешивается сохранённый матч (сильные стороны).

- Письмо хранится парой `(resume_id, vacancy_id)` в `cover_letters`:
  повторное открытие карточки отдаёт его из БД, токены не тратятся.
  «Перегенерировать письмо» (`force=true`) вызывает LLM заново;
  `DELETE` сбрасывает сохранённый вариант.
- Текст открывается в редактируемом поле — правки локальные, для кнопки
  «Копировать»; в БД остаётся исходный вариант LLM.
- Модель — `ai.cover_letter_model` (пусто → `match_model` → `model`),
  reasoning-флаг и уровень общие с матчингом (`ai.match_reasoning_enabled`,
  `ai.match_reasoning_effort`).

### Аутентификация и per-user данные

Своя лёгкая JWT-аутентификация: argon2-хэш пароля (`pwdlib`) + HS256
(`pyjwt`). Первый зарегистрированный аккаунт становится админом и наследует
резюме/пресеты, созданные до появления пользователей.

- Приватное (по `user_id`): резюме, пресеты фильтров, матчи, письма.
- Общее: вакансии, прогоны, аналитика, курсы ЦБ — единый корпус.
- Токен хранится в `localStorage`, уходит в `Authorization: Bearer …`;
  401 → редирект на `/login`. Для SSE (EventSource не умеет заголовки)
  токен передаётся параметром `access_token`.

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
| `POST` | `/auth/register` / `/auth/login` | Регистрация / вход → JWT access-токен |
| `GET` | `/auth/me` | Текущий пользователь |
| `GET` | `/config/defaults` | Значения по умолчанию для формы |
| `GET` | `/config/llm-models` | Каталог моделей из `llm_models.yaml` |
| `POST` | `/channels/search` | Поиск кандидатов каналов |
| `POST` | `/runs` | Запуск парсинга (фоновый поток) |
| `GET` | `/runs/{id}` | Статус и метрики прогона |
| `GET` | `/runs/{id}/events` | SSE: прогресс в реальном времени |
| `POST` | `/runs/{id}/cancel` | Отмена |
| `GET` | `/runs/{id}/vacancies` | Вакансии прогона (`sort_by`, `sort_order`) |
| `GET` | `/runs/{id}/export.xlsx` / `.json` | Скачивание отчёта |
| `GET` | `/vacancies` | Глобальный список вакансий (фильтры, сортировка, пагинация, `resume_id`) |
| `POST` | `/vacancies/import` | Импорт вакансий из текста: `{texts, source_url?}` → rules + LLM → запись в БД |
| `GET` | `/hh/status` | Статус HH sync (admin): токен задан?, TTL listings, счётчики кэша |
| `POST` | `/hh/sync` | Синхронный sync HH (admin): поиск → dedup → detail только для новых → БД |
| `GET` | `/vacancies/metrics` | Агрегаты для графиков по текущим фильтрам |
| `GET` | `/vacancies/export.xlsx` | Excel всех вакансий в БД (те же фильтры, что у списка) |
| `GET` | `/analytics/salaries` \| `/sources` \| `/companies` | Зарплатные квантили, качество каналов, топ компаний |
| `GET` `POST` `DELETE` | `/filters` | Пресеты фильтров (приватные) |
| `GET` `POST` `PUT` `DELETE` | `/resumes` | Резюме пользователя |
| `POST` `GET` | `/resumes/{id}/match/{vacancy_id}` | Оценка соответствия одной вакансии (`force=true` — пересчёт) |
| `POST` `GET` `DELETE` | `/resumes/{id}/cover-letter/{vacancy_id}` | Сопроводительное письмо (`force=true` — перегенерация, `tone`/`extra_notes` в теле) |
| `GET` | `/fx/rates`, `POST` `/fx/refresh` \| `/fx/recalculate-rub` | Курсы ЦБ и пересчёт зарплат в ₽ |
| `GET` | `/ai-cache/stats` | Число записей в кэше LLM |
| `POST` | `/ai-cache/clear` | Полная очистка кэша LLM |

Приватные разделы (`/filters`, `/resumes`, `POST /vacancies/import`) требуют JWT;
остальные — `X-API-Key`, если `API_KEY` задан.

Импортированные вручную вакансии помечаются `source_channel = "manual"`
(по нему их можно отфильтровать в списке); дедупликация — по тому же хэшу
текста, что и у постов Telegram. Пост-фильтры прогона (exclude_keywords,
форматы, локации) и порог `vacancy_score` при импорте не применяются:
при включённом AI решение «вакансия или нет» принимает LLM (`is_job_post`),
без него текст сохраняется как есть.

Вакансии с HH (`source_channel = "hh"`) появляются после admin-sync
(`/hh` или `POST /hh/sync`): поля берутся из API без LLM, дедуп мультигорода
как в MVP hhparser, TTL вакансий — общий `retention.vacancy_ttl_days`.
На вкладке `/hh` окно sync задаётся в интерфейсе: All, 7 / 14 / 30 дней или диапазон дат.
Кэш короткой выдачи (`hh_listings`) живёт `hh.listing_ttl_days` и не даёт
повторно дергать detail для уже загруженных id.

Кэш ответов LLM хранится в PostgreSQL (`ai_extraction_cache`). Параметры в `config.yaml`: `ai.cache_backend: postgres`, `ai.cache_ttl_days` (0 — без срока, иначе дни). Очистка — кнопка на странице **Парсинг** или `POST /ai-cache/clear`.

### Схема БД

Миграции инкрементальные (`alembic/versions/001…010`). При старте backend применяет `upgrade head`.

| Таблица | Назначение |
|---------|------------|
| `parse_runs` | Журнал прогонов: статус, метрики, снимок конфигурации |
| `vacancies` | Уникальные вакансии (`id` = content-hash или `sha256(hh:{id})` для HH), `archived_at` — TTL |
| `hh_listings` | Кэш короткой выдачи HH + `detail_fetched_at` (anti-DDOS detail) |
| `exchange_rates` | Курсы ЦБ по датам |
| `ai_extraction_cache` | Кэш ответов LLM при парсинге |
| `saved_filters` | Пресеты фильтров (`user_id`) |
| `resumes` | Резюме пользователя (`user_id`) |
| `vacancy_matches` | Оценки соответствия, PK `(resume_id, vacancy_id)` — он же кэш матчинга |
| `cover_letters` | Сопроводительные письма, PK `(resume_id, vacancy_id)` — он же кэш генерации |
| `users` | Аккаунты: email, argon2-хэш, `is_admin` |

**Если меняете колонки/таблицы** — добавьте новую миграцию (`alembic revision`) и обновите модели в `backend/db/models.py`; при следующем `docker compose up` она применится автоматически, данные сохранятся.

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
    api/routes/            — health, auth, config, channels, runs, vacancies,
                             analytics, filters, resumes, fx, ai-cache
    db/                    — модели, Alembic, репозиторий, кэш LLM, UPSERT
  alembic/                 — миграции SQL
    services/              — run_service, match_service, cover_letter_service,
                             auth_service, import_service, progress_bus

  frontend/                — React UI (Vite)
    src/pages/             — Login, Dashboard, Run, Vacancies
    src/components/        — ParseForm, VacancyTable, VacancyModal, ResumePanel,
                             MetricsCharts, AnalyticsPanel

  tg_jobs_parser/          — ядро парсинга (общее для API и CLI)
    parse_runner.py
    cli/                   — Typer CLI
    config/                — YAML + Pydantic
    parsers/               — Telethon
    extraction/            — rules
    ai/                    — OpenRouter, кэш, token cost
    evals/                 — scorer + live runner golden extraction
    filters/, deduplication/, analytics/, exporters/

  evals/golden/            — golden JSONL (extraction; match/cover — позже)
  evals/fixtures/          — offline ответы для pytest
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

**Версии зависимостей фиксирует `poetry.lock`.** Docker-образ backend ставит их из lock-файла: `poetry export` → `pip install --require-hashes`, затем проект `--no-deps` и `pip check`. Поэтому в контейнере те же версии, что локально и в тестах, а не самые свежие из диапазонов `pyproject.toml`. Новая зависимость: `poetry add <pkg>`, обновление: `poetry update <pkg>`. Оба коммитятся вместе с `poetry.lock`, а `poetry lock` без `--regenerate` уже залоченные версии не поднимает.

Проверка:

```powershell
poetry run python -c "import telethon; print(telethon.__version__)"
```

Используйте **`poetry run python -m tg_jobs_parser.cli …`** или активируйте `.venv`.

---

## Конфигурация

Основной файл: `config.yaml` (схема: `tg_jobs_parser/config/schema.py`).

Полное описание секций — в [config.md](config.md).

### Отбор постов (`parser`)

- **`vacancy_score_threshold`** — минимальный общий score правил.
- **`vacancy_min_substantive_score`** — минимум без бонуса за email/@ (отсекает «пустые» контакты).

### Хранение вакансий (`retention`)

- **`vacancy_ttl_days`** (по умолчанию 40) — через сколько дней вакансия
  архивируется (soft delete, `archived_at`): по `post_date`, а без неё — по
  `parsed_at`. `0` — не архивировать. Проверка идёт при старте backend, раз в
  час фоном и после каждого прогона. Архивные строки не попадают в списки и
  агрегаты, пока не передать `include_archived=true`.

### HH.ru (`hh`)

- **`enabled`** — разрешить admin sync.
- **`listing_ttl_days`** (по умолчанию 40) — TTL кэша `hh_listings` (hard delete
  по `last_seen_at`); после purge detail снова запросится.
- **`search.text` / `professional_role` / `per_page`** — параметры выдачи
  (дефолт — AQA/Python, роли 124 и 96).
- **`max_rps`** — лимит запросов к API (по умолчанию 3).
- **`db_flush_batch_size`** (по умолчанию 30) — промежуточный UPSERT+commit;
  буфер Vacancy в памяти очищается после каждого батча (меньше RAM, прогресс
  не теряется при обрыве/429).
- Секреты: `HH_API_TOKEN`, `HH_CONTACT_EMAIL` в `.env` (и в docker через `env_file`).
- Метрики/дашборд: Grafana «HH.ru sync» (`observability/grafana/.../hh.json`).

### Секция `ai` (опционально)

По умолчанию **`ai.enabled: false`**. При включении — разбор через **OpenRouter** (OpenAI-compatible SDK).

| Поле | Назначение |
|------|------------|
| `enabled` | Включить LLM |
| `mode` | `assist` / `primary` |
| `model` | Id из `llm_models.yaml` — модель парсинга вакансий |
| `match_model` | Модель матчинга резюме ↔ вакансия (обычно reasoning). Пусто — берётся `model` |
| `match_reasoning_enabled` | `true`, если `match_model` — reasoning-модель (OpenRouter `reasoning` вместо `json_object`) |
| `match_reasoning_effort` | Уровень рассуждений матчинга и писем: `max` / `xhigh` / `high` / `medium` / `low` / `minimal` / `none`. Только при `match_reasoning_enabled: true`; пусто — дефолт модели. Неподдерживаемый уровень OpenRouter сводит к ближайшему (напр. Gemini 3: `xhigh` → `high`); модели без выбора уровня его игнорируют. Рассуждения расходуют `max_completion_tokens` — на `xhigh`/`max` лимит стоит поднять |
| `cover_letter_model` | Модель генерации сопроводительных писем. Пусто — `match_model`, затем `model` |
| `fallback_models` | Запасные модели |
| `concurrency`, `requests_per_minute` | Темп и параллелизм |
| `stop_llm_when_rate_limited` | Отключить LLM при 429 до конца прогона |
| `max_completion_tokens` | Лимит выходных токенов |
| `use_cache` | Дисковый/Postgres кэш LLM |
| `prompt_version` | Должен совпадать с `EXTRACT_PROMPT_VERSION` в коде; кэш ключуется константой |

Ключ в `.env`: `OPENROUTER_API_KEY=sk-or-v1-...`

### LLM golden set и evals

Регрессии extraction ловятся без «на глаз»:

| Артефакт | Путь |
|----------|------|
| Golden dataset | `evals/golden/extraction.jsonl` |
| Offline fixtures | `evals/fixtures/extraction_outputs.json` |
| Scorer | `tg_jobs_parser/evals/score_extraction.py` |
| Версии промптов | `EXTRACT_PROMPT_VERSION` / `MATCH_PROMPT_VERSION` / `COVER_LETTER_PROMPT_VERSION` в `tg_jobs_parser/ai/prompts.py` |

```powershell
# Offline (в обычном pytest -q)
.venv\Scripts\python.exe -m pytest tests/test_evals_extraction.py -q

# Live против OpenRouter (дорого; не в CI)
.venv\Scripts\python.exe -m tg_jobs_parser.evals.run_extraction
```

Правило: изменили system/few-shot extraction → bump `EXTRACT_PROMPT_VERSION` → старый кэш не подхватится. Новый кейс — строка в JSONL + fixture для offline.

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

### Перенос данных между инстансами (дамп / импорт БД)

Экспорт всех таблиц приложения (прогоны, вакансии, курсы ЦБ, кэш LLM) в JSON:

```powershell
# локально (DATABASE_URL в .env)
poetry run tg-jobs-parser db dump -o backup/tgjobs_dump.json

# из контейнера backend
docker compose exec backend tg-jobs-parser db dump -o /app/backup/tgjobs_dump.json
```

Импорт в другой инстанс (целевой `DATABASE_URL`):

```powershell
poetry run tg-jobs-parser db import -f backup/tgjobs_dump.json
# полная замена данных в целевой БД (с подтверждением):
poetry run tg-jobs-parser db import -f backup/tgjobs_dump.json --mode replace
```

По умолчанию `--mode merge` — UPSERT по первичному ключу (существующие строки обновляются).  
`replace` — сначала очищает все четыре таблицы, затем загружает дамп.

Логи: консоль + `logs/app.log` (в Docker — только stdout, их собирает Alloy в Loki). Запись асинхронная: `QueueHandler` на root кладёт запись в очередь, stdout/файл пишет фоновый `QueueListener` — логирование не блокирует event loop. `app.log` ротируется по 10 МБ, хранится 5 частей `app.log.N.gz`. Stdout контейнеров в `docker-compose.yml` — `json-file` с ротацией (5 × 10 МБ, сжатие ротированных частей). Графики CLI: `output/charts/` (matplotlib).

---

## Результаты и экспорт

- **PostgreSQL** — постоянное хранение уникальных вакансий и журнал прогонов (`parse_runs`).
- **Экспорт из UI** — `GET /runs/{id}/export.xlsx` и `.json` (листы и метрики как в `exporters/`).
- **CLI** — при локальном `parse` пишет в `output/` (`vacancies.xlsx`, `vacancies.json`, `output/charts/`).

---

## Observability (Prometheus + Grafana + Loki)

Стек метрик HTTP API включён в `docker compose up`:

| Компонент | Назначение |
|-----------|------------|
| **`prometheus-fastapi-instrumentator`** | Счётчики запросов, статус-коды, latency на backend |
| **`GET /metrics`** | Эндпоинт Prometheus (не путать с `/api/v1/vacancies/metrics`) |
| **Prometheus** | Scrape backend каждые 15 с (`observability/prometheus.yml`) |
| **Grafana** | Дашборды **tg-jobs-parser API**, **LLM**, **Pipeline** и **HH.ru sync** (папка `tg-jobs-parser`) |
| **`llm_*` метрики** | Вызовы OpenRouter, токены, latency, кэш и пропуски при AI-enrichment |
| **Доменные метрики** | `backend/observability.py`: прогоны (`parse_runs_*`), UPSERT и TTL-архивация вакансий, ручной импорт, матчинг резюме↔вакансия (`vacancy_match_*`), письма (`cover_letter_*`), auth-события, запросы к ЦБ |
| **`db_*` gauges** | COUNT-агрегаты по таблицам на каждый scrape (вакансии, резюме, матчи, письма, прогоны, LLM-кэш, свежесть курсов ЦБ) |
| **Loki** | Хранение логов (filesystem, retention 30 дней — `observability/loki-config.yml`) |
| **Alloy** | Собирает stdout контейнеров compose-проекта через docker.sock, парсит `level`/`logger` (labels) и `request_id`/`trace_id` (structured metadata) из формата логов и шлёт в Loki; принимает OTLP-трассы от backend и отправляет в Tempo (`observability/alloy-config.alloy`) |
| **Tempo** | Хранение трасс (local, retention 7 дней — `observability/tempo-config.yml`) |

Конфигурация: `observability/prometheus.yml`, `observability/loki-config.yml`, `observability/tempo-config.yml`, `observability/alloy-config.alloy`, provisioning Grafana — `observability/grafana/provisioning/` (datasources Prometheus, Loki и Tempo с переходами лог ↔ трасса).

**HTTP-лог** (`backend/http_logging.py`, логгер `backend.http`, вместо `uvicorn.access`). Каждому запросу присваивается `request_id` (UUID; валидный входящий `X-Request-ID` переиспользуется), он возвращается в заголовке ответа `X-Request-ID` и попадает в **каждую** строку лога, записанную во время запроса (и в логи роутов/сервисов): `... | backend.http | request_id=<uuid> | ...`. На запрос — две записи:

- `REQUEST METHOD path {json}` — до обработки: клиент, заголовки, тело;
- `RESPONSE METHOD path status Nms {json}` — после ответа: статус, длительность, заголовки, тело.

Тела пишутся для JSON / form / `text/*` / XML (SSE, бинарные ответы и `Content-Disposition: attachment` — без тела). Маскируются `Authorization` (остаётся схема: `Bearer ***`), `Cookie`/`Set-Cookie`, `X-API-Key`, query-параметры и ключи тела вида `password`/`token`/`access_token`/`secret`/`api_key`. Уровень `RESPONSE`: 4xx — `WARNING`, 5xx — `ERROR`. Не логируются шумные эндпоинты из `HTTP_LOG_EXCLUDE_PATHS` (по умолчанию `/metrics`, health-check, Swagger/ReDoc/OpenAPI, favicon) и CORS preflight (`OPTIONS` с `Access-Control-Request-Method`).

Alloy кладёт `request_id` в **structured metadata** Loki (не label — уникален на запрос). В Grafana Explore:

```logql
# всё по одному запросу: REQUEST, логи обработчика, RESPONSE
{service="backend"} | request_id="3f2b8c1e-0d4a-4c7e-9a51-2b6f1d9e8a70"

# разобрать JSON HTTP-записей (поля event, status, body_*, headers_*)
{service="backend", logger="backend.http"} | regexp `(?P<http>\{.*\})$` | line_format "{{.http}}" | json
```

### Трассировка (OpenTelemetry → Tempo)

Трасса показывает, на что ушло время внутри одного запроса или прогона: дерево span'ов с длительностями. `backend/tracing.py` поднимает OpenTelemetry SDK при `OTEL_ENABLED=true`. Цепочка: backend → OTLP/HTTP → Alloy (batch) → Tempo → Grafana.

| Что трассируется | Как |
|---|---|
| Входящие HTTP | FastAPI-инструментация; span'ы `send`/`receive` отключены, пути из `HTTP_LOG_EXCLUDE_PATHS` не трассируются, `?access_token=` маскируется в атрибутах. Атрибут `request_id` — тот же, что в логах и `X-Request-ID` |
| SQL | SQLAlchemy-инструментация (текст запроса с плейсхолдерами, без значений параметров) |
| Исходящие HTTP | httpx-инструментация: OpenRouter (через openai SDK), ЦБ |
| LLM | `chat <model>`: `gen_ai.request.model`, `gen_ai.usage.input_tokens`/`output_tokens`, `llm.task` (`extract`/`match`/`cover_letter`), `llm.outcome`. Промпты и ответы в span не пишутся |
| Прогон парсинга | Отдельная трасса `parse_run` (со ссылкой на `POST /runs`): `parse.fetch_posts` (Telethon) → `parse.process_posts` → LLM-вызовы из пула потоков (контекст пробрасывается в воркеры) |
| Матчинг / письма / курсы ЦБ | `vacancy.match`, `cover_letter.generate`, `fx.sync_on_startup` |

Sampling: доля трасс — `OTEL_TRACES_SAMPLE_RATIO`. Sampler отбрасывает SQL/HTTP-span'ы без родителя: COUNT'ы `db_*`-коллектора на каждый scrape `/metrics` и фоновые запросы иначе дали бы тысячи трасс из одного span'а. Без `OTEL_ENABLED` (CLI, тесты) API OpenTelemetry работает как no-op.

**Связь логов и трасс.** Внутри span'а каждая строка лога получает `trace_id`: `... | request_id=<uuid> trace_id=<32 hex> | ...`. Alloy кладёт его в structured metadata. В Grafana:

- в Loki у строки с `trace_id` есть кнопка **«Открыть трассу»** (derived field);
- в Tempo у span'а есть **«Logs for this span»**: это логи с тем же `trace_id`;
- поиск в Tempo (TraceQL): `{ span.request_id = "<uuid>" }`, `{ name = "parse_run" }`, `{ span.llm.task = "match" && duration > 10s }`.

Из health-check, OpenAPI и `/metrics` HTTP-метрики не собираются (шум от probes и документации). LLM-метрики пишутся при парсинге с `ai.enabled: true` в том же процессе backend. При недоступной БД `db_*` gauges отдают пустой набор — scrape не падает.

---

## Тесты

```powershell
pytest
pytest -n 8     # параллельно (pytest-xdist)
```

В том числе: `tests/test_api_health.py`, `tests/test_auth.py`,
`tests/test_solo_match.py`, `tests/test_cover_letter.py`,
`tests/test_upsert_vacancies.py`, `tests/test_db_transfer.py`, тесты ядра
extraction/AI.

Тесты офлайновые: `tests/conftest.py` подставляет `CONFIG_PATH` на конфиг с
выключенным `fx` — иначе агрегаты ходят за курсами в ЦБ на каждый запрос.

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

Реализованное описано выше (аналитика, FTS и пресеты, матчинг резюме,
JWT / multi-user). Планы — в [ROADMAP.md](ROADMAP.md): трекер откликов,
экспорт в Google Sheets / Notion, CMS-админка вакансий, генерация
сопроводительных писем.

Вне списка фичей: восстановление прогресса прогона после рестарта backend,
расширение Telethon (joinchat, rate-limit).

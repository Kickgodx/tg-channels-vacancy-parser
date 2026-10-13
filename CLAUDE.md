# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Правила проекта (проверка изменений, LLM hygiene, миграции, зависимости, ветки) —
в `AGENTS.md`, он подключён ниже и здесь не дублируется.

@AGENTS.md

## Команды

Окружение — Windows, `.venv` в корне. Полный набор проверок — в `AGENTS.md`; дополнительно:

```bash
# один файл / один тест / по подстроке
.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py -q
.venv/Scripts/python.exe -m pytest tests/test_llm_jobs.py::test_name -q
.venv/Scripts/python.exe -m pytest -k "lease and recover" -q

# весь стек (postgres, redis, backend, parse-worker, llm-worker, frontend)
docker compose up --build                 # UI :5173, API :9000/docs
# observability (Prometheus/Grafana/Loki/Alloy/Tempo) — только при COMPOSE_PROFILES=observability в .env

# backend локально без Docker и без Redis
# env: PARSE_JOB_BACKEND=inprocess LLM_JOB_BACKEND=inprocess DATABASE_URL=postgresql+psycopg://tgjobs:tgjobs@localhost:5432/tgjobs
.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --port 8000

# frontend локально (VITE_API_BASE_URL=http://localhost:9000/api/v1 для compose-backend, :8000 для uvicorn)
cd frontend && npm run dev

# CLI: первая авторизация Telethon, дамп/импорт БД
.venv/Scripts/python.exe -m tg_jobs_parser.cli login --config config.yaml
.venv/Scripts/python.exe -m tg_jobs_parser.cli db dump -o backup/tgjobs_dump.json
```

У frontend тестов нет — проверка это `npm run lint` + `npm run build` (tsc).
В `addopts` pytest стоит `-m "not llm"`: live-тесты нужно включать явно (`-m llm`).

## Архитектура

Два Python-пакета в одном репозитории плюс React UI:

- **`tg_jobs_parser/`** — ядро: Telethon-fetch, правиловое извлечение, AI-enrichment,
  дедуп, FX, HH-клиент, экспорт. Не знает о FastAPI/SQLAlchemy.
- **`backend/`** — FastAPI, SQLAlchemy-репозитории, очереди RQ, воркеры, auth, observability.
- **`frontend/`** — Vite + React + TanStack Query; весь HTTP — в `src/api/client.ts`,
  типы ответов — в `src/types/api.ts` (держать в синхроне с `backend/schemas.py`).

Схемы потоков (mermaid) — `docs/architecture/`.

### Граница core ↔ backend

Зависимость односторонняя: `backend` импортирует `tg_jobs_parser`, обратно — нет
(единственное исключение — `tg_jobs_parser/cli/db.py`, дамп/импорт БД).
Там, где ядру нужна инфраструктура, используется порт: Postgres-кэш LLM —
`AIExtractionCacheBackend` в `tg_jobs_parser/ai/cache.py`, адаптер
`DbAIExtractionCache` регистрируется вызовом `register_db_ai_cache()` при старте
(FastAPI lifespan, CLI, `tests/conftest.py`). Новый entrypoint обязан сделать то же.

### Parse pipeline

`POST /runs` → `backend/services/run_service.py` → строка `parse_runs` (`queued`,
со снимком конфигурации) → RQ-очередь `parse` или поток (`PARSE_JOB_BACKEND`) →
`backend/workers/parse_tasks.run_parse_job` → `tg_jobs_parser/parse_runner.run_parse`:

Telethon fetch → `ingestion.process` (rules → опциональный AI → policy gates) →
dedup → FX в ₽ → UPSERT в `vacancies` → метрики.

- Одновременно допустим один активный прогон (409 `CONFLICT_ACTIVE_RUN`).
- Конфиг прогона = `config.yaml` + overrides из тела запроса
  (`backend/config_builder.build_config`); воркер читает **снимок** из
  `parse_runs.config_snapshot`, а не файл. Overrides — whitelist-схема
  `RunCreateRequest` (`extra="forbid"`): новое поле добавлять явно и только
  если его безопасно отдать клиенту (не `base_url`, не пути, не лимиты).
- `tg_jobs_parser/ingestion/` — общий путь для Telegram и ручного импорта
  (`POST /vacancies/import`, `MANUAL_POLICY` — без пост-фильтров и порога score).
  HH sync идёт мимо него: `hh/mapper` + `hh/dedup`, без LLM.
- `id` вакансии — content-hash очищенного текста (`utils/hashing.vacancy_content_id`),
  для HH — `sha256(hh:{id})`; UPSERT по нему и есть межпрогонный дедуп.

### Durable-джобы (parse и LLM)

Источник истины по статусу — Postgres, Redis — только транспорт (очередь RQ +
pub/sub для SSE). Оба типа джоб устроены одинаково:

- claim с lease (`lease_owner`, heartbeat) → `running`; истёкший lease или
  `queued` без живой RQ-джобы поднимает recover-цикл воркера
  (`backend/workers/recover*.py`);
- отмена прогона — флаг `cancel_requested` в БД, воркер его поллит (переживает
  рестарт API);
- прогресс прогона — колонка `parse_runs.progress`; SSE (`/runs/{id}/events`,
  `/llm-jobs/{id}/events`) читает Redis pub/sub с fallback на poll БД;
- матчинг и письма: кэш в БД → 200, иначе `llm_jobs` + 202 `{job_id}`
  (`backend/services/llm_job_service.py`, `backend/workers/llm_tasks.py`);
- мутации parse / HH sync / import принимают `Idempotency-Key`
  (`backend/services/idempotency.py`): резерв коммитится отдельной сессией до
  side-effect.

Бэкенды `inprocess` (дефолт в `Settings`, используется тестами) и `rq`
(Docker) должны оставаться взаимозаменяемыми — изменение в одном пути
проверять и во втором.

### Данные и доступ

- `backend/db/repository.py` — совместимый facade-реэкспорт; новый код
  импортирует bounded-репозитории напрямую (`parse_run_repository`,
  `vacancy_repository`, `user_content_repository`, `llm_job_repository`).
- Фильтры списка, метрик, аналитики и экспорта строятся одной функцией
  (`backend/db/vacancy_filters.py`) из `VacancyQuery` — новый фильтр добавлять там,
  иначе список и аналитика разойдутся.
- Тесты идут на SQLite, прод — Postgres: диалект-зависимые вещи (FTS →
  `ILIKE`, JSON-операторы, миграции с FK) требуют ветки под оба диалекта.
- Per-user (по `user_id`, JWT): резюме, пресеты, матчи, письма, `llm_jobs`.
  Общее: вакансии, прогоны, аналитика, курсы, AI-кэш — чтение любому
  залогиненному (`get_current_user` на роутере), мутации — `require_admin`.
  Анонимно доступны только `/health` и `/auth/register|login`. SSE получает токен
  параметром `?access_token=` (`get_sse_user` в `backend/api/deps.py`).
- Ошибки API — `AppError` / `http_error` из `backend/errors.py` с
  `detail = {code, message, retryable}`; frontend разбирает это в `ApiError`.
- Итоговый `overall_score` матчинга считает backend детерминированно из
  breakdown LLM (веса и cap за critical-пробелы) — не доверять числу из ответа модели.

### Конфигурация

- `config.yaml` (схема — `tg_jobs_parser/config/schema.py`, описание — `config.md`) —
  доменные настройки; `backend/settings.py` — env-настройки процесса (`.env`).
  `get_settings()` под `lru_cache`: в тестах после `monkeypatch.setenv` нужен
  `get_settings.cache_clear()` (и `reset_engine()` при смене `DATABASE_URL`) —
  см. фикстуру `api_client`.
- `llm_models.yaml` — каталог моделей и цен для UI и расчёта стоимости токенов.

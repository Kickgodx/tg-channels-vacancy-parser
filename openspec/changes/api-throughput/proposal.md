# Proposal

## Why

Нагрузочный тест без LLM (k6, py-spy, `EXPLAIN ANALYZE`; отчёт — в design.md) показал,
что API держит ~140 RPS на смеси запросов UI при 1.8 тыс. вакансий и ~40 RPS при
45 тыс. Поиск `q=` при росте корпуса деградирует до 4.6 RPS с p99 22 с и тянет вниз
остальные запросы. Причины — один процесс uvicorn под GIL, `ts_rank` с пересчётом
`to_tsvector` по каждому совпадению, дорогое логирование тел ответов и накладные
расходы на каждый запрос. Без исправлений каталог перестанет отвечать задолго до
того, как Postgres станет узким местом.

## What Changes

- Поиск `q=` идёт по хранимой колонке `vacancies.search_tsv` (generated, GIN).
  Релевантность упорядочивает первые 1000 совпадений по выбранной сортировке,
  остальные совпадения идут за ними в выбранной сортировке. **BREAKING** (порядок
  выдачи): очень старое релевантное совпадение больше не поднимается в начало
  выдачи, если совпадений больше 1000.
- **BREAKING** (формат журнала): HTTP-журнал пишет одну запись на запрос — после
  ответа, с данными запроса и ответа. Тело длиннее `HTTP_LOG_BODY_LIMIT` больше не
  разбирается целиком: маскируется и обрезается его начало.
- Несколько процессов API в одном контейнере: `API_WORKERS` (по умолчанию 1, в
  docker-compose 4). Пул соединений с БД и threadpool делятся между процессами.
  Очистку, синхронизацию курсов при старте и сервер `/metrics` выполняет один
  процесс под advisory lock. Метрики Prometheus собираются со всех процессов.
  Больше одного процесса требует `REDIS_URL` и `rq` у всех бэкендов задач.
- Меньше работы на запрос: зависимости без I/O не уходят в threadpool, текущий
  пользователь читается одним Core-запросом без ORM-сущности, список вакансий —
  Core-выборкой колонок, метрики и зарплатная аналитика — меньшим числом SQL
  (`GROUPING SETS`). Ответы API не меняются.
- Инструменты нагрузочного тестирования в репозитории (`scripts/loadtest/`):
  сценарии k6, размножение корпуса на копии базы, HTML-отчёт k6 web dashboard,
  метрики k6 в Prometheus (remote write) и дашборд k6 в Grafana.
- Документация: сэмплирование трасс `OTEL_TRACES_SAMPLE_RATIO` и `APP_ENV=prod`
  под нагрузкой, `API_WORKERS`, запуск нагрузочного теста.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `catalog/vacancy-search`: ранжирование выдачи с `q` — окно из 1000 совпадений.
- `platform/api-conventions`: одна запись HTTP-журнала на запрос; маскирование
  обрезанного тела.
- `platform/data-maintenance`: плановую очистку выполняет один процесс API.
- `platform/runtime-config`: число процессов API (`API_WORKERS`) и его условия.

## Impact

- Код:
  - поиск: `backend/db/models.py`, `backend/db/vacancy_filters.py`,
    `backend/db/vacancy_repository.py`, новая миграция Alembic,
    `backend/db/transfer.py` (generated-колонка не пишется);
  - запрос: `backend/api/deps.py`, все роуты с `get_current_user` /
    `require_admin` (тип `CurrentUser`), `backend/api/routes/auth.py`,
    `backend/db/vacancy_metrics.py`, `backend/db/analytics.py`;
  - журнал: `backend/http_logging.py`;
  - процессы: `backend/main.py`, `backend/settings.py`, `backend/db/session.py`,
    `backend/observability.py`, новый модуль лидера процесса;
  - `docker-compose.yml` (`API_WORKERS`, флаг remote write у Prometheus),
    `observability/grafana/provisioning/dashboards/json/` (дашборд k6).
- API: ответы без изменений, кроме порядка выдачи с `q` при >1000 совпадений.
- Данные: миграция добавляет `vacancies.search_tsv` (переписывает таблицу под
  ACCESS EXCLUSIVE — на текущем объёме доли секунды) и заменяет индекс
  `ix_vacancies_fts` на `ix_vacancies_search_tsv`.
- Эксплуатация: формат HTTP-журнала в Loki (одна строка `HTTP` вместо пары
  `REQUEST`/`RESPONSE`); `/metrics` API собирается со всех процессов.

# Tasks

## 1. Поиск по `search_tsv` с окном ранжирования

- [x] 1.1 Миграция Alembic: `vacancies.search_tsv` (generated stored), GIN `ix_vacancies_search_tsv`, удаление `ix_vacancies_fts`; `downgrade` обратный. В модели — `Computed(persisted=True)` + `deferred`. Проверка — тест миграции на `pg_empty_db` (upgrade → колонка заполнена для существующей строки, downgrade → прежний индекс); `EXPLAIN` поиска использует `ix_vacancies_search_tsv`
- [x] 1.2 Computed-колонки пропускаются при upsert (`upsert_vacancies`) и в дампе/импорте (`backend/db/transfer.py`). Проверка — `tests/test_upsert_vacancies.py`, `tests/test_vacancy_upsert_batch.py`, `tests/test_db_transfer.py` зелёные; новый тест: дамп не содержит `search_tsv`, импорт дампа проходит
- [x] 1.3 `fts_query` по колонке-документу; `_vacancy_order_by` с окном `FTS_RANK_WINDOW = 1000` (подзапрос тех же фильтров и сортировки, для `match_score` — с join), без ранжирования в списке вакансий прогона. Проверка — тесты в `tests/test_vacancy_search.py`: «Другая форма слова», «Релевантность внутри окна», «Совпадение за пределами окна» (окно уменьшено monkeypatch'ем до 3, `total` считает все); экспорт с `q` — в том же порядке, что список
- [x] 1.5 Страница при `q` и сортировке не по `post_date desc` — в два запроса (id, затем колонки и превью). Найдено итоговым замером 6.2. Проверка — тесты в `tests/test_vacancy_search.py`: сортировка по зарплате в обе стороны (пусто в конце), страницы полной сортировки без пересечений, страницы поиска в порядке ранжирования
- [x] 1.4 Документация: README (поиск и окно ранжирования). Проверка — раздел обновлён

## 2. HTTP-журнал

- [x] 2.1 Одна запись `RESPONSE … {json}` с `request` (`client`, `headers`, `body`); захват тела не больше лимита + 1 байт; разбор JSON только для тела не длиннее лимита, иначе регулярное маскирование и маскирование незакрытого значения на границе. Проверка — `tests/test_http_logging.py`: «Одна запись на запрос», «Большое тело», «Секрет в обрезанном теле», «Проверка здоровья»; прежние тесты маскирования зелёные
- [x] 2.2 README: раздел HTTP-лога (одна запись, поле `request`, LogQL). Проверка — примеры LogQL соответствуют новому формату

## 3. Меньше работы на запрос

- [x] 3.1 `verify_api_key`, `bearer_token`, `sse_credentials` — `async def`. Проверка — `tests/test_api_access.py`, `tests/test_sse_ticket.py`, `tests/test_auth.py` зелёные
- [x] 3.2 `CurrentUser` из одного Core-запроса в `get_current_user` / `authenticate_sse` / `require_admin`; роуты принимают `CurrentUser`, смена пароля и `logout-all` загружают `UserRow` явно. Проверка — `tests/test_auth.py`, `tests/test_auth_refresh.py`, `tests/test_api_access.py` зелёные; смена пароля — `test_change_password_revokes_old_tokens`; новый тест: заблокированный пользователь получает 401 на следующем запросе
- [x] 3.3 Список вакансий — Core-выборка колонок (без `raw_text`, `cleaned_text`, `search_tsv`) в обеих ветках `list_vacancies`. Проверка — `tests/test_vacancy_list_slim.py`, `tests/test_vacancy_list_index.py`, `tests/test_resume_matching.py` зелёные
- [x] 3.4 Метрики — `GROUPING SETS` (8 SQL → 3), зарплатная аналитика — грейд+формат, периоды и периоды × группы тремя запросами (10 → 5). Проверка — `tests/test_vacancy_metrics_sql.py`, `tests/test_analytics_db.py`, `tests/test_metrics_builder.py` зелёные; новый тест считает SQL-запросы метрик (событие `before_cursor_execute`)

## 4. Несколько процессов API

- [x] 4.1 `Settings.api_workers` (`API_WORKERS`, ≥1) и `check_api_workers`: при N > 1 нужны `REDIS_URL`, `JWT_SECRET` и `rq` у трёх бэкендов задач; ошибка называет переменную. Проверка — `tests/test_settings_env.py`: «По умолчанию один процесс», «Неверное значение», «Без Redis», «Задачи inprocess»; воркер RQ с `API_WORKERS=4` в окружении стартует
- [x] 4.2 Пул и threadpool: `pool_limits` делит лимиты роли `api` на N (не меньше 1+1); lifespan ставит `total_tokens` threadpool по пулу процесса. Проверка — `tests/test_db_pool.py`: «Четыре процесса» (20+20 → 5+5 на процесс), N = 1 — прежние 20+20
- [x] 4.3 Лидер (`backend/leader.py`): advisory lock на отдельном соединении; синхронная попытка в lifespan, затем попытка раз в 30 с; обязанности — стартовая и ежечасная очистка, синхронизация курсов, сервер `/metrics`, `mark_process_dead`; потеря соединения снимает лидерство. Проверка — тесты `tests/test_leader.py`: «Несколько процессов API» (два лидера на одной базе — очистку выполняет один), «Процесс-исполнитель завершился» (закрытие соединения лидера → второй берёт роль); `tests/test_db_cleanup.py` зелёный
- [x] 4.4 Multiprocess-метрики: `multiprocess_mode` у gauge'ей; при `PROMETHEUS_MULTIPROC_DIR` — registry лидера с `MultiProcessCollector` и `_DBStatsCollector`, gauge'и пула `livesum` с обновлением потоком. Проверка — `tests/test_app_metrics.py`: «Запросы в разные процессы» (два процесса пишут в общий каталог, сумма в выдаче лидера); без multiprocess — прежние тесты зелёные
- [x] 4.5 `main()`: при N > 1 — каталог метрик и `uvicorn.run("backend.main:app", workers=N)`, при N = 1 — прежний вызов. compose: `API_WORKERS: ${API_WORKERS:-4}` у `backend`. Проверка — `docker compose up --build backend`: healthcheck зелёный, в логах один «Плановая очистка»/лидер, `/metrics` на 9100 отдаёт `http_requests_total`, токен работает во всех процессах (20 запросов `/auth/me` подряд — 200)
- [x] 4.6 Документация: `config.md`/README — `API_WORKERS`, условия N > 1, деление пула, лидер; рекомендации под нагрузкой: `APP_ENV=prod` (тела не пишутся), `OTEL_TRACES_SAMPLE_RATIO=0.1`. CLAUDE.md — заметка, что `uvicorn --reload` работает одним процессом. Проверка — тексты обновлены

## 5. Нагрузочные инструменты

- [x] 5.1 `scripts/loadtest/`: `k6/catalog.js` (сценарии `EP`, VU, длительность, логин/регистрация пользователя нагрузки), `clone_db.sh` (копия `tgjobs_lt` и множитель корпуса, отказ при имени `tgjobs`), `run.sh` (k6 в сети compose, `K6_WEB_DASHBOARD_EXPORT` → `scripts/loadtest/out/report-*.html`, `PROM_RW=1` → remote write). `out/` в `.gitignore`. Проверка — `bash -n` скриптов; прогон `run.sh me 4 5s` на стенде даёт итоговую строку и HTML-отчёт
- [x] 5.2 Prometheus: флаг `--web.enable-remote-write-receiver` в compose; дашборд `observability/grafana/provisioning/dashboards/json/k6.json` (RPS, p95/p99, ошибки, VU k6 и RPS/задержки backend). Проверка — после прогона с `PROM_RW=1` в Prometheus есть `k6_http_reqs_total`, дашборд в Grafana показывает данные
- [x] 5.3 `scripts/loadtest/README.md` и ссылка из README: порядок (копия базы → стенд → прогон → отчёт), live-дашборд на `:5665`, Grafana, предупреждения (не рабочая база, лимит соединений, receiver без аутентификации). Проверка — команды из README выполняются как написаны

## 6. Итоговая проверка

- [x] 6.1 `.venv/Scripts/python.exe -m pytest -n 8 -q`, `.venv/Scripts/ruff.exe check .`, `ruff format --check .` — зелёные; `cd frontend && npm run lint && npm run build` — зелёные (типы ответов не менялись)
- [x] 6.2 Повторный нагрузочный тест новыми инструментами на корпусе ×25 (`API_WORKERS=4`): поиск и смесь UI против замеров в design.md; результаты — в отчёт пользователю
- [x] 6.3 `openspec validate api-throughput --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/api-throughput` в `master` через `--no-ff`.
- `/opsx:archive api-throughput`.

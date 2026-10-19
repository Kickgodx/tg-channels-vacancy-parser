# Tasks

## 1. Метрики приложения

- [x] 1.1 `db_jobs_lease_expired{kind}` (`parse`, `llm`, `hh_sync`, с нулями) в `_DBStatsCollector` (`backend/observability.py`): `running` с `lease_until IS NULL OR lease_until < now()`. Проверка: тест в `tests/test_app_metrics.py` — прогон и LLM-задача с истёкшим lease дают 1, с живым lease и не-`running` — 0.
- [x] 1.2 `api_overload_rejections_total` в `backend/observability.py`, инкремент в `backend/overload.py` при 503 `OVERLOADED`. Проверка: `tests/test_overload.py` — отказ увеличивает счётчик, обычный ответ и 503 HH (`UPSTREAM_UNAVAILABLE`) — нет.
- [x] 1.3 `llm_enrich_disabled_total{reason}` (`circuit_breaker`, `quota`, `init_failed`, серии с нулём при импорте) в `tg_jobs_parser/observability/llm_metrics.py`. Однократный инкремент на переходе `llm_disabled_mid_run` в `AIEnrichmentService` для `quota` и `circuit_breaker`, `init_failed` — в `parse_runner.run_parse` при ошибке создания сервиса (assist и primary). Проверка: `tests/test_ai_transient_retry.py` — квота и circuit breaker в прогоне дают +1 при многих пропущенных постах, `tests/test_llm_metrics.py` — серии с нулём в свежем процессе, `tests/test_streaming_parse.py` — падающее создание AI (assist и primary) даёт `init_failed` +1.
- [x] 1.4 README, раздел про метрики/observability: три новые метрики. Проверка: имена совпадают с кодом.

## 2. Бэкап публикует состояние

- [x] 2.1 `scripts/pg_backup.sh`: `write_metrics` (атомарная запись `$BACKUP_DIR/pg_backup.prom`), итог попытки в `.last_attempt`, обёртка над `backup_once` (успех или провал любой ветки), запись при старте цикла и после `once`. Проверка: `sh -n`. В контейнере `postgres:16-alpine` против compose-Postgres: `once` → файл с `last_attempt_success 1`. Подмена `pg_restore` на `false` → `0`, `last_verified` прежний. Рестарт → `0` сохраняется.
- [x] 2.2 README (раздел бэкапа): файл метрик и что из него берут алерты. Проверка: описание совпадает со скриптом.

## 3. Правила алертов и тесты правил

- [x] 3.1 `observability/alerts/tg-jobs.yml`: `JobLeaseExpired`, `LlmQueueGrowing`, `ParseQueueStuck`, `FxRatesStale`, `LlmDisabled`, `ApiOverloaded`, `TargetDown`, `WorkerMissing`, `ParseRunFailed`, `PgBackupStale`, `PgBackupFailed`, `PgBackupMetricsMissing` с порогами из spec, `severity` и `summary`/`description` на русском. Проверка: `promtool check rules`.
- [x] 3.2 `observability/alerts/tests/tg-jobs.test.yml`: для каждого алерта ряд, при котором он срабатывает, и, где spec даёт отрицательный сценарий, ряд, при котором молчит (короткий всплеск llm, 503 без перегрузки, `cancelled`-прогон, recover за минуту). Проверка: `promtool test rules` зелёный.
- [x] 3.3 `scripts/check_alerts.sh`: в Docker-образах из compose — `promtool check config` и `test rules`, `amtool check-config` (MSYS_NO_PATHCONV для Git Bash). Проверка: скрипт проходит локально.
- [x] 3.4 `tests/test_alert_rules.py` (офлайн): правила разбираются, у каждого алерта есть `severity` из {`critical`,`warning`} и `summary`, каждое имя метрики в `expr` объявлено в коде (REGISTRY, `describe()` сборщика БД, HTTP-метрики instrumentator, `up`, `pg_backup_*` из скрипта). Проверка: тест зелёный, а переименование метрики в правиле его роняет.

## 4. Стек observability

- [x] 4.1 `observability/alertmanager.yml` (получатель `ui-only` без каналов, группировка по `alertname`), сервис `alertmanager` (`prom/alertmanager:v0.28.1`, профиль `observability`, порт 9093 на `INFRA_BIND_ADDR`, том `alertmanager_data`) в `docker-compose.yml`; `rule_files` и `alerting` в `observability/prometheus.yml`, монтирование `observability/alerts`. Проверка: `scripts/check_alerts.sh`, `docker compose --profile observability config -q`.
- [x] 4.2 Alloy: монтирование `${BACKUP_HOST_DIR:-./backup/pg}:/backups:ro`, `prometheus.exporter.unix` (только `textfile`) → `prometheus.scrape` (`job_name = "pg-backup"`) → `prometheus.remote_write` в Prometheus. Проверка: Alloy стартует без ошибок, в Prometheus есть `pg_backup_last_verified_timestamp_seconds{job="pg-backup"}`.
- [x] 4.3 Grafana: datasource Alertmanager (`observability/grafana/provisioning/datasources/alertmanager.yml`, `implementation: prometheus`). Проверка: в Grafana → Alerting видны правила Prometheus и алерты Alertmanager.
- [x] 4.4 README: раздел «Алерты» (список, где смотреть, как подключить канал доставки, silence на праздники ЦБ), порт 9093 в таблице сервисов; `AGENTS.md` — `scripts/check_alerts.sh` в проверках при изменении правил; `docs/architecture/` — Alertmanager на схеме observability, если она там есть. Проверка: команды из README выполняются как написано.

## 5. Интеграционная проверка

- [x] 5.1 Полный прогон: `pytest -n 8 -q`, `ruff check .`, `scripts/check_alerts.sh`.
- [x] 5.2 Живой стек (`--profile observability`): в Prometheus → Alerts загружены все 12 правил без ошибок вычисления. Остановленный `hh-worker` через 5 минут даёт алерт в UI Alertmanager (Grafana читает его же), после запуска алерт гаснет. Итог: сработал `TargetDown`, а не `WorkerMissing` (DNS SD держит прежний адрес) — spec, design, README и правило поправлены. Результат — в отчёте пользователю.

## Workflow follow-up

- Обновить `zclaudereview.md`: пункт «Алертов нет» закрыт; у бэкапа остаётся только off-site копия.
- Мерж `feature/alerting` в master через `--no-ff` и архив изменения — после подтверждения пользователя.

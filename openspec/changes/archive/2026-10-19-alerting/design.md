# Design

## Context

- Prometheus (`prom/prometheus:v3.2.1`, профиль `observability`) скрейпит backend
  (`backend:9100`, статическая цель) и воркеры через DNS SD (`llm-worker:9101`,
  `parse-worker:9102`, `hh-worker:9103`); remote-write-receiver уже включён для k6.
  `rule_files` и `alerting` в `observability/prometheus.yml` нет.
- `_DBStatsCollector` (`backend/observability.py`) считает агрегаты БД при scrape только в
  процессе API, с кэшем `METRICS_DB_STATS_CACHE_SECONDS`; при `API_WORKERS` > 1 `/metrics`
  отдаёт лидер. Там уже есть `fx_latest_rate_timestamp_seconds`,
  `db_parse_runs_finished_24h{status}` и глубины очередей `llm_rq_queue_jobs{queue}`
  (`livemax`, без метки pid).
- HTTP-метрики пишет `prometheus_fastapi_instrumentator`; его middleware — самое внешнее,
  поэтому 503 из `OverloadMiddleware` в `http_requests_total` попадают, но там же и 503
  HH `UPSTREAM_UNAVAILABLE`.
- Lease у всех трёх типов задач — колонка `lease_until` (`parse_runs`, `llm_jobs`,
  `hh_sync_jobs`); recover идёт раз в `JOB_RECOVER_INTERVAL_SECONDS` (60).
- Отключение LLM в разборе проходит через `AIEnrichmentService`
  (`tg_jobs_parser/ai/enricher.py`): ветка `LlmQuotaExhausted` (квота, дневной лимит,
  суточный бюджет) и circuit breaker; обе ставят `stats.llm_disabled_mid_run`. Ошибка
  создания сервиса ловится в `parse_runner.run_parse` (в `assist` — предупреждение и
  правила, в `primary` — `LlmUnavailableError`).
- `pg-backup` — `postgres:16-alpine` с `scripts/pg_backup.sh`, без HTTP; его состояние
  сейчас видно только Docker healthcheck по файлу `.last_verified`. Alloy уже собирает
  логи и трассы и работает непривилегированным (uid 473).

## Goals / Non-Goals

**Goals:**
- Правила в git, проверяемые `promtool test rules` без запуска стека.
- Источник истины для состояния задач — БД, как в `platform/durable-jobs`, а не память
  воркера, которая пропадает вместе с ним.
- Алерты о событиях (упавший прогон, отключение LLM) держатся достаточно долго, чтобы их
  увидели в UI без доставки.

**Non-Goals:**
- Каналы доставки (Telegram, почта), inhibit-правила, routing по severity: получатель
  один, без каналов.
- Grafana-managed алерты и дашборд алертов.
- Off-site копия бэкапа (отдельный пункт ревью).
- Алерты на латентность API, стоимость LLM, FloodWait Telegram.

## Decisions

### Правила Prometheus + Alertmanager, а не Grafana alerting
Правила — YAML в `observability/alerts/`, монтируются в Prometheus. Их можно проверить
`promtool` (синтаксис и юнит-тесты на временных рядах), они не зависят от тома Grafana и
так их и просило ревью. Grafana получает datasource типа `alertmanager`
(`implementation: prometheus`) и показывает правила Prometheus через существующий
datasource. Альтернатива — provisioning алертов Grafana: формат громоздкий и не
тестируется офлайн.

Alertmanager — `prom/alertmanager:v0.28.1`, один получатель `ui-only` без конфигураций
(валидный «blackhole»), `group_by: [alertname]`, `repeat_interval: 12h`. Состояние
(silences) — в томе `alertmanager_data`. Порт 9093 — на `INFRA_BIND_ADDR`, как у
остальных служб observability.

### Истёкший lease — метрика из БД, а не heartbeat воркера
`db_jobs_lease_expired{kind}` считается в `_DBStatsCollector` тремя `COUNT` по
`status = 'running' AND (lease_until IS NULL OR lease_until < now())`. Серии отдаются и
с нулём. Альтернатива — `worker_heartbeat_timestamp_seconds` вместе с
`db_parse_runs{status="running"}`. Но серия heartbeat исчезает вместе с умершим воркером,
её нет в режиме `inprocess`, и она не ловит сломанный recover при живом воркере. `for: 5m`
при recover раз в минуту отделяет «recover не работает» от обычного истечения lease.

### Очередь llm: «не пустела и растёт»
`min_over_time(llm_rq_queue_jobs{queue="llm"}[15m]) > 0 and
delta(llm_rq_queue_jobs{queue="llm"}[15m]) > 0` с `for: 5m`. Один порог глубины либо
шумит на легальных всплесках (импорт до 50 текстов), либо ловит отставание слишком
поздно. Очередь `parse` — просто `> 0` с `for: 10m`: прогон активен один.

### События держатся окном 24 ч
Доставки нет, поэтому алерт, горящий 2 минуты, никто не увидит. `ParseRunFailed` берёт
`db_parse_runs_finished_24h{status="failed"} > 0`. Это готовая метрика из БД, она
переживает рестарт воркера. `LlmDisabled` — `increase(llm_enrich_disabled_total[24h]) > 0`.
Счётчик живёт в процессе разбора (parse-worker, llm-worker, а в `inprocess` — API), а не
в БД: так ловятся и отключение посреди многочасового прогона, и `init_failed`, который в
БД виден только текстом предупреждения. Серии всех `reason` создаются нулями при импорте
модуля метрик. Иначе первый `inc()` создал бы серию сразу со значением 1, и `increase()`
его не увидел бы.

Однократность на разбор — флаг перехода в `AIEnrichmentService` под существующим
`stats._lock`: `llm_disabled_mid_run` из `False` в `True`. `init_failed` пишет
`parse_runner` в ветке, где создание сервиса упало, до решения assist/primary. LLM-импорт
при ошибке создания падает сам (`LlmUnavailableError`) и счётчик не трогает: это видно
статусом задачи.

### Отдельный счётчик отказов перегрузки
`api_overload_rejections_total` (Counter без меток, multiprocess-совместим) растёт в
`OverloadMiddleware._reject`. Фильтр `http_requests_total{status="503"}` смешал бы
перегрузку с недоступностью HH. Доля считается к `http_requests_total` того же
`job="tg-jobs-backend"`. Стенд нагрузочного теста (`job="tg-jobs-loadtest"`) так
исключается: его перегружают намеренно.

### Процессы: `up == 0` и `absent(up)`
Остановленный контейнер ловит `TargetDown`. Docker DNS на имя остановленного контейнера
отвечает ошибкой (а не NXDOMAIN), и DNS SD Prometheus при ошибке держит прежний адрес:
цель остаётся с `up == 0`. Проверено на живом стеке: `hh-worker` остановлен, через 5 минут
сработал `TargetDown`, после старта погас. Цели нет вовсе, если имя не резолвилось с
момента старта Prometheus: Prometheus перезапущен при остановленном воркере или
`--scale 0`. Для этого — `WorkerMissing`: `absent(up{job="…"})` по каждому из трёх
воркеров. Backend — статическая цель, ему хватает `TargetDown`.

### Бэкап: textfile-метрики через Alloy
`pg_backup.sh` пишет `$BACKUP_DIR/pg_backup.prom` атомарно (временный файл без суффикса
`.prom`, затем `mv`) при старте цикла и после каждой попытки. Серии:
`pg_backup_last_verified_timestamp_seconds` (из `.last_verified`, 0 — проверенного дампа
нет), `pg_backup_interval_seconds`, `pg_backup_last_attempt_timestamp_seconds`,
`pg_backup_last_attempt_success`. Итог попытки хранится в `.last_attempt`, поэтому
переживает рестарт. Обёртка вокруг `backup_once` записывает итог любого возврата,
`locked` при занятом lock до неё не доходит.

Alloy монтирует каталог дампов только на чтение и читает его
`prometheus.exporter.unix` с единственным коллектором `textfile`. Затем
`prometheus.scrape` (`job_name = "pg-backup"`, раз в минуту) отправляет метрики в
`prometheus.remote_write` → `http://prometheus:9090/api/v1/write`.

Альтернативы:
- Loki ruler по строкам лога: провал ловится, но «давно не было бэкапа» выражается через
  `absent_over_time` и ложно срабатывает на свежем Loki. Кроме того, алерт завязался бы
  на текст лога.
- HTTP внутри `pg-backup`: в образе нет сервера, а busybox httpd — хрупкий хак.
- node_exporter: лишний сервис ради одного файла.

`PgBackupStale` повторяет порог healthcheck (1,5 × интервал) и сравнивает с
`pg_backup_interval_seconds`, а не с константой. `for: 1h` поглощает первый бэкап на
свежей установке, где `last_verified` = 0, пока идёт дамп.

### Проверка правил
- `observability/alerts/tests/*.test.yml` — юнит-тесты `promtool`: для каждого алерта
  случай «срабатывает» и, где есть, «не срабатывает».
- `scripts/check_alerts.sh` запускает в Docker `promtool check config`,
  `promtool test rules` и `amtool check-config`, в тех же образах, что в compose.
- Офлайн-тест pytest `tests/test_alert_rules.py`: YAML правил разбирается, у каждого
  алерта есть `severity` и `summary`, каждое имя метрики из `expr` объявлено в коде. Для
  проверки берутся имена из `prometheus_client.REGISTRY`, `describe()` сборщика агрегатов
  БД, HTTP-метрики instrumentator, `up` и `pg_backup_*` из `scripts/pg_backup.sh`. Так
  переименование метрики не оставит алерт молча мёртвым.

## Risks / Trade-offs

- [Атрибуты `prometheus.exporter.unix` в Alloy 1.7 (`set_collectors`, блок `textfile`)
  проверены по документации, не запуском] → в задачах есть запуск Alloy с конфигом и
  проверка серии `pg_backup_last_attempt_success` в Prometheus.
- [`db_*`-метрики (lease, failed-прогоны, курс) пропадают, если API не отвечает] → это
  ловит `TargetDown` для backend.
- [`FxRatesStale` на длинных праздниках: ЦБ не публикует курс, например, в новогодние
  каникулы] → обычные выходные покрыты порогом 3 суток (курс на субботу ЦБ публикует в
  пятницу). Для каникул остаётся silence в Alertmanager.
- [`increase()` по счётчику процесса теряет событие, если процесс упал до первого scrape
  после него (15 с)] → приемлемо: падение процесса ловят `TargetDown` / `JobLeaseExpired`.
- [Без доставки алерт видит только тот, кто открыл UI] → осознанный выбор пользователя.
  Канал добавляется в `alertmanager.yml` без изменения правил.

## Migration Plan

Миграций БД нет. После мержа — `docker compose --profile observability up -d` поднимает
`alertmanager` и перечитывает конфиги Prometheus, Alloy и Grafana (`up -d` пересоздаст
контейнеры с изменёнными volume). Существующий `.last_verified` подхватывается при старте
цикла `pg-backup`; до первой попытки серии `pg_backup_last_attempt_*` нет, и
`PgBackupFailed` молчит. Откат — удалить `rule_files`/`alerting` из `prometheus.yml` и
сервис `alertmanager`. Файл `pg_backup.prom` ничему не мешает.

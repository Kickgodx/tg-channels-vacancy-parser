# Proposal

## Why

Prometheus собирает метрики backend и воркеров, но правил алертов и Alertmanager нет:
зависший прогон, исчерпанная квота LLM, устаревший курс ЦБ или провал бэкапа видны, только
если кто-то сам откроет нужный дашборд. Ревью (раздел 7, «Алертов нет») отметило это как
главный пробел эксплуатации, а бэкап ждёт алерта на провал проверки.

## What Changes

- Правила алертов Prometheus (`observability/alerts/*.yml`) и сервис Alertmanager в профиле
  `observability`. Доставки нет: получатель без каналов, алерты видны в UI Alertmanager
  (`:9093`) и в Grafana (datasource Alertmanager). Канал (Telegram, почта) подключается
  позже одной правкой `observability/alertmanager.yml`.
- Алерты:
  - фоновая задача (прогон, LLM-задача, HH sync) в `running` с истёкшим lease;
  - очередь `llm` растёт и не разбирается; прогон висит в очереди `parse`;
  - последний курс ЦБ старше 3 суток;
  - LLM отключился при разборе (прогон, LLM-импорт): circuit breaker, квота или бюджет
    провайдера, AI не инициализировался;
  - устойчивый поток 503 `OVERLOADED`;
  - цель скрейпа недоступна или воркер пропал из DNS;
  - прогон завершился `failed`;
  - бэкап `pg-backup` устарел или последняя попытка провалилась.
- Новые метрики под алерты:
  - `db_jobs_lease_expired{kind}`: `running`-задачи с истёкшим lease, из БД при scrape;
  - `llm_enrich_disabled_total{reason}`: отключения LLM в AI enrichment;
  - `api_overload_rejections_total`: отказы 503 `OVERLOADED` (в `http_requests_total`
    503 смешан с 503 HH `UPSTREAM_UNAVAILABLE`);
  - `pg_backup_*`: `pg-backup` пишет textfile-метрики в каталог дампов, Alloy читает их и
    отправляет в Prometheus (remote write уже включён).
- Проверка правил: `promtool test rules` с тестами на каждый алерт и pytest-тест, что каждая
  метрика в правилах объявлена в коде.

## Capabilities

### New Capabilities
- `platform/alerting`: какие состояния системы поднимают алерт, по каким метрикам и где
  алерты видны; метрики, которые для этого публикуют API, воркеры и `pg-backup`.

### Modified Capabilities

## Impact

- `observability/prometheus.yml` (`rule_files`, `alerting`), новые
  `observability/alerts/`, `observability/alertmanager.yml`,
  `observability/alloy-config.alloy` (textfile + remote write), datasource Alertmanager в
  Grafana, `docker-compose.yml` (сервис `alertmanager`, том, монтирование каталога дампов в
  Alloy только на чтение).
- `backend/observability.py` (`db_jobs_lease_expired`, `api_overload_rejections_total`),
  `backend/overload.py`, `tg_jobs_parser/observability/llm_metrics.py`,
  `tg_jobs_parser/ai/enricher.py`, `tg_jobs_parser/parse_runner.py`.
- `scripts/pg_backup.sh` (файл метрик и итог последней попытки), README, `config.md` не
  затрагивается.
- API и схема БД не меняются, миграций нет. Без профиля `observability` поведение
  прежнее: `pg-backup` только дополнительно пишет файл `pg_backup.prom`.

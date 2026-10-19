# Архитектура по сервисам

Runtime-карта: Docker Compose, внешние API, observability.

```mermaid
flowchart LR
  subgraph UI
    FE["frontend<br/>:5173 · React"]
  end

  subgraph App
    BE["backend<br/>:9000→8000 · FastAPI<br/>API_WORKERS процессов, один — лидер"]
    PW["parse-worker<br/>RQ · :9102/metrics"]
    W["llm-worker<br/>RQ · :9101/metrics"]
    HW["hh-worker<br/>RQ · :9103/metrics"]
    CLI["CLI Typer<br/>login / db dump·import"]
  end

  subgraph Data
    PG[("postgres<br/>:5432")]
    RD[("redis<br/>:6379 · RQ")]
    PGB["pg-backup<br/>pg_dump + проверка"]
  end

  subgraph Obs
    PROM["prometheus<br/>:9090"]
    ALLOY["alloy<br/>:12345 · OTLP"]
    LOKI["loki<br/>:3100"]
    TEMPO["tempo<br/>:3200"]
    GRAF["grafana<br/>:3000"]
    AM["alertmanager<br/>:9093 · без доставки"]
  end

  subgraph Ext["Внешние"]
    TG["Telegram<br/>Telethon"]
    OR["OpenRouter"]
    HH["HH.ru API"]
    CBR["ЦБ FX"]
  end

  FE -->|"REST / SSE"| BE
  CLI --> PG
  CLI --> TG
  BE --> PG
  BE -->|enqueue job_id| RD
  RD --> PW
  RD --> W
  RD --> HW
  PW --> PG
  W --> PG
  HW --> PG
  PW --> TG
  PW --> OR
  W --> OR
  HW --> HH
  BE -->|"лидер"| CBR
  BE -->|"/metrics :9100 (лидер)"| PROM
  PW -->|"/metrics :9102"| PROM
  W -->|"/metrics :9101"| PROM
  HW -->|"/metrics :9103"| PROM
  BE -->|"OTLP :4318"| ALLOY
  PW -->|"OTLP"| ALLOY
  W -->|"OTLP"| ALLOY
  HW -->|"OTLP"| ALLOY
  ALLOY --> LOKI
  ALLOY --> TEMPO
  PGB --> PG
  PGB -.->|"pg_backup.prom"| ALLOY
  ALLOY -->|"remote write"| PROM
  PROM -->|"алерты"| AM
  AM --> GRAF
  PROM --> GRAF
  LOKI --> GRAF
  TEMPO --> GRAF
```

| Сервис | Порт | Роль |
|--------|------|------|
| frontend | 5173 | Web UI |
| backend | 9000→8000; 9100 (только Docker-сеть) | API, JWT, постановка джоб в очереди; `API_WORKERS` процессов (по умолчанию 4). Курсы ЦБ, плановая очистка и `/metrics` на :9100 — только у лидера |
| parse-worker | 9102 (только Docker-сеть) | RQ `SimpleWorker`: прогоны парсинга (Telethon, LLM-разбор), очередь `parse`, concurrency=1 |
| llm-worker | 9101 (только Docker-сеть) | RQ `SimpleWorker`: мэтч/письма/ручной импорт с LLM; `/metrics` без DB COUNT-collector, с `db_pool_*` |
| hh-worker | 9103 (только Docker-сеть) | RQ `SimpleWorker`: фоновый HH sync (очередь `hh`) |
| redis | 6379 | Очереди RQ `llm` / `parse` / `hh` (AOF на томе `redis_data`) + pub/sub статуса SSE |
| postgres | 5432 | Вакансии, runs, users, FX, cache, `llm_jobs` |
| pg-backup | — | `pg_dump` по расписанию с проверкой восстановления; состояние — файл `pg_backup.prom` в каталоге дампов |
| prometheus | 9090 | Scrape backend + воркеры (`dns_sd`); правила алертов (`observability/alerts/`); remote-write receiver для метрик k6 (`scripts/loadtest`) и бэкапа (Alloy) |
| alertmanager | 9093 | Сработавшие алерты Prometheus; доставки наружу нет — UI и Grafana → Alerting |
| grafana | 3000 | Дашборды / Explore (в т.ч. **DB pool**, **LLM jobs / queue**), Alerting |
| alloy | 12345 | Логи Docker + OTLP → Loki/Tempo; `pg_backup.prom` → Prometheus |
| loki | 3100 | Логи |
| tempo | 3200 | Трассы |

Параллелизм LLM: `docker compose up --scale llm-worker=N`. Локально без Redis: `LLM_JOB_BACKEND=inprocess`.

## Процессы API

- **`API_WORKERS`** — число процессов uvicorn в контейнере backend (GIL держит процесс на ~1 ядре).
  Пул соединений с Postgres делится между ними. Нескольким процессам нужны Redis (`rq`) и `JWT_SECRET`;
  локальный `uvicorn --reload` — всегда один процесс.
- **Лидер** (`backend/leader.py`) — процесс, удерживающий `pg_try_advisory_lock` на отдельном соединении.
  Только он синхронизирует курсы ЦБ при старте, раз в час чистит БД и поднимает сервер `/metrics`
  (сумма метрик всех процессов через `PROMETHEUS_MULTIPROC_DIR`). Если лидер упал, Postgres снимает
  lock вместе с сессией, и роль за ≤30 с берёт другой процесс.
- **Защита от перегрузки** (`backend/overload.py`) — у каждого процесса столько мест обработки, сколько
  соединений в пуле. Запрос, не получивший место за `API_QUEUE_TIMEOUT_SECONDS`, получает быстрый
  503 `OVERLOADED` (`Retry-After: 1`), а не висит до тайм-аута пула. `/health` идёт без очереди.

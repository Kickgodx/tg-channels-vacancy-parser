# Архитектура по сервисам

Runtime-карта: Docker Compose, внешние API, observability.

```mermaid
flowchart LR
  subgraph UI
    FE["frontend<br/>:5173 · React"]
  end

  subgraph App
    BE["backend<br/>:9000→8000 · FastAPI"]
    PW["parse-worker<br/>RQ · :9102/metrics"]
    W["llm-worker<br/>RQ · :9101/metrics"]
    HW["hh-worker<br/>RQ · :9103/metrics"]
    CLI["CLI Typer<br/>login / db dump·import"]
  end

  subgraph Data
    PG[("postgres<br/>:5432")]
    RD[("redis<br/>:6379 · RQ")]
  end

  subgraph Obs
    PROM["prometheus<br/>:9090"]
    ALLOY["alloy<br/>:12345 · OTLP"]
    LOKI["loki<br/>:3100"]
    TEMPO["tempo<br/>:3200"]
    GRAF["grafana<br/>:3000"]
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
  BE --> CBR
  BE -->|"/metrics :9100"| PROM
  PW -->|"/metrics :9102"| PROM
  W -->|"/metrics :9101"| PROM
  HW -->|"/metrics :9103"| PROM
  BE -->|"OTLP :4318"| ALLOY
  PW -->|"OTLP"| ALLOY
  W -->|"OTLP"| ALLOY
  HW -->|"OTLP"| ALLOY
  ALLOY --> LOKI
  ALLOY --> TEMPO
  PROM --> GRAF
  LOKI --> GRAF
  TEMPO --> GRAF
```

| Сервис | Порт | Роль |
|--------|------|------|
| frontend | 5173 | Web UI |
| backend | 9000→8000; 9100 (только Docker-сеть) | API, JWT, постановка джоб в очереди, курсы ЦБ; `/metrics` — на :9100 |
| parse-worker | 9102 (только Docker-сеть) | RQ `SimpleWorker`: прогоны парсинга (Telethon, LLM-разбор), очередь `parse`, concurrency=1 |
| llm-worker | 9101 (только Docker-сеть) | RQ `SimpleWorker`: мэтч/письма/ручной импорт с LLM; `/metrics` без DB COUNT-collector, с `db_pool_*` |
| hh-worker | 9103 (только Docker-сеть) | RQ `SimpleWorker`: фоновый HH sync (очередь `hh`) |
| redis | 6379 | Очереди RQ `llm` / `parse` / `hh` (AOF на томе `redis_data`) + pub/sub статуса SSE |
| postgres | 5432 | Вакансии, runs, users, FX, cache, `llm_jobs` |
| prometheus | 9090 | Scrape backend + воркеры (`dns_sd`) |
| grafana | 3000 | Дашборды / Explore (в т.ч. **DB pool**, **LLM jobs / queue**) |
| alloy | 12345 | Логи Docker + OTLP → Loki/Tempo |
| loki | 3100 | Логи |
| tempo | 3200 | Трассы |

Параллелизм LLM: `docker compose up --scale llm-worker=N`. Локально без Redis: `LLM_JOB_BACKEND=inprocess`.

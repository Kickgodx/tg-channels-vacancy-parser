# Архитектура по сервисам

Runtime-карта: Docker Compose, внешние API, observability.

```mermaid
flowchart LR
  subgraph UI
    FE["frontend<br/>:5173 · React"]
  end

  subgraph App
    BE["backend<br/>:9000→8000 · FastAPI"]
    W["llm-worker<br/>RQ · :9101/metrics"]
    CLI["CLI Typer<br/>auth / parse"]
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
    DDG["DuckDuckGo"]
  end

  FE -->|"REST / SSE"| BE
  CLI --> BE
  BE --> PG
  BE -->|enqueue job_id| RD
  RD --> W
  W --> PG
  W --> OR
  BE --> TG
  BE --> OR
  BE --> HH
  BE --> CBR
  BE --> DDG
  BE -->|"/metrics"| PROM
  W -->|"/metrics :9101"| PROM
  BE -->|"OTLP :4318"| ALLOY
  W -->|"OTLP"| ALLOY
  ALLOY --> LOKI
  ALLOY --> TEMPO
  PROM --> GRAF
  LOKI --> GRAF
  TEMPO --> GRAF
```

| Сервис | Порт | Роль |
|--------|------|------|
| frontend | 5173 | Web UI |
| backend | 9000→8000 | API, парсинг, JWT, `/metrics`, enqueue LLM jobs |
| llm-worker | 9101 (только Docker-сеть) | RQ worker: мэтч/письма; `/metrics` для Prometheus |
| redis | 6379 | Очередь RQ `llm` + pub/sub статуса SSE |
| postgres | 5432 | Вакансии, runs, users, FX, cache, `llm_jobs` |
| prometheus | 9090 | Scrape backend + llm-worker |
| grafana | 3000 | Дашборды / Explore (в т.ч. **LLM jobs / queue**) |
| alloy | 12345 | Логи Docker + OTLP → Loki/Tempo |
| loki | 3100 | Логи |
| tempo | 3200 | Трассы |

Параллелизм LLM: `docker compose up --scale llm-worker=N`. Локально без Redis: `LLM_JOB_BACKEND=inprocess`.

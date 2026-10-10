# Архитектура по сервисам

Runtime-карта: Docker Compose, внешние API, observability.

```mermaid
flowchart LR
  subgraph UI
    FE["frontend<br/>:5173 · React"]
  end

  subgraph App
    BE["backend<br/>:9000→8000 · FastAPI"]
    CLI["CLI Typer<br/>auth / parse"]
  end

  subgraph Data
    PG[("postgres<br/>:5432")]
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
  BE --> TG
  BE --> OR
  BE --> HH
  BE --> CBR
  BE --> DDG
  BE -->|"/metrics"| PROM
  BE -->|"OTLP :4318"| ALLOY
  ALLOY --> LOKI
  ALLOY --> TEMPO
  PROM --> GRAF
  LOKI --> GRAF
  TEMPO --> GRAF
```

| Сервис | Порт | Роль |
|--------|------|------|
| frontend | 5173 | Web UI |
| backend | 9000→8000 | API, парсинг, JWT, `/metrics` |
| postgres | 5432 | Вакансии, runs, users, FX, cache |
| prometheus | 9090 | Scrape метрик |
| grafana | 3000 | Дашборды / Explore |
| alloy | 12345 | Логи Docker + OTLP → Loki/Tempo |
| loki | 3100 | Логи |
| tempo | 3200 | Трассы |

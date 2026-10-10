# Архитектура по логике

Бизнес-потоки: основной parse pipeline и боковые сценарии.

## Parse pipeline (главный поток)

```mermaid
flowchart LR
  UI["1. UI Парсинг<br/>POST /runs"]
  RUN["2. Run service"]
  FETCH["3. Telethon fetch"]
  RULES["4. Rules extract"]
  FILT["5. Post filters"]
  AI["6. AI enrich opt"]
  DEDUP["7. Dedup"]
  FX["8. FX → RUB"]
  DB["9. UPSERT DB"]
  SSE["10. Metrics + SSE"]

  UI --> RUN --> FETCH --> RULES --> FILT --> AI --> DEDUP --> FX --> DB --> SSE
```

| Шаг | Модуль |
|-----|--------|
| Run | `backend.services.run_service` |
| Fetch | `parsers.telegram_telethon` / `parse_runner` |
| Rules | `extraction/*` |
| Filters | `filters.vacancy_filters` |
| AI | `ai.enricher` → OpenRouter (+ cache) |
| Dedup | `deduplication.deduplicator` |
| FX | `fx.vacancy_rub` |
| DB | `backend.db.repository` |
| SSE | `progress_bus` → RunPage |

## Боковые потоки

```mermaid
flowchart TB
  subgraph Import
    I1["Текст из UI"] --> I2["Rules ± AI"] --> I3["UPSERT manual"]
  end

  subgraph HH["HH sync admin"]
    H1["Search listings"] --> H2["Dedup + detail"] --> H3["Mapper → vacancies"]
  end

  subgraph Match
    M1["POST match"] --> M2["кэш? → 200"]
    M1 --> M3["llm_jobs 202"]
    M3 --> M4["Redis RQ"]
    M4 --> M5["llm-worker"]
    M5 --> M6["OpenRouter"]
    M6 --> M7["vacancy_matches"]
    M3 --> M8["SSE / poll GET"]
  end

  subgraph Letter["Cover letter"]
    L1["POST letter"] --> L2["кэш? / llm_jobs + RQ"]
    L2 --> L3["llm-worker → OpenRouter"]
    L3 --> L4["cover_letters"]
  end

  subgraph Analytics
    A1["Фильтры списка"] --> A2["SQL-агрегаты"] --> A3["salaries / sources / companies"]
  end
```

### Auth и данные

- **JWT** — login/register; API Bearer, SSE через `access_token`
- **Per-user** — resumes, saved_filters, matches, cover_letters, llm_jobs
- **Shared** — vacancies, parse_runs, analytics, FX, AI cache
- **Interactive LLM** — Task Queue (RQ) + Competing Consumers (`llm-worker`); Postgres = статус, Redis = очередь + pub/sub для SSE. Fallback `LLM_JOB_BACKEND=inprocess` для тестов. Scale: `--scale llm-worker=N`. Shared OpenRouter RPM across workers — follow-up (Redis token-bucket).

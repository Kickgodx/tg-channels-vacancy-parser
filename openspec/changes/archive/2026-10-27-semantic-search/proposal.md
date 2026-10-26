# Proposal

## Why

Поиск по словам не находит вакансию, где то же самое сказано другими словами, в
карточке нет «похожих», а подобрать вакансии под резюме можно только LLM-оценкой по
одной. Эмбеддинги дают всё это дёшево: вектор вакансии считается один раз, сравнение —
запрос к Postgres.

## What Changes

- pgvector: образ `docker/postgres` (`postgres:16-alpine` + pgvector 0.8.7) для `postgres`,
  `pg-backup` и тестов; миграция 042 — `vacancy_embeddings` (HNSW по косинусу) и
  `resume_embeddings`.
- `ai.embeddings` в `config.yaml`; клиент OpenRouter `/embeddings` с общим RPM-лимитом и
  повторами, без бюджета пользовательского LLM.
- Фоновая индексация у лидера API раз в `sweep_interval_seconds`: новые, изменённые и
  посчитанные другой моделью вакансии; неизменный текст — без запроса к API.
- `GET /vacancies/{id}/similar` — похожие активные вакансии (без почти того же текста, не
  больше двух от компании); `GET /vacancies/semantic-status` — покрытие.
- `search_mode=semantic` в фильтрах каталога — 300 ближайших к запросу под остальными
  фильтрами (список, аналитика, экспорт); сортировка по умолчанию — по близости.
- `sort_by=resume_fit` — по близости к эмбеддингу резюме.
- LLM-задача `match_batch` (`POST /resumes/{id}/match-batch`, до 20 вакансий).
- UI: переключатель режима поиска, «Подбор под резюме» с «Оценить топ-N», блок «Похожие».

## Capabilities

### New Capabilities

- `catalog/semantic-search` — эмбеддинги, похожие вакансии, смысловой поиск, подбор под резюме.

### Modified Capabilities

- `career/resume-matching` — пакетная оценка.

## Impact

`docker/postgres/Dockerfile`, `docker-compose.yml`, `tests/conftest.py`; миграция 042;
`tg_jobs_parser/config/schema.py`, `ai/clients/openrouter.py`; backend —
`vector_type.py`, `embedding_queries.py`, `embedding_service.py`,
`match_batch_service.py`, фильтры и сортировка каталога, роуты, лидер API; frontend.
После обновления: `docker compose up --build` — пересобирается и образ Postgres.

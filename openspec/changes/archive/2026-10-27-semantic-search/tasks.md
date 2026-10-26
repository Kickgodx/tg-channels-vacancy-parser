# Tasks

## 1. Инфраструктура

- [x] 1.1 `docker/postgres/Dockerfile` (alpine + pgvector 0.8.7), compose `postgres` и `pg-backup`, сборка образа в `tests/conftest.py`. Проверка — весь pytest на этом образе
- [x] 1.2 Миграция 042, тип `Vector`, модели. Проверка — `test_vector_literal_roundtrip`, тесты эмбеддингов на Postgres

## 2. Индексация и выборки

- [x] 2.1 `ai.embeddings`, `OpenRouterClient.embed` через общий `_request`, `embedding_service` (индексация, вектор запроса и резюме, похожие, покрытие), проход у лидера API. Проверка — `test_sweep_*`, `test_similar_*`
- [x] 2.2 `search_mode=semantic` в фильтрах, порядок по близости, `sort_by=resume_fit`. Проверка — `test_semantic_search_*`, `test_resume_fit_sort`
- [x] 2.3 `match_batch` (сервис, воркер, `/llm-jobs`, `POST /resumes/{id}/match-batch`). Проверка — `test_match_batch_scores_skips_fresh_and_reports`

## 3. UI и документация

- [x] 3.1 Переключатель поиска, «Подбор под резюме» + «Оценить топ-N», блок «Похожие» с вложенной карточкой. Проверка — `npm run lint`, `npm run build`, сквозной прогон UI на копии базы с реальными эмбеддингами
- [x] 3.2 README, config.md, AGENTS, ROADMAP

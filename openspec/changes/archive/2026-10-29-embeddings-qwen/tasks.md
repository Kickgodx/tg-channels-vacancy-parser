# Tasks

## 1. Eval

- [x] 1.1 `tg_jobs_parser/evals/embeddings` (метрики, кэш векторов, синтетические запросы, LLM-судья для поиска и «Похожих»), `scripts/build_embedding_eval.py`, ручные запросы `evals/golden/embedding_queries.jsonl`. Проверка — `tests/test_evals_embeddings.py`, прогон на данных проекта

## 2. Переход на Qwen3

- [x] 2.1 `ai.embeddings`: модель по умолчанию, `query_instruction` / `resume_instruction`, `near_duplicate_distance`; инструкции в векторе запроса и резюме. Проверка — `test_query_and_resume_use_instructions`
- [x] 2.2 Только векторы текущей модели: смысловой поиск, `resume_fit`, «Похожие». Проверка — `test_other_model_vectors_are_ignored`
- [x] 2.3 Карта: пересчёт при смене модели после полной переиндексации, резюме на карте другой модели — `resume_error`. Проверка — `test_map_due_on_model_change`
- [x] 2.4 config.md, README; выкат и переиндексация каталога

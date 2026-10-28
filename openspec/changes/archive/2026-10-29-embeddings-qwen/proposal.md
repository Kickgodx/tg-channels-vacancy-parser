# Proposal

## Why

Eval на вакансиях проекта (`python -m tg_jobs_parser.evals.embeddings`, 2370 вакансий, LLM-судья)
показал, что `openai/text-embedding-3-small` заметно уступает новым мультиязычным моделям:
nDCG@10 смыслового поиска 0.678 против 0.841 у `qwen/qwen3-embedding-8b` с инструкцией, nDCG@5
«Похожих» 0.707 против 0.873. Qwen3 при этом вдвое дешевле. Без инструкции на стороне запроса
Qwen3 на коротких запросах хуже даже 3-small, поэтому инструкция — часть перехода.

Смена модели в проде сейчас небезопасна: смысловой поиск, подбор под резюме и «Похожие»
сравнивают вектор запроса со всеми векторами таблицы, и пока каталог переиндексируется,
вектор новой модели сравнивается с векторами старой. Порог «почти тот же текст» 0,02 подобран
под 3-small.

## What Changes

- Модель по умолчанию — `qwen/qwen3-embedding-8b` (1536 измерений через `dimensions`, без миграции).
- `ai.embeddings.query_instruction` / `resume_instruction`: текст запроса и резюме отправляется
  как `Instruct: …\nQuery:…` (формат Qwen3); вакансии — без инструкции. Пусто — текст как есть.
- `ai.embeddings.near_duplicate_distance` (0,013 для Qwen3; пересчитывается eval'ом).
- Смысловой поиск, подбор под резюме, «Похожие» и резюме на карте используют только векторы
  текущей модели; вакансия с вектором другой модели — как без вектора.
- Карта, посчитанная другой моделью, пересчитывается лидером, как только каталог
  переиндексирован целиком.
- Eval моделей эмбеддингов: `tg_jobs_parser/evals/embeddings`, `scripts/build_embedding_eval.py`.

## Capabilities

### Modified Capabilities

- `catalog/semantic-search` — инструкции, порог из конфига, только векторы текущей модели.
- `catalog/market-map` — пересчёт и резюме при смене модели.

## Impact

`tg_jobs_parser/config/schema.py`, `config.md`; `backend/services/embedding_service.py`,
`backend/db/embedding_queries.py`, `backend/db/vacancy_repository.py`, `backend/schemas.py`,
`backend/api/params.py`, `backend/api/routes/vacancies.py`, `backend/services/market_map_service.py`.
Переиндексация каталога после выката — фоновой задачей лидера (или разово `sweep_vacancies`).

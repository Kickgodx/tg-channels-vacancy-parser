# Tasks

## 1. План и факты

- [x] 1.1 `MarketQueryPlan` (strict JSON), промпты плана и ответа, `market_plan` / `market_answer` с fallback. Проверка — `tests/test_ask_market.py::test_plan_schema_tolerates_null_filters_and_unknown_topics`, `test_plan_uses_strict_schema_and_letters_model`, `test_plan_prompt_mentions_history`
- [x] 1.2 Нормализация фильтров плана. Проверка — `test_normalize_filters`
- [x] 1.3 Факты по разделам и вариантам. Проверка — `test_compute_facts_topics_and_variants`

## 2. Задача и API

- [x] 2.1 `kind=ask_market`, `POST /market-chat/ask`, `ask_market_result`. Проверка — `test_ask_job_flow`, `test_unanswerable_skips_facts_and_second_call`
- [x] 2.2 Валидация, авторизация, квота. Проверка — `test_ask_validation_and_quota`

## 3. UI и документация

- [x] 3.1 Страница «Спросить базу». Проверка — `npm run lint`, `npm run build`
- [x] 3.2 README, AGENTS.md (версия промпта)

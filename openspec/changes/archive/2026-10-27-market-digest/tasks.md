# Tasks

## 1. Backend

- [x] 1.1 `DigestConfig`, таблица `market_digests` (миграция 044), факты недели. Проверка — `tests/test_market_digest.py` (`test_facts_of_week`, `test_facts_small_week_has_no_technology_moves`, `test_week_helpers`)
- [x] 1.2 Промпт `market-digest-v1`, `OpenRouterClient.market_digest`, генерация с fallback и `summary_error`, `stale`. Проверка — `test_prompt_contains_facts_and_forbids_inventing`, `test_market_digest_uses_cover_letter_model_plain_text`, `test_generate_stores_and_reuses`, `test_generate_keeps_facts_when_llm_fails`, `test_generate_without_llm_summary_and_for_empty_week`
- [x] 1.3 Задача лидера, роуты `/digests`. Проверка — `test_ensure_latest_digest`, `test_ensure_latest_digest_disabled`, `test_api_read_for_users_generate_for_admin`

## 2. UI и документация

- [x] 2.1 Страница «Обзор» (`/digest`). Проверка — `npm run lint`, `npm run build`
- [x] 2.2 README, config.md, config.yaml, AGENTS (версия промпта), ROADMAP (head миграций)

# Tasks

Baseline: код не меняется. Каждая задача — сверить сценарии требования с
перечисленными тестами (прочитать тест и убедиться, что он проверяет именно
сценарий) и прогнать их. Сценарий без теста остаётся с пометкой «теста нет».

## 1. career/resumes

- [x] 1.1 «Пользователь ведёт свои резюме»: `tests/test_resume_matching.py::test_resumes_crud`, `tests/test_auth.py::test_resumes_are_per_user`, `test_resume_update`; порядок «новые — первыми», обрезка пробелов в названии и 404 на повторное удаление через API — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.2 «Поля резюме ограничены по длине»: теста нет. Проверка — пробел записан
- [x] 1.3 «Правка текста резюме сбрасывает его оценки и письма»: `tests/test_resume_matching.py::test_update_resume_text_drops_matches_and_letters`, `test_update_resume_keeps_results_when_text_unchanged`. Проверка — тесты зелёные
- [x] 1.4 «Удаление резюме удаляет его оценки и письма»: `tests/test_cover_letter.py::test_cover_letter_removed_with_resume`; удаление оценок и LLM-задач резюме — теста нет. Проверка — тесты зелёные, пробел записан

## 2. career/resume-matching

- [x] 2.1 «Итоговую оценку считает система, а не модель»: `tests/test_resume_matching.py::test_calculate_overall_score_weights`, `tests/test_solo_match.py::test_match_single_end_to_end`. Проверка — тесты зелёные
- [x] 2.2 «Критичные пробелы ограничивают итоговую оценку»: `tests/test_resume_matching.py::test_calculate_overall_score_critical_penalty`. Проверка — тесты зелёные
- [x] 2.3 «Ответ модели нормализуется»: `tests/test_resume_matching.py::test_match_extraction_tolerates_sloppy_llm_output`, `test_match_prompt_and_parse`. Проверка — тесты зелёные
- [x] 2.4 «Битый ответ модели запрашивается повторно один раз»: `tests/test_solo_match.py::test_match_retries_once_on_invalid_json`. Проверка — тесты зелёные
- [x] 2.5 «Оценку делает модель матчинга»: `tests/test_solo_match.py::test_match_model_falls_back_to_parse_model`, `test_match_uses_match_model_and_reasoning`; обрезка до `ai.max_input_chars` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.6 «Отказ модели завершает задачу оценки ошибкой»: `tests/test_solo_match.py::test_solo_match_reports_llm_failure`; сохранность прежней оценки при отказе — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.7 «Оценка пары требует существующих резюме и вакансии»: `tests/test_solo_match.py::test_solo_match_404_and_auth`. Проверка — тесты зелёные
- [x] 2.8 «Список вакансий с резюме показывает оценку пары»: `tests/test_resume_matching.py::test_match_upsert_and_join_in_list_vacancies`, `test_vacancy_list_marks_stale_match_without_changing_score_or_order`; сортировка по возрастанию — теста нет. Проверка — тесты зелёные, пробел записан

## 3. career/cover-letters

- [x] 3.1 «Письмо пишется в выбранном тоне с пожеланиями пользователя»: `tests/test_cover_letter.py::test_cover_letter_prompt_contains_inputs_and_tone`, `test_cover_letter_end_to_end`; 422 на другой тон и длинные пожелания — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.2 «Сохранённая оценка пары — контекст письма»: `tests/test_cover_letter.py::test_cover_letter_prompt_contains_inputs_and_tone` (сборка запроса); подстановка сохранённой оценки из БД — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.3 «Письмо пишет модель писем обычным текстом»: `tests/test_cover_letter.py::test_cover_letter_model_falls_back_to_match_then_parse`, `test_cover_letter_uses_own_model_without_json_format`. Проверка — тесты зелёные
- [x] 3.4 «Ответ модели очищается, пустой запрашивается повторно»: `tests/test_cover_letter.py::test_parse_cover_letter_strips_fence`; один повтор при пустом ответе — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.5 «Отказ модели завершает задачу письма ошибкой»: `tests/test_cover_letter.py::test_cover_letter_reports_llm_failure`. Проверка — тесты зелёные
- [x] 3.6 «Письмо пары требует существующих резюме и вакансии»: `tests/test_cover_letter.py::test_cover_letter_404_and_auth`. Проверка — тесты зелёные
- [x] 3.7 «Сохранённое письмо можно удалить»: `tests/test_cover_letter.py::test_cover_letter_delete`. Проверка — тесты зелёные

## 4. platform/llm-governance

- [x] 4.1 «Суточная квота LLM-вызовов пользователя»: `tests/test_llm_quota.py::test_daily_quota_limits_non_admin_jobs`, `test_admin_is_exempt_from_daily_quota`; квота `0` и возврат активной задачи без списания — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 4.2 «Отказ по квоте не создаёт задачу и не ретраится»: `tests/test_llm_quota.py::test_daily_quota_limits_non_admin_jobs`, `test_reserve_respects_daily_limit`; параллельные запросы — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 4.3 «Суточный бюджет пользовательского LLM»: `tests/test_llm_quota.py::test_daily_budget_disables_llm_until_utc_midnight`, `test_budget_applies_to_interactive_llm_only`, `test_no_budget_by_default`. Проверка — тесты зелёные
- [x] 4.4 «Бюджет общий для всех процессов»: `tests/test_llm_quota.py::test_redis_gate_daily_budget_per_utc_day`, `test_daily_budget_is_shared_through_gate`, `test_daily_budget_counts_locally_when_gate_fails`. Проверка — тесты зелёные
- [x] 4.5 «Темп запросов к провайдеру общий для всех процессов»: `tests/test_ai_rate_limit.py::test_acquire_serializes_requests`, `tests/test_llm_quota.py::test_redis_gate_counts_per_minute_window`, `test_two_gates_share_one_budget`, `test_limiter_waits_for_global_slot`, `test_limiter_fails_open_when_gate_unavailable`, `test_limiter_stops_waiting_when_llm_disabled`, `test_no_gate_without_redis`. Проверка — тесты зелёные
- [x] 4.6 «Исчерпанный лимит провайдера отключает LLM до сброса»: `tests/test_ai_rate_limit.py::test_disable_on_remaining_zero`, `test_reenabled_after_reset_time_passes`, `test_extract_headers_from_error`; дневной лимит провайдера и повтор 429 по `ai.max_retries_on_429` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 4.7 «Модели выбираются из каталога»: `tests/test_llm_models_config.py` (все тесты файла), `tests/test_ai_fallback_models.py::test_resolved_fallback_models_dedupes_primary`; `ai.model` вне каталога, подстановка `default_fallbacks` и `LLM_MODELS_PATH` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 4.8 «Стоимость LLM считается по ценам каталога»: `tests/test_token_cost.py` (все тесты файла), `tests/test_parse_observability.py` (тесты стоимости в метриках); поля стоимости в LLM-статистике прогона и учёт токенов без тарифа — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 4.9 «Версия промпта задаётся константой кода»: `tests/test_evals_extraction.py::test_prompt_versions_defined`, `test_resolve_extract_prompt_version_warns_on_mismatch`. Проверка — тесты зелёные
- [x] 4.10 «Golden-набор извлечения проверяется без сети»: `tests/test_evals_extraction.py::test_golden_dataset_loads`, `test_golden_offline_fixtures_meet_threshold`. Проверка — тесты зелёные

## 5. ingestion/ai-extraction (правка этапа 2)

- [x] 5.1 «Транзиентные ошибки повторяются, клиентские — нет» (MODIFIED): `tests/test_ai_transient_retry.py::test_transient_errors_are_retried_with_backoff`, `test_transient_retries_are_bounded`, `test_client_error_is_not_retried`, `test_retries_can_be_disabled`, `test_rate_limit_is_detected_by_status_code_not_text`; повтор при 408, 409 и обрыве соединения — теста нет. Проверка — тесты зелёные, пробел записан

## 6. Итоговая проверка

- [x] 6.1 Прогнать все тесты из групп 1–5 (`.venv/Scripts/python.exe -m pytest -n 8 -q <файлы>`) — зелёные; при расхождении сценария с тестом поправить сценарий в spec этого изменения, а не тест
- [x] 6.2 Пробелы («теста нет») и наблюдения из design.md (Open Questions) собраны в итоговом отчёте
- [x] 6.3 `openspec validate baseline-career --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/baseline-career` в `master` через `--no-ff`.
- `/opsx:archive baseline-career`.

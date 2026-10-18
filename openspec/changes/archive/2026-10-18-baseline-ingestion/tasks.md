# Tasks

Baseline: код не меняется. Каждая задача — сверить сценарии требования с
перечисленными тестами (прочитать тест и убедиться, что он проверяет именно
сценарий) и прогнать их. Сценарий без теста остаётся с пометкой «теста нет».

## 1. ingestion/telegram-fetch

- [x] 1.1 «Окно поиска задаётся датами и ограничено по длине»: `tests/test_dates.py` (все тесты файла); обрезка длинного окна с предупреждением — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.2 «Чекпоинт канала ускоряет повторное чтение»: `tests/test_telethon_incremental.py::test_checkpoint_reads_only_newer_messages_and_keeps_coverage`, `test_checkpoint_not_covering_date_from_is_ignored`, `test_incremental_fetch_disabled_ignores_checkpoint`, `test_date_to_in_past_uses_offset_date_and_no_checkpoint`, `tests/test_channel_checkpoints.py::test_incremental_fetch_off_passes_no_checkpoints`. Проверка — тесты зелёные
- [x] 1.3 «Чекпоинт фиксируется только за сплошное чтение»: `tests/test_telethon_incremental.py::test_full_window_records_checkpoint_from_date_from`, `test_scan_limit_hit_gives_no_checkpoint`, `test_no_new_messages_keeps_checkpoint_id`, `tests/test_streaming_parse.py::test_channel_is_flushed_and_checkpointed_before_next_is_fetched`, `test_checkpoint_not_moved_when_vacancies_were_not_written`. Проверка — тесты зелёные
- [x] 1.4 «Чтение ограничено по объёму и темпу»: `tests/test_telethon_incremental.py::test_zero_interval_disables_page_pauses`, `test_page_interval_before_each_next_history_page`, `test_cancel_during_page_pause_stops_without_checkpoint`, `test_scan_limit_hit_gives_no_checkpoint`. Проверка — тесты зелёные
- [x] 1.5 «FloodWait ожидается в пределах лимита»: `tests/test_telethon_incremental.py::test_short_flood_wait_is_waited_and_resumed`, `test_long_flood_wait_returns_partial_posts_with_warning`, `tests/test_parse_observability.py::test_flood_wait_gave_up_marks_channel_partial`; не больше 3 повторов за обход — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.6 «Ошибка чтения не теряет прочитанное»: `tests/test_telethon_incremental.py::test_error_mid_scan_keeps_read_posts`, `test_error_before_any_post_propagates`, `tests/test_streaming_parse.py::test_fetch_failure_keeps_already_processed_channels`. Проверка — тесты зелёные

## 2. ingestion/rule-extraction

- [x] 2.1 «Оценка поста складывается из признаков вакансии»: `tests/test_vacancy_detector.py` (все тесты файла), `tests/test_ingestion_process.py::test_rules_from_raw_post_sets_channel_and_score`. Проверка — тесты зелёные
- [x] 2.2 «Пост прогона Telegram проходит порог оценки и пост-фильтры»: `tests/test_vacancy_filters.py::test_apply_post_filters_score_threshold`, `test_apply_post_filters_substantive_score_threshold`, `test_apply_post_filters_each_filter_can_reject`, `tests/test_ingestion_process.py::test_process_raw_post_rules_only_drops_low_score`, `test_process_raw_post_telegram_filters_exclude_keyword`, `test_manual_policy_skips_post_filters`; порог при включённом AI — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.3 «Фильтр специализаций требует пересечения»: `tests/test_vacancy_filters.py::test_specialization_empty_selection_allows_everything`, `test_specialization_requires_intersection`, `test_specialization_selected_but_nothing_detected_is_rejected`. Проверка — тесты зелёные
- [x] 2.4 «Исключающие слова ищутся без учёта регистра и «ё»»: `tests/test_vacancy_filters.py::test_exclude_keyword_substring_rejects`, `test_exclude_keyword_normalizes_case_and_yo`, `test_exclude_keyword_absent_passes`, `test_short_exclude_keyword_matches_whole_word_only`, `test_short_exclude_keyword_cyrillic_word_boundary`, `test_blank_exclude_keywords_are_ignored`, `test_apply_post_filters_uses_merged_exclude_keywords_by_default`. Проверка — тесты зелёные
- [x] 2.5 «Фильтр формата работы пропускает неизвестный формат»: `tests/test_vacancy_filters.py::test_work_format_no_restriction`, `test_work_format_allowed_list`, `test_work_format_unknown_passes_allowed_list`, `test_exclude_office_alone`, `test_exclude_office_combined_with_allowed_list`. Проверка — тесты зелёные
- [x] 2.6 «Фильтр локации пропускает неизвестную локацию»: `tests/test_vacancy_filters.py::test_location_empty_lists_allow_everything`, `test_location_country_field_match_is_case_insensitive`, `test_location_country_partial_match_both_directions`, `test_location_country_found_in_text`, `test_location_unknown_country_passes`, `test_location_unknown_city_passes`, `test_location_other_country_rejected`, `test_location_city_rules`, `test_location_country_and_city_both_must_pass`. Проверка — тесты зелёные
- [x] 2.7 «Период зарплаты нормализуется консервативно»: `tests/test_salary_period.py` (все тесты файла), `tests/test_salary_extractor.py::test_hourly_rub_per_hour_range`, `test_hourly_rub_per_hour_single`, `test_hourly_rub_slash_chas_text`, `test_hourly_usd_range_in_text`, `test_monthly_tr_range_not_hourly`, `test_monthly_k_gross_pair_not_hourly`. Проверка — тесты зелёные
- [x] 2.8 «Зарплата не путается с другими числами»: `tests/test_salary_extractor.py::test_phone_number_is_not_salary`, `test_russian_phone_not_salary`, `test_year_is_not_salary`, `test_experience_range_is_not_salary`, `test_job_url_id_is_not_salary`, `test_negotiable_salary_returns_empty`, `test_negotiable_english_returns_empty`, `test_bare_number_is_salary`, `test_bare_number_below_threshold_ignored`, `test_salary_with_keyword_anchor_no_currency`. Проверка — тесты зелёные
- [x] 2.9 «Грейд — старший из найденных»: `tests/test_grade_extractor.py` (все тесты файла). Проверка — тесты зелёные

## 3. ingestion/deduplication

- [x] 3.1 «Id вакансии из Telegram — хэш нормализованного текста»: `tests/test_vacancy_content_id.py` (все тесты файла), `tests/test_upsert_vacancies.py::test_upsert_two_distinct_texts_two_rows`. Проверка — тесты зелёные
- [x] 3.2 «Дубли внутри прогона отсеиваются»: `tests/test_deduplicator.py::test_exact_duplicate`, `test_fuzzy_duplicate_within_same_specializations`, `test_fuzzy_picks_first_group_on_equal_score`, `tests/test_parse_job_resilience.py::test_duplicates_get_sightings_whether_flushed_or_finalized` (причина `normalized_hash`). Проверка — тесты зелёные
- [x] 3.3 «Нечёткое сравнение — только внутри набора специализаций»: `tests/test_deduplicator.py::test_fuzzy_blocking_skips_other_specializations`, `test_exact_duplicate_ignores_blocking`; отключение нечёткого сравнения порогом выше 100 — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.4 «Дедуп по ходу прогона равен разовому»: `tests/test_incremental_db_flush.py::test_incremental_dedup_matches_one_shot`. Проверка — тест зелёный
- [x] 3.5 «Повторное сохранение не откатывает LLM-разбор»: `tests/test_upsert_vacancies.py::test_upsert_ai_reextraction_replaces_extracted_fields`, `test_upsert_rules_does_not_overwrite_ai_extraction`, `test_upsert_sets_updated_at`, `tests/test_vacancy_upsert_batch.py::test_existing_rows_take_new_extraction_and_metadata`, `test_manual_import_keeps_run_binding`. Проверка — тесты зелёные
- [x] 3.6 «Каждое появление поста учитывается»: `tests/test_vacancy_sightings.py::test_upsert_records_originals_and_duplicates`, `test_rereading_same_post_keeps_its_first_sighting`, `test_manual_import_records_no_sightings`, `test_purged_run_keeps_sightings`. Проверка — тесты зелёные

## 4. ingestion/ai-extraction

- [x] 4.1 «Пост с низкой оценкой не отправляется в LLM»: `tests/test_ai_enricher_mock.py::test_enrich_skips_low_vacancy_score`. Проверка — тест зелёный
- [x] 4.2 «Пост, который LLM признал не вакансией, отбрасывается»: `tests/test_ai_enricher_mock.py::test_enrich_drops_non_job`. Проверка — тест зелёный
- [x] 4.3 «Отказ LLM на посте зависит от режима»: `tests/test_ai_enricher_mock.py::test_enrich_rules_only_after_quota`, `tests/test_ai_transient_retry.py::test_primary_mode_marks_run_incomplete_when_llm_disabled`, `test_assist_mode_falls_back_to_rules_and_stays_complete`, `test_circuit_breaker_in_assist_mode_falls_back_to_rules`. Проверка — тесты зелёные
- [x] 4.4 «Circuit breaker отключает LLM после серии ошибок»: `tests/test_ai_transient_retry.py::test_circuit_breaker_stops_llm_after_consecutive_errors`, `test_circuit_breaker_resets_on_success`. Проверка — тесты зелёные
- [x] 4.5 «Транзиентные ошибки повторяются, клиентские — нет»: `tests/test_ai_transient_retry.py::test_transient_errors_are_retried_with_backoff`, `test_transient_retries_are_bounded`, `test_client_error_is_not_retried`, `test_retries_can_be_disabled`, `test_rate_limit_is_detected_by_status_code_not_text`. Проверка — тесты зелёные
- [x] 4.6 «Запасные модели пробуются по порядку»: `tests/test_ai_transient_retry.py::test_text_with_429_still_gets_fallback_model`, `tests/test_ai_fallback_models.py::test_resolved_fallback_models_dedupes_primary`, `tests/test_ai_enricher_mock.py::test_enrich_caches_fallback_answer_under_fallback_model`; «при rate limit (429) запасные модели не пробуются» — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 4.7 «В assist LLM уточняет поля правил по уверенности»: `tests/test_ai_merger.py::test_merge_ai_salary_and_specs`, `tests/test_ai_enricher_mock.py::test_enrich_merges_job`; неуверенный ответ (ниже `min_confidence`) — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 4.8 «В primary поля берутся только от LLM»: `tests/test_ai_merger.py::test_primary_grade_from_ai_not_rules`, `test_primary_unknown_grade_not_rules_fallback`, `test_primary_specs_only_from_ai_not_rules`, `test_primary_clears_rules_salary_when_ai_null`, `test_primary_does_not_apply_regex_hourly_from_v_chasti`. Проверка — тесты зелёные
- [x] 4.9 «Ответ LLM кэшируется по тексту, модели, версии промпта и режиму»: `tests/test_ai_cache_db.py::test_cache_put_get_roundtrip`, `test_cache_ttl_expires`, `tests/test_ai_enricher_mock.py::test_enrich_caches_fallback_answer_under_fallback_model`; промах кэша при смене версии промпта — теста нет. Проверка — тесты зелёные, пробел записан

## 5. catalog/vacancy-retention

- [x] 5.1 «Вакансия архивируется по сроку, а не удаляется»: `tests/test_upsert_vacancies.py::test_archive_expired_vacancies_archives_records_beyond_ttl`; `retention.vacancy_ttl_days: 0` и расписание (старт API, раз в час, конец прогона) — теста нет. Проверка — тест зелёный, пробелы записаны
- [x] 5.2 «Повторная публикация возвращает вакансию из архива»: `tests/test_upsert_vacancies.py::test_repost_advances_post_date_and_unarchives`, `test_same_or_older_post_keeps_date_and_archive`, `test_repost_fills_missing_post_date`, `test_hh_republished_vacancy_returns_from_archive`. Проверка — тесты зелёные
- [x] 5.3 «Список вакансий по умолчанию без архива»: `tests/test_upsert_vacancies.py::test_list_vacancies_hides_archived_unless_included`. Проверка — тест зелёный

## 6. Итоговая проверка

- [x] 6.1 Прогнать все тесты из групп 1–5 (`.venv/Scripts/python.exe -m pytest -n 8 -q <файлы>`) — зелёные; при расхождении сценария с тестом поправить сценарий в spec этого изменения, а не тест
- [x] 6.2 Пробелы («теста нет») и наблюдения из design.md (Open Questions) собраны в итоговом отчёте
- [x] 6.3 `openspec validate baseline-ingestion --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/baseline-ingestion` в `master` через `--no-ff`.
- `/opsx:archive baseline-ingestion`.

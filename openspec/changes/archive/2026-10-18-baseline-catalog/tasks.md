# Tasks

Baseline: код не меняется. Каждая задача — сверить сценарии требования с
перечисленными тестами (прочитать тест и убедиться, что он проверяет именно
сценарий) и прогнать их. Сценарий без теста остаётся с пометкой «теста нет».

## 1. catalog/vacancy-search

- [x] 1.1 «Список сужается фильтрами каталога»: `tests/test_vacancy_import.py::test_import_creates_vacancy_and_dedups` (фильтр канала в списке), `tests/test_analytics_db.py::test_analytics_respects_filters`, `tests/test_export_xlsx.py::test_vacancies_export_xlsx_has_salary_filter`; несколько специализаций, `@` в канале списка, граница периода и отбор по `has_salary=true` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.2 «Полнотекстовый поиск по тексту вакансии»: `tests/test_saved_filters_db.py::test_q_filter_uses_russian_fulltext_search`, `test_fts_condition_compiles_for_postgresql`; порядок по релевантности — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.3 «Сортировка списка»: `tests/test_query_validation.py::test_invalid_query_is_422` (`sort_by=raw_text`), `tests/test_export_streaming.py::test_vacancies_export_is_capped_and_marked` (по дате поста по убыванию); пустые значения в конце и сортировка по рублёвой зарплате — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.4 «Список листается до 10 000 позиций»: `tests/test_vacancy_upsert_batch.py::test_list_depth_is_capped`, `tests/test_vacancy_list_index.py::test_list_vacancies_total_past_last_page`, `tests/test_query_validation.py::test_invalid_query_is_422`. Проверка — тесты зелёные
- [x] 1.5 «Параметры каталога проверяются»: `tests/test_query_validation.py::test_invalid_query_is_422`, `test_valid_query_passes`. Проверка — тесты зелёные
- [x] 1.6 «Список отдаёт превью, карточка — полный текст»: `tests/test_vacancy_list_slim.py::test_list_vacancies_returns_preview_without_full_texts`, `test_get_vacancy_returns_full_texts`, `test_get_vacancy_not_found`. Проверка — тесты зелёные

## 2. catalog/analytics

- [x] 2.1 «Агрегаты считаются по фильтрам каталога»: `tests/test_analytics_db.py::test_analytics_respects_filters`, `test_salary_analytics_include_archived_active_and_archived`. Проверка — тесты зелёные
- [x] 2.2 «Зарплатные агрегаты строятся по рублёвой сумме»: `tests/test_fx_metrics.py::test_metrics_ignore_native_salary_without_stored_rub`, `tests/test_vacancy_metrics_sql.py::test_salary_metrics_sql_uses_stored_rub_without_fx_subquery`. Проверка — тесты зелёные
- [x] 2.3 «Квантили зарплат по группам»: `tests/test_analytics_db.py::test_salary_analytics_quantiles`; 15 специализаций и порядок групп — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.4 «Динамика и распределение зарплат»: `tests/test_analytics_db.py::test_salary_analytics_quantiles` (диапазоны, месяцы), `test_salary_analytics_yearly_and_period_groups`. Проверка — тесты зелёные
- [x] 2.5 «Метрики дашборда»: `tests/test_vacancy_metrics_sql.py::test_daily_buckets_keep_latest_days`, `test_postgresql_lateral_unnest_is_correlated`, `tests/test_fx_metrics.py::test_metrics_avg_salary_in_rub`, `test_metrics_ignore_native_salary_without_stored_rub`; ограничения 50 и 25 специализаций — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.6 «Качество каналов»: `tests/test_analytics_db.py::test_source_quality_counts_duplicates`, `tests/test_vacancy_sightings.py::test_source_quality_counts_sightings`, `test_source_quality_filters_apply_to_sightings`. Проверка — тесты зелёные
- [x] 2.7 «Топ работодателей»: `tests/test_analytics_db.py::test_top_companies_ordered_by_count`, `tests/test_query_validation.py::test_invalid_query_is_422` (`limit=0`), `test_valid_query_passes` (`limit=100`). Проверка — тесты зелёные
- [x] 2.8 «Агрегаты кэшируются ненадолго»: `tests/test_read_caches.py::test_aggregate_cache_hits_and_keys_by_filters`, `test_vacancy_upsert_invalidates_aggregates`, `test_zero_ttl_disables_cache`, `test_cache_is_bounded`; сброс кэша пересчётом рублёвых сумм — теста нет. Проверка — тесты зелёные, пробел записан

## 3. catalog/salary-fx

- [x] 3.1 «Зарплата приводится к одному числу в рублях в месяц»: `tests/test_fx_convert.py::test_salary_amount_to_rub_usd`, `test_rur_equals_rub`, `test_rub_hourly_monthly_conversion`, `test_year_suffix_divides_by_12`, `test_rub_per_hour_to_monthly_rub`, `test_dollar_sign_equals_usd`, `test_salary_amount_to_rub_usdt_uses_usd_rate`, `test_effective_salary_amount_range`. Проверка — тесты зелёные
- [x] 3.2 «Курс — последний сохранённый»: `tests/test_fx_backfill.py::test_ensure_latest_rates_prefers_db_without_network`, `test_backfill_db_rates_only_uses_latest_in_db`; запись при недоступном ЦБ и `fx.enabled: false` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.3 «Курсы подгружаются при старте API»: теста нет. Проверка — пробел записан
- [x] 3.4 «Администратор обновляет курсы»: `tests/test_api_access.py::test_mutations_forbidden_for_non_admin` (только доступ); сам `POST /fx/refresh` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.5 «Пересчёт рублёвых сумм без запроса к ЦБ»: `tests/test_fx_backfill.py::test_backfill_db_rates_only_uses_latest_in_db`, `test_backfill_converts_annual_jpy_to_monthly`; 400 при пустых курсах — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.6 «Курсы и их дата видны пользователю»: `tests/test_fx_metrics.py::test_prepare_fx_uses_latest_rates_from_db_without_network`; содержимое `GET /fx/rates` — теста нет. Проверка — тесты зелёные, пробел записан

## 4. catalog/vacancy-export

- [x] 4.1 «Экспорт каталога следует фильтрам и сортировке списка»: `tests/test_export_streaming.py::test_vacancies_export_is_capped_and_marked`, `test_export_under_cap_has_no_truncation_mark`, `tests/test_export_xlsx.py::test_vacancies_export_xlsx_has_salary_filter`. Проверка — тесты зелёные
- [x] 4.2 «Состав файла экспорта»: `tests/test_export_streaming.py::test_vacancies_export_is_capped_and_marked`, `tests/test_export_xlsx.py::test_vacancies_export_xlsx`, `test_run_export_xlsx`. Проверка — тесты зелёные
- [x] 4.3 «Экспорт собирается потоком»: `tests/test_export_streaming.py::test_export_iterator_reads_once_and_keeps_session_light`. Проверка — тесты зелёные

## 5. catalog/saved-filters

- [x] 5.1 «Пользователь ведёт свои пресеты фильтров»: `tests/test_saved_filters_db.py::test_saved_filters_crud`, `tests/test_auth.py::test_saved_filters_are_per_user`. Проверка — тесты зелёные
- [x] 5.2 «Параметры пресета — параметры запроса списка»: теста нет. Проверка — пробел записан

## 6. sources/manual-import

- [x] 6.1 «Запрос импорта ограничен»: `tests/test_query_validation.py::test_body_text_limits`, `tests/test_vacancy_import.py::test_import_batch_and_empty`; больше 50 текстов и длинный `source_url` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 6.2 «Импорт без LLM выполняется сразу»: `tests/test_vacancy_import.py::test_import_creates_vacancy_and_dedups`, `test_import_with_ai_runs_as_llm_job`, `test_import_with_ai_is_sync_when_every_text_exists`. Проверка — тесты зелёные
- [x] 6.3 «Итог импорта по каждому тексту»: `tests/test_vacancy_import.py::test_import_batch_and_empty`, `test_import_creates_vacancy_and_dedups`, `test_import_with_ai_runs_as_llm_job`; один текст дважды в запросе — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 6.4 «Импортированная вакансия — из канала `manual`»: `tests/test_vacancy_import.py::test_import_creates_vacancy_and_dedups`, `tests/test_vacancy_sightings.py::test_manual_import_records_no_sightings`, `tests/test_parse_run_vacancies.py::test_manual_import_adds_no_links`; дата поста — момент импорта — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 6.5 «Импорт не применяет фильтры прогона»: `tests/test_vacancy_import.py::test_import_rules_mode_saves_any_text`, `test_import_with_ai_runs_as_llm_job`. Проверка — тесты зелёные
- [x] 6.6 «Сбой LLM на тексте отражается в итоге текста»: теста нет. Проверка — пробел записан
- [x] 6.7 «Квота импорта списывается по текстам»: `tests/test_vacancy_import.py::test_import_falls_back_to_rules_when_daily_quota_ends`; текст, уже имеющийся в базе, без списания — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 6.8 «Повтор импорта продолжает с необработанного текста»: `tests/test_vacancy_import.py::test_import_job_retry_skips_already_processed_texts`, `test_import_job_stops_when_lease_is_lost`, `test_import_job_rq_timeout_grows_with_texts`. Проверка — тесты зелёные

## 7. sources/hh-sync

- [x] 7.1 «Sync запускается с окном поиска»: `tests/test_hh_sync_window.py` (все тесты файла), `tests/test_hh_api.py::test_hh_sync_503_without_token`, `test_hh_sync_range_422_without_dates`, `test_hh_sync_passes_period_for_7d`. Проверка — тесты зелёные
- [x] 7.2 «Поиск в HH ограничен по темпу и глубине»: `tests/test_hh_api.py::test_hh_sync_passes_period_for_7d` (параметры поиска); темп, повтор 429 и глубина 2000 — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 7.3 «Мультигородные копии — дубли одной вакансии»: `tests/test_hh_dedup.py::test_same_vacancy_in_other_city_has_same_key`, `test_dedupe_keeps_first_and_records_copies`, `tests/test_hh_review_fixes.py::test_stabilize_prefers_detail_fetched_canonical`, `tests/test_hh_mapper.py::test_short_to_vacancy_duplicate_flags`. Проверка — тесты зелёные
- [x] 7.4 «Детали вакансии скачиваются один раз»: `tests/test_hh_api.py::test_hh_sync_skip_detail_on_second_run`, `tests/test_hh_review_fixes.py::test_content_hash_change_resets_detail_fetched_at`, `test_duplicate_does_not_overwrite_existing_detail_vacancy`; ошибка HH при деталях — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 7.5 «Вакансия HH сохраняется без LLM»: `tests/test_hh_mapper.py::test_detail_to_vacancy_fields`, `test_html_to_text_strips_tags`, `test_map_work_format_priority`, `test_clean_hh_url_strips_tech_source`, `test_detail_to_vacancy_developer_role_dual_tag`. Проверка — тесты зелёные
- [x] 7.6 «Грейд вакансии HH — по требуемому опыту»: `tests/test_hh_mapper.py::test_grade_from_experience_ids`, `test_grade_from_experience_name_backfill`, `test_detail_to_vacancy_all_experience_grades`, `test_short_to_vacancy_experience_grade`, `tests/test_hh_grades_backfill.py::test_backfill_hh_grades_from_experience_name`. Проверка — тесты зелёные
- [x] 7.7 «Вакансии HH записываются пачками»: `tests/test_hh_batch_flush.py::test_hh_sync_flushes_in_batches`, `tests/test_hh_review_fixes.py::test_mid_group_flush_when_duplicates_exceed_batch`. Проверка — тесты зелёные
- [x] 7.8 «Кэш выдачи HH очищается по сроку»: `tests/test_hh_listings_ttl.py::test_purge_expired_listings`; очистка при старте и раз в час — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 7.9 «Итог и ошибка sync»: `tests/test_hh_api.py::test_hh_sync_api_error_fails_job`, `test_hh_sync_skip_detail_on_second_run`, `tests/test_hh_sync_jobs.py::test_unexpected_error_fails_job_with_short_message`, `test_job_records_result_and_progress`. Проверка — тесты зелёные
- [x] 7.10 «Состояние HH доступно администратору»: `tests/test_hh_api.py::test_hh_status_requires_admin`, `test_hh_sync_idempotency_key_returns_same_job` (история); содержимое статуса и порядок истории — теста нет. Проверка — тесты зелёные, пробел записан

## 8. Итоговая проверка

- [x] 8.1 Прогнать все тесты из групп 1–7 (`.venv/Scripts/python.exe -m pytest -n 8 -q <файлы>`) — зелёные; при расхождении сценария с тестом поправить сценарий в spec этого изменения, а не тест
- [x] 8.2 Пробелы («теста нет») и наблюдения из design.md (Open Questions) собраны в итоговом отчёте
- [x] 8.3 `openspec validate baseline-catalog --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/baseline-catalog` в `master` через `--no-ff`.
- `/opsx:archive baseline-catalog`.

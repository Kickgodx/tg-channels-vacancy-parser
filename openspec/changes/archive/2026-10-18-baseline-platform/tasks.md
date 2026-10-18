# Tasks

Baseline: код не меняется. Каждая задача — сверить сценарии требования с
перечисленными тестами (прочитать тест и убедиться, что он проверяет именно
сценарий) и прогнать их. Сценарий без теста остаётся с пометкой «теста нет».

## 1. platform/api-conventions

- [x] 1.1 «Ошибки API в едином формате»: `tests/test_api_errors.py::test_conflict_active_run_detail_shape`, `test_http_404_uses_typed_detail`, `tests/test_api_access.py::test_reads_require_jwt`, `test_mutations_forbidden_for_non_admin`. Проверка — тесты зелёные
- [x] 1.2 «Ошибка валидации запроса — 422 с полем»: `tests/test_query_validation.py::test_invalid_query_is_422`; формат сообщения `<поле>: <причина>` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.3 «Непредвиденная ошибка не раскрывает подробностей»: `tests/test_http_logging.py::test_unhandled_error_logged_as_500`; тело ответа `INTERNAL` с `retryable: true` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.4 «У каждого запроса есть id»: `tests/test_http_logging.py::test_incoming_request_id_reused_only_if_uuid`, `test_request_and_response_are_separate_records_with_same_id`, `test_route_logs_carry_request_id_prefix`, `tests/test_logger.py::test_request_id_prefix_survives_queue`. Проверка — тесты зелёные
- [x] 1.5 «HTTP-журнал не содержит секретов»: `tests/test_http_logging.py::test_auth_bodies_and_headers_are_redacted`, `test_nested_json_redaction`, `test_form_body_redaction`, `test_truncated_json_falls_back_to_regex_redaction`. Проверка — тесты зелёные
- [x] 1.6 «Что попадает в HTTP-журнал»: `tests/test_http_logging.py::test_request_and_response_are_separate_records_with_same_id`, `test_noisy_endpoints_and_preflight_are_not_logged`, `test_non_text_sse_and_attachments_have_no_body`, `test_large_body_is_truncated`; журнал без тел при `HTTP_LOG_BODIES=false` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.7 «Браузерный доступ только с разрешённых источников»: теста нет. Проверка — пробел записан

## 2. platform/runtime-config

- [x] 2.1 «Конфигурация — config.yaml и окружение процесса»: `tests/test_read_caches.py::test_load_config_is_cached_until_file_changes`, `test_load_config_returns_independent_copies`; подхват правки `llm_models.yaml` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.2 «Ошибка в config.yaml останавливает операцию с понятной причиной»: `tests/test_read_caches.py::test_load_config_errors_are_not_cached`; 500 у `/config` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.3 «Режим APP_ENV меняет только умолчания»: `tests/test_settings_env.py::test_dev_is_default_and_keeps_local_conveniences`, `test_prod_closes_defaults`, `test_explicit_values_win_in_prod`, `test_explicit_values_win_in_dev`, `test_app_env_aliases`, `test_unknown_app_env_is_rejected`, `tests/test_auth_refresh.py::test_cookie_secure_in_prod_settings`. Проверка — тесты зелёные
- [x] 2.4 «dev не запускается на внешнем адресе»: `tests/test_settings_env.py::test_dev_on_loopback_starts`, `test_dev_on_public_addr_refuses_to_start`, `test_dev_on_public_addr_allowed_explicitly`, `test_prod_on_public_addr_starts`. Проверка — тесты зелёные
- [x] 2.5 «База данных — только PostgreSQL»: `tests/test_settings_env.py::test_database_url_defaults_to_compose_postgres_password`, `test_database_url_must_be_postgres`. Проверка — тесты зелёные
- [x] 2.6 «Ошибка настроек не раскрывает значения»: `tests/test_settings_env.py::test_startup_error_does_not_echo_env_values`. Проверка — тесты зелёные
- [x] 2.7 «Конфигурация для UI»: `tests/test_api_access.py::test_non_admin_can_read` (только доступ); содержимое `/config` и `/config/defaults` — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.8 «Проверка здоровья»: `tests/test_api_health.py::test_health`; `degraded` при недоступной базе — теста нет. Проверка — тесты зелёные, пробел записан

## 3. platform/data-maintenance

- [x] 3.1 «Схема базы обновляется при старте»: `tests/test_db_pool.py::test_migration_lock_wraps_upgrade_on_postgres`, `test_migration_lock_released_on_failure`; остановка старта при ошибке миграции — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.2 «Плановая очистка при старте и раз в час»: теста нет. Проверка — пробел записан
- [x] 3.3 «Завершённые задачи удаляются по сроку хранения»: `tests/test_db_cleanup.py::test_purges_only_expired_and_finished_rows`, `test_zero_ttl_keeps_jobs_and_runs`, `test_purge_old_hh_sync_jobs_keeps_active_and_recent`. Проверка — тесты зелёные
- [x] 3.4 «Ненужные служебные записи удаляются»: `tests/test_db_cleanup.py::test_purges_only_expired_and_finished_rows`, `tests/test_auth_refresh.py::test_cleanup_purges_expired_and_revoked_keeps_live`; сроки ключей идемпотентности 24 часа и 15 минут — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.5 «Курсы хранятся ограниченно, последние остаются»: `tests/test_db_cleanup.py::test_purges_old_exchange_rates`, `test_exchange_rates_keep_latest_date_and_startup_sync_window`, `test_exchange_rates_zero_ttl_keeps_all`. Проверка — тесты зелёные
- [x] 3.6 «Очистка идёт пачками и не падает целиком»: `tests/test_db_cleanup.py::test_purge_runs_in_batches`, `test_failing_table_does_not_stop_others`; потолок 50 пачек за проход — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 3.7 «Дамп и импорт общего корпуса»: `tests/test_db_transfer.py::test_dump_import_roundtrip_merge`, `test_import_replace_overwrites`, `test_load_dump_rejects_unknown_version`, `tests/test_parse_run_vacancies.py::test_dump_roundtrip_keeps_links_and_old_dump_is_backfilled`, `tests/test_vacancy_sightings.py::test_dump_roundtrip_keeps_sightings_and_old_dump_is_backfilled`. Проверка — тесты зелёные

## 4. Итоговая проверка

- [x] 4.1 Прогнать все тесты из групп 1–3 (`.venv/Scripts/python.exe -m pytest -n 8 -q <файлы>`) — зелёные; при расхождении сценария с тестом поправить сценарий в spec этого изменения, а не тест
- [x] 4.2 Пробелы («теста нет») и наблюдения из design.md (Open Questions) собраны в итоговом отчёте
- [x] 4.3 `openspec validate baseline-platform --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/baseline-platform` в `master` через `--no-ff`.
- `/opsx:archive baseline-platform`.

# Tasks

Baseline: код не меняется. Каждая задача — сверить сценарии требования с
перечисленными тестами (прочитать тест и убедиться, что он проверяет именно
сценарий) и прогнать их. Сценарий без теста остаётся с пометкой «теста нет»;
добавление теста — вне этого изменения.

## 1. platform/durable-jobs

- [x] 1.1 «Статус задачи хранится в Postgres»: постановка — `tests/test_parse_jobs.py::test_start_parse_run_inprocess_completes`; сбой постановки — `tests/test_parse_jobs.py::test_enqueue_failure_marks_run_failed`, `tests/test_hh_sync_jobs.py::test_enqueue_failure_marks_job_failed` (для LLM-задачи — теста нет). Проверка — тесты зелёные и проверяют 202/`queued` и `failed` после сбоя
- [x] 1.2 «Задачу выполняет один воркер под lease»: `tests/test_parse_jobs.py::test_claim_parse_run_only_once`, `tests/test_llm_rq.py::test_claim_llm_job_atomic_one_winner`, `tests/test_job_attempts.py::test_claim_increments_attempts`. Проверка — тесты зелёные
- [x] 1.3 «Итог пишет только владелец lease»: `tests/test_lease_recover.py::test_finalize_after_reclaim_is_noop`, `tests/test_lease_recover.py::test_llm_set_status_after_reclaim_is_noop`, `tests/test_hh_sync_jobs.py::test_job_stops_without_finalizing_when_lease_is_lost`. Проверка — тесты зелёные
- [x] 1.4 «Зависшая задача восстанавливается»: упал — `tests/test_lease_recover.py::test_expired_lease_running_is_requeued`, `tests/test_hh_sync_jobs.py::test_recover_requeues_running_job_with_expired_lease`; lease действует — `tests/test_lease_recover.py::test_live_lease_running_not_recovered`, `tests/test_hh_sync_jobs.py::test_recover_leaves_live_lease_alone`; живая RQ-задача — `tests/test_parse_jobs.py::test_recover_skips_live_rq_job`, `tests/test_llm_rq.py::test_recover_skips_live_rq_job`. Проверка — тесты зелёные
- [x] 1.5 «Лимит попыток останавливает повторы»: `tests/test_job_attempts.py::test_parse_run_fails_after_max_attempts`, `test_recover_does_not_requeue_poisoned_parse_run`, `test_llm_job_errors_after_max_attempts`, `test_recover_does_not_requeue_poisoned_llm_job`, `tests/test_hh_sync_jobs.py::test_recover_fails_job_after_max_attempts`. Проверка — тесты зелёные
- [x] 1.6 «Отмена хранится флагом в БД»: в очереди — `tests/test_parse_jobs.py::test_cancel_queued_run`, `tests/test_job_cancel.py::test_cancel_queued_llm_job_is_immediate`, `tests/test_hh_sync_jobs.py::test_cancel_queued_sync_is_immediate_and_frees_slot`; отмена побеждает лимит — `tests/test_job_attempts.py::test_cancel_requested_wins_over_attempt_limit` (только прогон; для LLM и HH — теста нет: их тесты recover с флагом не исчерпывают попытки). Проверка — тесты зелёные, пробел записан
- [x] 1.7 «Единственность активной задачи»: прогон — `tests/test_parse_jobs.py::test_second_post_runs_while_queued_returns_409`, `tests/test_parse_active_unique.py`; HH — `tests/test_hh_api.py::test_hh_sync_conflict_while_job_is_active`; пара оценки — `tests/test_llm_jobs.py::test_llm_job_reuses_active_and_enforces_per_user_limit`, `tests/test_llm_job_one_active.py::test_parallel_enqueue_of_same_pair_creates_one_job`. Проверка — тесты зелёные
- [x] 1.8 «Лимит параллельных LLM-задач пользователя»: `tests/test_llm_jobs.py::test_llm_job_reuses_active_and_enforces_per_user_limit` (статус 429), `tests/test_llm_job_one_active.py::test_parallel_enqueue_respects_per_user_limit`; код `RATE_LIMITED` в теле ответа — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 1.9 «Идемпотентная постановка»: повтор после успеха — `tests/test_idempotency.py::test_idempotent_post_runs_returns_same_run_id`, `tests/test_hh_api.py::test_hh_sync_idempotency_key_returns_same_job`, `tests/test_vacancy_import.py::test_import_job_replays_by_idempotency_key`; ошибка освобождает ключ — `tests/test_idempotency.py::test_idempotent_key_replays_conflict_without_second_run`; другое тело, `IDEMPOTENCY_IN_PROGRESS`, ключ длиннее 128 — теста нет. Проверка — тесты зелёные, пробелы записаны
- [x] 1.10 «Режимы rq и inprocess взаимозаменяемы»: `tests/test_job_attempts.py::test_api_start_recovers_queued_run_inprocess`, `test_api_recovers_running_run_after_lease_expiry`, `test_api_start_skips_recovery_for_rq_backends`; выполнение через RQ — `tests/test_rq_worker_entrypoints.py` (по задаче каждого типа). Проверка — тесты зелёные

## 2. identity/authentication

- [x] 2.1 «Первый зарегистрированный пользователь — администратор»: `tests/test_auth.py::test_first_user_claims_legacy_rows`, `test_register_login_me`; атомарность «первый ли» при параллельной регистрации — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.2 «Регистрацию можно закрыть»: `tests/test_auth.py::test_registration_closed_for_new_users`, `test_registration_closed_by_default_in_prod`, `tests/test_settings_env.py::test_explicit_values_win_in_prod`. Проверка — тесты зелёные
- [x] 2.3 «Данные для регистрации проверяются»: `tests/test_auth.py::test_register_rejects_invalid_email`, `test_register_duplicate_race_is_conflict_not_500`, `test_login_accepts_legacy_email_without_validation`. Проверка — тесты зелёные
- [x] 2.4 «Вход выдаёт access-токен и refresh-сессию»: `tests/test_auth.py::test_register_login_me`, `test_token_form_login_for_swagger`, `tests/test_auth_refresh.py::test_login_sets_httponly_strict_cookie_on_auth_path`; вход отключённого пользователя (403) — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.5 «Попытки входа ограничены»: `tests/test_auth.py::test_failed_logins_lock_email_before_password_check`, `test_successful_login_resets_failures`, `test_auth_attempts_limited_per_ip`, `test_failed_logins_counted_in_redis`. Проверка — тесты зелёные и проверяют `Retry-After`
- [x] 2.6 «Access-токен принимается только из заголовка»: `tests/test_api_access.py::test_jwt_in_query_is_not_accepted_anywhere`, `test_reads_require_jwt`, `tests/test_auth.py::test_token_without_version_claim_still_valid`. Проверка — тесты зелёные
- [x] 2.7 «Refresh-сессия в защищённом cookie»: `tests/test_auth_refresh.py::test_login_sets_httponly_strict_cookie_on_auth_path` (в том числе: в БД только хэш), `test_cookie_secure_in_prod_settings`; продление срока сессии при обновлении — теста нет. Проверка — тесты зелёные, пробелы записаны
- [x] 2.8 «Обновление ротирует refresh-токен»: `tests/test_auth_refresh.py::test_refresh_rotates_cookie_and_issues_working_access_token`, `test_refresh_without_or_with_unknown_cookie_is_401`, `test_parallel_refresh_within_grace_keeps_session`, `test_reuse_after_grace_revokes_whole_session`, `test_reuse_does_not_touch_other_sessions`, `test_expired_refresh_token_is_401`, `test_disabled_user_cannot_refresh`. Проверка — тесты зелёные
- [x] 2.9 «Выход и отзыв сессий»: `tests/test_auth_refresh.py::test_logout_revokes_session_and_clears_cookie`, `test_logout_without_cookie_is_ok`, `test_logout_all_and_password_change_revoke_refresh`, `tests/test_auth.py::test_logout_all_revokes_tokens`, `test_change_password_revokes_old_tokens`; «неверный текущий пароль засчитывается как неудачный вход» — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 2.10 «SSE получает доступ одноразовым тикетом»: `tests/test_sse_ticket.py` (все тесты файла). Проверка — тесты зелёные
- [x] 2.11 «Секрет подписи обязателен в prod»: `tests/test_settings_env.py::test_prod_requires_long_jwt_secret`, `test_startup_error_does_not_echo_env_values`; временный секрет в dev — теста нет. Проверка — тесты зелёные, пробел записан

## 3. identity/access-control

- [x] 3.1 «Анониму доступны только здоровье и вход»: `tests/test_api_access.py::test_reads_require_jwt`, `test_mutations_require_jwt`, `tests/test_api_health.py::test_health`. Проверка — тесты зелёные
- [x] 3.2 «Общие данные читает любой вошедший пользователь»: `tests/test_api_access.py::test_non_admin_can_read`. Проверка — тест зелёный
- [x] 3.3 «Прогоны, импорт и HH доступны только администратору»: `tests/test_api_access.py::test_mutations_forbidden_for_non_admin`, `test_run_reads_forbidden_for_non_admin`, `tests/test_hh_api.py::test_hh_sync_jobs_are_admin_only`, `tests/test_vacancy_import.py::test_import_is_admin_only`. Проверка — тесты зелёные
- [x] 3.4 «Личные данные видит только владелец»: `tests/test_auth.py::test_resumes_are_per_user`, `test_resume_update`, `test_saved_filters_are_per_user`, `tests/test_solo_match.py::test_solo_match_404_and_auth`, `tests/test_cover_letter.py::test_cover_letter_404_and_auth`, `tests/test_job_cancel.py::test_cancel_llm_job_api`; «администратор не видит чужие личные данные» — теста нет. Проверка — тесты зелёные, пробел записан

## 4. parse-runs

- [x] 4.1 «Конфигурация прогона — серверный конфиг плюс разрешённые overrides»: `tests/test_api_access.py::test_run_rejects_non_whitelisted_overrides`, `test_run_rejects_model_outside_catalog`, `test_build_config_applies_whitelisted_overrides`. Проверка — тесты зелёные
- [x] 4.2 «Воркер выполняет снимок конфигурации»: теста нет (воркер читает `config_snapshot` в `parse_tasks`, но смену `config.yaml` после постановки тест не проверяет). Проверка — пробел записан
- [x] 4.3 «Отмена прогона администратором»: `tests/test_parse_jobs.py::test_cancel_queued_run`, `test_cancel_running_sets_flag`, `test_reset_parse_run_to_queued_with_cancel_stays_cancelled`, `tests/test_parse_job_resilience.py::test_cancel_poll_survives_db_error`, `tests/test_streaming_parse.py::test_user_cancel_reaches_fetch`; «вакансии отменённого прогона остаются» — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 4.4 «Прогресс прогона доступен по ходу работы»: `tests/test_run_sse.py::test_get_run_returns_patched_progress`, `tests/test_parse_jobs.py::test_patch_run_progress_sets_heartbeat`, `tests/test_parse_job_resilience.py::test_progress_write_failure_does_not_fail_run`. Проверка — тесты зелёные
- [x] 4.5 «Прогон, не сохранивший вакансии, не завершается успешно»: `tests/test_parse_job_resilience.py::test_vacancy_save_failure_fails_run_instead_of_completing`. Проверка — тест зелёный
- [x] 4.6 «Прогон, потерявший посты из-за LLM, неполный»: `tests/test_streaming_parse.py::test_llm_posts_lost`, `tests/test_parse_job_resilience.py::test_incomplete_run_is_partial_and_keeps_vacancies`, `tests/test_ai_transient_retry.py::test_primary_error_ratio_above_threshold_marks_run_incomplete`, `test_primary_error_ratio_within_threshold_stays_complete`, `test_circuit_breaker_stops_llm_after_consecutive_errors`. Проверка — тесты зелёные
- [x] 4.7 «Вакансии прогона»: `tests/test_parse_run_vacancies.py` (все тесты файла); «HH sync не добавляет вакансии в прогон» — теста нет. Проверка — тесты зелёные, пробел записан
- [x] 4.8 «Экспорт прогона ограничен»: `tests/test_export_streaming.py::test_run_exports_are_capped`. Проверка — тест зелёный

## 5. Итоговая проверка

- [x] 5.1 Прогнать все тесты, перечисленные в группах 1–4 (`.venv/Scripts/python.exe -m pytest -n 8 -q <файлы>`) — зелёные; при расхождении сценария с тестом — поправить сценарий в spec этого изменения, а не тест
- [x] 5.2 Список пробелов («теста нет») и расхождений из design.md (Open Questions) собран в итоговом отчёте для решения: какие закрыть тестами или отдельными изменениями
- [x] 5.3 `openspec validate baseline-invariants --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/baseline-invariants` в `master` через `--no-ff`.
- `/opsx:archive baseline-invariants`.

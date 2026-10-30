# Tasks

## 1. Токены доступа

- [x] 1.1 Миграция `049_api_tokens`, `ApiTokenRow`, `backend/services/api_tokens.py`
- [x] 1.2 `/auth/tokens`: выпуск и отзыв только из сессии входа, лимит, валидация. Проверка — `tests/test_api_tokens.py::test_create_list_and_use_token`, `test_token_cannot_manage_tokens`, `test_validation_and_limit`
- [x] 1.3 Scope'ы в `get_current_user`, `pat_scope`, отказ в `require_admin`. Проверка — `test_read_scope_allows_get_only`, `test_llm_scope_opens_marked_endpoints_only`, `test_token_never_opens_admin_endpoints`
- [x] 1.4 Отзыв, срок, смена пароля, изоляция пользователей. Проверка — `test_revoked_expired_and_password_change_reject_token`, `test_token_sees_only_own_data_and_tokens`
- [x] 1.5 UI «Токены». Проверка — `npm run lint`, `npm run build`

## 2. MCP-сервер

- [x] 2.1 `tg_jobs_mcp`: клиент API, форматирование, инструменты. Проверка — `tests/test_mcp_server.py::test_tools_listed_with_schemas_and_hints`, `test_search_filters_preset_and_paging`, `test_get_vacancy_marks_text_as_data`, `test_resume_tools_and_isolation`, `test_format_helpers`
- [x] 2.2 LLM-инструменты: кэш, ожидание, `job_id` по таймауту. Проверка — `test_match_resume_waits_for_llm_job`, `test_llm_job_returned_by_id_after_timeout`; отмена задачи при отмене вызова клиентом — теста нет
- [x] 2.3 Ошибки и запуск. Проверка — `test_errors_explain_token_problems`, `test_stdio_entrypoint_lists_tools_and_reports_unreachable_api`, `test_settings_from_env`, `test_main_without_token_exits`
- [x] 2.4 Extra `mcp`, `poetry.lock`, CI, README, CLAUDE.md

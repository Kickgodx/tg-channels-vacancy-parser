# Tasks

## 1. LLM

- [x] 1.1 `ResumeTailoringExtraction`, промпт `resume-tailor-v1`, `tailor_resume(_with_fallback)` с моделью писем и strict JSON. Проверка — `tests/test_resume_tailoring.py` (`test_parse_tailoring_*`, `test_prompt_*`, `test_tailor_uses_cover_letter_model_and_json_schema`)

## 2. Backend

- [x] 2.1 Таблица `resume_tailorings` (миграция 041), репозиторий, сброс при правке/удалении резюме, потери `replace`. Проверка — `test_tailor_dropped_on_resume_edit_and_delete`
- [x] 2.2 Сервис, `KIND_RESUME_TAILOR` в воркере, `resume_tailoring` в `/llm-jobs/{id}`, роуты GET/POST/DELETE, `stale`. Проверка — `test_tailor_runs_llm_then_serves_cache`, `test_tailor_foreign_resume_and_missing_vacancy`
- [x] 2.3 `pypdf` в pyproject/poetry.lock, `extract_resume_text`, `POST /resumes/parse-file`. Проверка — `test_extract_*`, `test_parse_file_endpoint`

## 3. UI и документация

- [x] 3.1 `ResumeTailoringPanel` в карточке, загрузка файла в форме резюме. Проверка — `npm run lint`, `npm run build`, скриншот
- [x] 3.2 README, AGENTS (версия промпта), ROADMAP (head миграций)

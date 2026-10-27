# Tasks

## 1. LLM

- [x] 1.1 `InterviewPrepExtraction`, промпт `interview-prep-v1`, `interview_prep(_with_fallback)` с моделью писем и strict JSON. Проверка — `tests/test_interview_prep.py` (`test_parse_interview_prep_*`, `test_prompt_marks_missing_skills_as_gap_topics`, `test_interview_prep_uses_cover_letter_model_and_json_schema`)

## 2. Backend

- [x] 2.1 Таблица `interview_preps` (миграция 043), репозиторий, сброс при правке/удалении резюме, потери `replace`. Проверка — `test_interview_prep_dropped_on_resume_edit_and_delete`
- [x] 2.2 Сервис, `KIND_INTERVIEW_PREP` в воркере, `interview_prep` в `/llm-jobs/{id}`, роуты GET/POST/DELETE, `stale`, навыки сохранённой оценки. Проверка — `test_interview_prep_runs_llm_then_serves_cache`, `test_interview_prep_passes_saved_match_skills`, `test_interview_prep_foreign_resume_and_missing_vacancy`

## 3. UI и документация

- [x] 3.1 `InterviewPrepPanel` в карточке вакансии. Проверка — `npm run lint`, `npm run build`
- [x] 3.2 README, AGENTS (версия промпта), ROADMAP (head миграций)

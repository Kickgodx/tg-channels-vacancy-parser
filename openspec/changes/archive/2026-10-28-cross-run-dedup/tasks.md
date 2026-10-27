# Tasks

## 1. Дедуп

- [x] 1.1 Блоки по компании и контакту, правило полного текста с грейдом, компанией и окном. Проверка — `tests/test_deduplicator.py` (`test_edited_repost_is_text_duplicate`, `test_text_rule_guards_grade_company_window_and_off`, `test_contact_block_finds_duplicate_with_other_specializations`)
- [x] 1.2 `seed` вакансий из БД, `known_vacancies` в `run_parse`, загрузка в воркере. Проверка — `test_seeded_known_vacancy`, `tests/test_cross_run_dedup.py` (`test_parse_job_passes_known_vacancies`, `test_recent_vacancies_for_dedup`)

## 2. Запись

- [x] 2.1 Дубль вакансии из БД сдвигает дату, возвращает из архива, привязывает к прогону. Проверка — `test_repost_of_db_vacancy_touches_it_and_records_sighting`
- [x] 2.2 `vacancies.duplicate_of` (миграция 046), `db merge-duplicates`. Проверка — `test_merge_duplicates_once`, пробный запуск на живой базе

## 3. Документация

- [x] 3.1 README, config.md, config.yaml, ROADMAP (head миграций)

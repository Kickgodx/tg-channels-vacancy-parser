# Tasks

## 1. Изменения

- [x] 1.1 `deduplication.changes` (`vacancy_changes`, `is_edited_repost`). Проверка — `tests/test_vacancy_history.py` (`test_changes_salary_format_city_and_mentioned_technologies`, `test_no_changes_and_vanished_salary`)
- [x] 1.2 `vacancy_sightings.changes` (миграция 047), запись в `_insert_sightings` и в `db merge-duplicates`. Проверка — `test_run_records_changes_and_history_api`, `test_merge_duplicates_records_changes`

## 2. API и UI

- [x] 1.3 Склеивание: посты с текстом копии — перепосты с правками, выпрямление цепочек (`flatten_hidden_copies`). Проверка — `tests/test_cross_run_dedup.py::test_flatten_hidden_copies_repoints_chain`, `test_merge_all_follows_chain_through_moved_date`, живая база
- [x] 2.1 `GET /vacancies/{id}/history`. Проверка — `test_run_records_changes_and_history_api`, `test_merge_duplicates_records_changes`
- [x] 2.2 `VacancyHistoryBlock` в карточке. Проверка — `npm run lint`, `npm run build`
- [x] 2.3 README, ROADMAP (head миграций)

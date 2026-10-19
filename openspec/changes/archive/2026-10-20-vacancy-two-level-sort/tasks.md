# Tasks

## 1. Backend

- [x] 1.1 `then_by` / `then_order` в `VacancyQuery` (валидатор пары), в `GET /vacancies` и `/vacancies/export.xlsx` (`check_sort_pair`). Проверка — `tests/test_vacancy_multi_sort.py`: 422 вне пары, пресет хранит вторую ступень
- [x] 1.2 ORDER BY в `_selected_order`: день по UTC литералом `'UTC'`, равные — от новых к старым. Проверка — тесты порядка и страниц в `tests/test_vacancy_multi_sort.py`
- [x] 1.3 Миграция 034 с двумя частичными индексами, одним запросом — только первая страница индексируемой сортировки. Проверка — `EXPLAIN (GENERIC_PLAN)` без узла Sort; на compose-базе 1.7 тыс. активных: первая страница ~1 мс, 30-я — 6 мс вместо 36

## 2. UI

- [x] 2.1 Shift+клик и кнопка над таблицей, индикаторы ¹/², `then_by` в URL, пресетах и экспорте; дата в таблице по UTC. Проверка — `npm run lint`, `npm run build`

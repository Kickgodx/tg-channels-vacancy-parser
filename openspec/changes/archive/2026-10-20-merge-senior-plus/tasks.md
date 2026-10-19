# Tasks

## 1. Ядро

- [x] 1.1 Убрать `senior+` из `_ORDER` и `_PATTERNS`, алиас `senior+` → `senior` в `normalize_grade`. Проверка — `tests/test_merge_senior_plus.py`: «Senior+ …» и `normalize_grade("senior+")` дают `senior`, `senior+` нет в `GRADE_OPTIONS`
- [x] 1.2 HH: `moreThan6` и «Более 6 лет» → `senior`. Проверка — `tests/test_hh_mapper.py`, `tests/test_hh_grades_backfill.py`
- [x] 1.3 Промпт: «6+ лет» → `senior`, `EXTRACT_PROMPT_VERSION = "extract-v8"`; golden-кейсы и offline-фикстуры. Проверка — offline-проверка golden по фикстурам в общем прогоне pytest

## 2. Данные и UI

- [x] 2.1 Миграция `035_merge_senior_plus`: `vacancies.detected_grade` и `saved_filters.params.grade`. Проверка — `tests/test_merge_senior_plus.py`: строки с `senior+` стали `senior`
- [x] 2.2 `GRADE_ORDER` в `AnalyticsPanel.tsx`, README. Проверка — `npm run lint`, `npm run build`

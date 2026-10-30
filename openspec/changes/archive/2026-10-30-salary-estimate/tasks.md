# Tasks

## 1. Backend

- [x] 1.1 Миграция 050 `vacancy_salary_estimates`, модель `VacancySalaryEstimateRow`
- [x] 1.2 `backend/services/salary_estimate_service.py`: соседи (грейд, окно, модель), квантили, свежесть, проход лидера. Проверка — `tests/test_salary_estimate.py` (`test_estimate_nearest_same_grade`, `test_unknown_grade_searches_all_grades`, `test_too_few_neighbours_no_estimate`, `test_excluded_neighbours`, `test_sweep_stores_refreshes_and_drops`)
- [x] 1.3 `GET /vacancies/{id}/salary-estimate`. Проверка — `test_endpoint_statuses`, `test_endpoint_insufficient_and_disabled`, `test_endpoint_uses_stored_fresh_estimate`
- [x] 1.4 Флаг `salary_estimates` в фильтре списка; `analytics_filters` для метрик и аналитики. Проверка — `test_list_flag_counts_estimates_but_analytics_does_not`
- [x] 1.5 Лидер API: проход оценок после индексации эмбеддингов (`backend/main.py`)
- [x] 1.6 CLI `db salary-estimate-eval`. Проверка — `test_evaluate_leave_one_out`; на живой базе — медианная ошибка 25,5%, внутри p25–p75 48,6%

## 2. UI и документация

- [x] 2.1 Блок «Ориентировочная зарплата» в карточке вакансии без зарплаты; флажок «…и с оценкой по похожим». Проверка — `npm run lint`, `npm run build`, API на копии живой базы
- [x] 2.2 README (раздел «Аналитика», таблица API, CLI)

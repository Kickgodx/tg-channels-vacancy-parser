# Tasks

## 1. Расчёт и API

- [x] 1.1 `plan_route`: жадный маршрут, отброс хвоста без новых подходящих, фильтр редких технологий и практик. Проверка — `tests/test_career_paths.py::test_route_picks_tech_that_opens_most_vacancies`, `test_route_keeps_combo_step_and_drops_useless_tail`, `test_route_coverage_only_and_filters`
- [x] 1.2 `career_paths`: позиция, шаг по грейду, соседние роли, прирост зарплаты. Проверка — `test_career_paths_grade_step_and_neighbour_role`, `test_career_paths_small_grade_falls_back_to_all_grades`
- [x] 1.3 `GET /resumes/{id}/career-paths`: чужое резюме — 404, без эмбеддингов — 503. Проверка — `test_career_paths_empty_and_access`, `test_career_paths_embeddings_disabled_is_503`
- [x] 1.4 `nearest_vacancies(specialization=...)`. Проверка — тесты 1.2 и `tests/test_resume_market.py`

## 2. UI и документация

- [x] 2.1 `CareerPathsPanel` на странице «Резюме». Проверка — `npm run lint`, `npm run build`
- [x] 2.2 README

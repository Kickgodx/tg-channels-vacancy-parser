# Tasks

- [x] 1.1 Детектор: жёсткие и мягкие исключения, хэштеги вакансии. Проверка — `tests/test_vacancy_detector.py::test_seeker_and_digest_posts_are_not_vacancies`, `test_vacancy_with_learning_words_passes`, `test_course_ads_without_role_excluded`; на 631 вакансии HH ложных исключений 0 (было 137)
- [x] 1.2 `clean_technology_names`, HH-маппер, merge LLM. Проверка — `tests/test_technology_cleanup.py`
- [x] 1.3 `db clean-vacancies`. Проверка — `tests/test_vacancy_cleanup.py`; применено к живой базе после дампа `tgjobs_20261030_174612.dump`
- [x] 1.4 README

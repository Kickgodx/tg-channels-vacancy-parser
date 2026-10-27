# Tasks

## 1. Backend

- [x] 1.1 `technology_matcher` в `skill_gap_service`, `resume_market_service` (ближайшие по эмбеддингу, профиль, технологии, квантили), `GET /resumes/{id}/market`. Проверка — `tests/test_resume_market.py` (`test_market_profile_salary_and_technologies`, `test_market_without_enough_salaries`, `test_market_empty_catalog_and_access`, `test_market_embeddings_disabled_is_503`, `test_quantile_matches_percentile_cont`)

## 2. UI и документация

- [x] 2.1 `ResumeMarketPanel` на странице «Резюме», общая полоса `SalaryStrip`. Проверка — `npm run lint`, `npm run build`
- [x] 2.2 README

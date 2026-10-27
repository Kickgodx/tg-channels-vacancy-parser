# Tasks

## 1. Расчёт и API

- [x] 1.1 `skill_what_if`: сегмент, подходящие вакансии, варианты, `peer_gaps`. Проверка — `tests/test_skill_what_if.py::test_what_if_options_and_peer_gaps`, `test_is_learnable`
- [x] 1.2 `GET /resumes/{id}/what-if`: доступ к чужому резюме — 404, без эмбеддингов — 503. Проверка — `test_what_if_empty_and_access`, `test_what_if_embeddings_disabled_is_503`
- [x] 1.3 `technology_premiums`, общие помощники «Резюме и рынок». Проверка — `tests/test_resume_market.py`, `tests/test_analytics_db.py`

## 2. UI и документация

- [x] 2.1 `SkillWhatIfPanel` на странице «Резюме». Проверка — `npm run lint`, `npm run build`
- [x] 2.2 README

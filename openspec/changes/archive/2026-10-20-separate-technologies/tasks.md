# Tasks

## 1. Ядро

- [x] 1.1 Каталог технологий, `TECHNOLOGY_ALIASES`, `canonical_technologies`, `split_specializations`; языки убраны из `DEFAULT_SPECIALIZATIONS`. Проверка — `tests/test_separate_technologies.py`
- [x] 1.2 Разделение в правилах, ответе LLM, мёрже, HH, `FiltersConfig`; фильтр технологий прогона. Проверка — `tests/test_separate_technologies.py`, `tests/test_ai_merger.py`, `tests/test_hh_mapper.py`
- [x] 1.3 Промпт: роль — функция, стек — технологии, `extract-v10`; golden-кейсы и offline-фикстуры (два новых кейса). Проверка — `tests/test_evals_extraction.py`

## 2. Данные и API

- [x] 2.1 Миграция `037_separate_technologies`. Проверка — `tests/test_separate_technologies.py::test_migration_separates_technologies`
- [x] 2.2 Фильтр `technology` в списке/метриках/аналитике/экспорте, старые ссылки, фасет, топ технологий в метриках. Проверка — `tests/test_api_health.py::test_vacancy_metrics_aggregated`, `tests/test_vacancy_metrics_sql.py`
- [x] 2.3 LLM-джоба `reextract` и `GET|POST /vacancies/reextract`. Проверка — `tests/test_reextract.py`

## 3. UI и документация

- [x] 3.1 Фильтр «Технологии», колонка, график «Топ технологий», технологии в форме прогона, панель переразбора. Проверка — `npm run lint`, `npm run build`
- [x] 3.2 README, config.md, config.yaml

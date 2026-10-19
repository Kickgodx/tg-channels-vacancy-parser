# Tasks

## 1. Ядро

- [x] 1.1 `DEFAULT_SPECIALIZATIONS` без `Тестировщик` / `Manual QA` / `AQA` / `Программист, разработчик`, синонимы — в `QA` / `Automation QA` / `Software Engineer`; `canonical_specializations` в ответе LLM, `FiltersConfig`, `merged_specializations`. Проверка — `tests/test_merge_qa_specializations.py`, `tests/test_specialization_extractor.py`
- [x] 1.2 HH: роли сразу канонические. Проверка — `tests/test_hh_mapper.py`
- [x] 1.3 Промпт: правило QA / Automation QA, `EXTRACT_PROMPT_VERSION = "extract-v9"`; golden-кейсы и offline-фикстуры. Проверка — offline-проверка golden по фикстурам в общем прогоне pytest

## 2. Данные и API

- [x] 2.1 Миграция `036_merge_qa_specializations`: `vacancies.detected_specializations` и `saved_filters.params.specializations`. Проверка — `tests/test_merge_qa_specializations.py`
- [x] 2.2 Фильтр списка вакансий приводит прежние имена; `config.yaml`, README, config.md. Проверка — общий прогон pytest

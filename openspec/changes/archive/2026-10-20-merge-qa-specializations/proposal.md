# Proposal

## Why

Одна и та же роль жила под несколькими тегами: `QA`, `Тестировщик` и `Manual QA` —
ручное тестирование; `AQA` и `Automation QA` — автоматизация; HH-роль
«Программист, разработчик» дублировалась в `Software Engineer`. Фильтры UI
показывали все варианты, а вакансия попадала то под один, то под другой тег —
фильтр по `QA` терял часть QA-вакансий, аналитика дробилась.

## What Changes

- Канонические имена: `QA` (вобрал «Тестировщик», `Manual QA`), `Automation QA`
  (вобрал `AQA`), `Software Engineer` (вобрал «Программист, разработчик» и голое
  «программист» в тексте). Их синонимы
  переехали в канонические записи `DEFAULT_SPECIALIZATIONS`, прежние ключи удалены —
  в фильтрах UI их больше нет.
- `SPECIALIZATION_ALIASES` / `canonical_specializations` приводят прежние имена:
  ответ LLM и кэш extraction, `filters.specializations` (config.yaml, overrides,
  снимки прогонов), `dictionaries.specializations`, фильтр списка вакансий.
- HH: роли «Тестировщик» и «Программист, разработчик» сразу пишутся как `QA` /
  `Software Engineer`, без исходного имени роли.
- Промпт extraction: правило QA / Automation QA; версия `extract-v9`.
- Миграция `036_merge_qa_specializations`: `vacancies.detected_specializations` и
  `saved_filters.params.specializations`. Снимки прогонов не меняются — это история.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `ingestion/rule-extraction` — приведение синонимов специализаций.
- `sources/hh-sync` — роли HH под каноническими именами.

## Impact

`tg_jobs_parser/config/schema.py`, `tg_jobs_parser/ai/schema.py`,
`tg_jobs_parser/ai/prompts.py`, `tg_jobs_parser/hh/mapper.py`,
`backend/db/vacancy_filters.py`, `alembic/versions/036_merge_qa_specializations.py`,
`config.yaml`, golden-кейсы extraction, README, config.md.
Смена версии промпта сбрасывает кэш extraction: повторный разбор тех же постов снова
пойдёт в LLM.

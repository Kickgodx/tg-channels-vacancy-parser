# Proposal

## Why

Грейд `senior+` в базе — по сути «HH, опыт больше 6 лет»: 68 из 70 таких вакансий
пришли с HH, где грейд считается только по полю опыта, и в Telegram-постах «Senior+»
почти не встречается (2 поста, оба «Senior/Staff»). LLM ставил `senior+` только по
правилу «6+ лет», хотя у 401 вакансии `senior` из Telegram тоже часто «6+», «7+»,
«8+ лет». Отдельный грейд дробит аналитику (у `senior+` 11 вакансий с зарплатой) и
не несёт смысла, отличного от `senior`.

## What Changes

- Грейда `senior+` больше нет: порядок — `intern` < `junior` < `junior+` < `middle` <
  `middle+` < `senior` < `lead` < `staff` < `principal`.
- «Senior+» в тексте, `senior+` от LLM и в старых данных (кэш ответов LLM) приводятся
  к `senior`.
- HH: опыт «больше 6 лет» → `senior`. Грейд HH по-прежнему определяется только по опыту.
- Промпт extraction: «6+ лет» → `senior`; версия `extract-v8`.
- Миграция `035_merge_senior_plus`: `senior+` → `senior` в `vacancies` и в
  сохранённых фильтрах (`saved_filters.params.grade`). Снимки метрик прошлых прогонов
  (`parse_runs.metrics`) не меняются — это история.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `ingestion/rule-extraction` — порядок грейдов без `senior+`, приведение `senior+`.
- `sources/hh-sync` — «больше 6 лет» → `senior`.

## Impact

`tg_jobs_parser/extraction/grade_extractor.py`, `tg_jobs_parser/hh/grades.py`,
`tg_jobs_parser/ai/prompts.py`, `alembic/versions/035_merge_senior_plus.py`,
`frontend/src/components/AnalyticsPanel.tsx`, golden-кейсы extraction, README.
Смена версии промпта сбрасывает кэш extraction: повторный разбор тех же постов снова
пойдёт в LLM.

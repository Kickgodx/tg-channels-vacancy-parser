# Proposal

## Why

В специализации попадал стек: `DEFAULT_SPECIALIZATIONS` содержал `Python`, `Java`,
`Go`, `Golang`, а промпт учил LLM ставить роль `["Go"]` для «Go Developer»; модель
добавляла и `PHP`, `C++`, `.NET`, `Rust`, `1C`. AQA на Python получала
`["Automation QA", "Python"]`, ML на Python — `["AI", "Python"]`: фильтр по роли и
аналитика по специализациям смешивали функцию и технологию. Поле `technologies` уже
было, но без нормализации написания (`Go` / `Golang`), без индекса и без фильтра.

## What Changes

- Специализация — только роль. Языки, фреймворки, БД, платформы и инструменты —
  технологии: `split_specializations` переносит их из специализаций правил, ответа LLM
  и кэша extraction, ролей HH, `filters.specializations` (config.yaml, overrides,
  снимки прогонов) и запроса списка (старые ссылки). «Python Developer» без другой
  роли получает `Software Engineer`. Из `DEFAULT_SPECIALIZATIONS` убраны
  `Python` / `Java` / `Go` / `Golang`; `DEFAULT_TECHNOLOGIES` расширен.
- Технологии хранятся в каноническом написании (`canonical_technologies`,
  `TECHNOLOGY_ALIASES`): `Golang` → `Go`, `Apache Kafka` → `Kafka`, регистр — по словарю.
- Фильтр по технологиям: в списке, метриках, аналитике и экспорте (`technology`) и в
  прогоне (`filters.technologies`) — любая из выбранных, со специализациями через «и».
  Фасет `GET /vacancies/technologies` — варианты фильтра; в метриках — топ-50 технологий.
- Промпт extraction: роль — функция, стек — `technologies`, правило для
  «<язык> developer»; версия `extract-v10`.
- Миграция `037_separate_technologies`: `vacancies.technologies` → JSONB + GIN,
  языки из специализаций — в технологии, канонизация технологий, пресеты фильтров.
- Переразбор LLM: `POST /vacancies/reextract` (admin) — LLM-джоба `reextract` для
  вакансий, оставшихся без роли.

## Capabilities

### New Capabilities

- `ingestion/reextraction` — переразбор LLM сохранённых вакансий.

### Modified Capabilities

- `ingestion/rule-extraction` — роли и технологии раздельно, фильтр технологий прогона.
- `catalog/vacancy-search` — фильтр по технологиям, фасет.
- `catalog/analytics` — топ технологий в метриках.
- `sources/hh-sync` — key_skills в каноническом написании, роли HH через разделение.

## Impact

`tg_jobs_parser/config/schema.py`, `tg_jobs_parser/ai/{schema,prompts,merger,search_context}.py`,
`tg_jobs_parser/ingestion/rules.py`, `tg_jobs_parser/filters/vacancy_filters.py`,
`tg_jobs_parser/hh/mapper.py`, `backend/{schemas.py,api/params.py}`,
`backend/db/{models,vacancy_filters,vacancy_metrics}.py`, `backend/api/routes/{vacancies,config,llm_jobs}.py`,
`backend/services/{reextract_service,llm_job_service,import_service}.py`,
`backend/workers/llm_tasks.py`, миграция 037, frontend (фильтр, колонка, график,
форма прогона, панель переразбора), golden-кейсы extraction, README, config.md, config.yaml.
Смена версии промпта сбрасывает кэш extraction. Фильтр прогона, где языки стояли
в `specializations` (логика «или»), после переноса в `technologies` сужает выборку
(логика «и»): `config.yaml` обновлён — языки убраны, `technologies: []`.

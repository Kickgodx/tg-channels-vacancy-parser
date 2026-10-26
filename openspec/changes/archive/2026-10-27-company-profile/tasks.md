# Tasks

## 1. Ключ компании

- [x] 1.1 `tg_jobs_parser/utils/company.py`, колонка и миграция 040 с бэкфиллом, запись в `vacancy_to_row` и `_EXTRACTION_COLUMNS`, импорт дампа, почасовой бэкфилл. Проверка — `tests/test_company_profile.py` (`test_company_key`, `test_mapper_writes_company_key`, `test_backfill_company_keys`)

## 2. Профиль и флаги

- [x] 2.1 `backend/db/company_profile.py`: профиль, флаги; `GET /companies/profile`, `GET /vacancies/{id}/flags`. Проверка — `tests/test_company_profile.py`
- [x] 2.2 Топ работодателей по `company_key`. Проверка — `test_top_companies_merge_spellings`, `tests/test_analytics_db.py`

## 3. UI и документация

- [x] 3.1 Страница `/company`, флаги и ссылка на компанию в карточке, ссылки из топа компаний. Проверка — `npm run lint`, `npm run build`, скриншот на живых данных
- [x] 3.2 README (возможности, API, схема БД)

# Proposal

## Why

Baseline этапов 2 и 4 нашёл места, где данные каталога и источников теряются
или искажаются молча:
- фильтр `has_salary` смотрит на зарплату в исходной валюте, а метрики и
  аналитика — на рублёвую сумму, поэтому цифры не сходятся со списком;
  `has_salary=false` не фильтрует вовсе;
- пересчёт рублёвых сумм пропускает архивные вакансии;
- `salary_display.rates_as_of` без курсов показывает сегодняшнюю дату, как будто
  курсы свежие;
- если курсов в базе нет и ЦБ недоступен, запрос к ЦБ уходит на каждую нерублёвую
  вакансию пачки — по 20 с таймаута на каждую;
- HH:
  - неудачная повторная загрузка деталей заменяет полную карточку короткой
    выдачей;
  - выдача глубже 2000 теряется без предупреждения;
  - вакансия без ролей получает выдуманные специализации `AQA` и `Python`;
- Telegram:
  - `filters.any_specialization` не снимает фильтр специализаций;
  - слишком длинное окно обрезается с конца, и теряются самые свежие дни;
  - лимит `parser.max_posts_per_channel` виден только в журнале.

## What Changes

- **BREAKING** `has_salary=true` оставляет вакансии с рублёвой суммой, как
  аналитика. `has_salary=false` оставляет вакансии без неё.
- Пересчёт рублёвых сумм (`POST /fx/refresh`, `POST /fx/recalculate-rub`)
  включает архивные вакансии.
- `salary_display.rates_as_of` без курсов (и при `fx.enabled: false`) — `null`.
- При записи пачки к ЦБ идёт не больше одного запроса, даже если он не удался.
- HH:
  - сбой загрузки деталей уже сохранённой вакансии оставляет её карточку
    прежней;
  - если найдено больше 2000, в новом поле итога `warnings` появляется
    предупреждение (API, тип во frontend и страница HH);
  - вакансия без ролей получает пустой список специализаций.
- Telegram:
  - `any_specialization: true` снимает фильтр специализаций;
  - длинное окно обрезается со стороны `date_from`, свежие дни остаются;
  - лимит канала попадает в `parse_warnings`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `catalog/vacancy-search`: смысл `has_salary`.
- `catalog/salary-fx`: архив в пересчёте, один запрос к ЦБ на пачку,
  `rates_as_of` без курсов.
- `sources/hh-sync`: предупреждение о глубине, сохранение карточки при сбое
  деталей, специализации без ролей, `warnings` в итоге.
- `ingestion/telegram-fetch`: обрезка окна, лимит канала в `parse_warnings`.
- `ingestion/rule-extraction`: `any_specialization` в фильтре специализаций.

## Impact

- Код:
  - фильтры каталога и курсы: `backend/db/vacancy_filters.py`,
    `backend/db/vacancy_metrics.py`, `backend/fx/apply.py`,
    `backend/fx/backfill.py`;
  - HH: `backend/services/hh_sync_service.py`, `backend/api/routes/hh.py`,
    `tg_jobs_parser/hh/mapper.py`;
  - Telegram: `tg_jobs_parser/filters/vacancy_filters.py`,
    `tg_jobs_parser/utils/dates.py`, `tg_jobs_parser/parse_runner.py`,
    `tg_jobs_parser/parsers/telegram_telethon.py`;
  - frontend: `frontend/src/types/api.ts`, `frontend/src/pages/HhPage.tsx`.
- API:
  - `has_salary` меняет смысл;
  - `HhSyncResponse` получает поле `warnings` (по умолчанию пустое; у старых
    итогов в базе его нет, читаются они как прежде);
  - `rates_as_of` может быть `null` (тип во frontend это уже допускает).
- Данные: у HH-вакансий без ролей, сохранённых раньше, `AQA`/`Python` остаются
  до следующего sync, который перезапишет поля. Миграций нет.

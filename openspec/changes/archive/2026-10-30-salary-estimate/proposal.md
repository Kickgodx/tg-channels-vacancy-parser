# Proposal

## Why

У двух третей вакансий Telegram зарплата не указана. Блок «Рынок для похожих» показывает
квантили группы (грейд, роль, технологии), но это распределение по группе, а не оценка для
конкретной вакансии. У каждой вакансии уже есть эмбеддинг, а у сотен — указанная зарплата:
ближайшие по смыслу вакансии того же грейда дают более точную оценку. На живой базе
(718 вакансий с зарплатой, leave-one-out) медианная ошибка оценки 25,5% против 28,2% у
лестницы рыночной вилки, оценку получают 679 вакансий против 617.

## What Changes

- Оценка зарплаты вакансии без рублёвой суммы: медиана и p25–p75 зарплат 15 ближайших по
  эмбеддингу уникальных вакансий с зарплатой того же грейда за год до поста (неизвестный
  грейд — среди всех); меньше 8 похожих — оценки нет.
- Таблица `vacancy_salary_estimates` (миграция 050). Лидер API пересчитывает оценки после
  прохода индексации эмбеддингов: новые, изменённые и старше 7 дней, до 1000 за проход.
- `GET /vacancies/{id}/salary-estimate` — оценка со статусом и пятью ближайшими похожими;
  без свежей сохранённой оценки считается на лету.
- Флаг списка `salary_estimates`: вместе с `has_salary` вакансии с оценкой считаются
  «с зарплатой». Метрики и аналитика флаг игнорируют: оценки в агрегаты не входят. В строках
  списка оценка не показывается.
- CLI `db salary-estimate-eval` — точность оценки на вакансиях с известной зарплатой.
- UI: блок «Ориентировочная зарплата» в карточке вакансии без зарплаты, флажок «…и с
  оценкой по похожим» под «Только с указанной зарплатой».

## Capabilities

### New Capabilities

- `catalog/salary-estimate` — оценка зарплаты вакансии без вилки.

### Modified Capabilities

- `catalog/vacancy-search` — флаг `salary_estimates` у фильтра `has_salary`.

## Impact

`alembic/versions/050_salary_estimates.py`, `backend/db/models.py`,
`backend/services/salary_estimate_service.py`, `backend/main.py` (лидер),
`backend/db/vacancy_filters.py`, `backend/api/params.py`, `backend/api/routes/vacancies.py`,
`backend/api/routes/analytics.py`, `backend/schemas.py`, `tg_jobs_parser/cli/db.py`, frontend
(`SalaryEstimateBlock`, `VacancyModal`, `VacanciesPage`, `api/client.ts`, `types/api.ts`).
LLM не используется; нужен включённый `ai.embeddings`.

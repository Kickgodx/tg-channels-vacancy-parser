# Proposal

## Why

Больше половины вакансий без зарплаты, и по карточке не понять, на что рассчитывать.
Данные для оценки есть: тысячи вакансий с рублёвой зарплатой, грейдом, ролью и
технологиями. Для вакансии с зарплатой полезно обратное: где она относительно рынка.

## What Changes

- `GET /vacancies/{id}/market-salary` — квантили (p10, p25, медиана, p75, p90) рублёвой
  зарплаты похожих вакансий за год до вакансии. Критерии ослабляются, пока похожих не
  наберётся 8: грейд + роль + технология → грейд + роль → грейд + технология → роль. У
  вакансии с зарплатой — доля похожих с зарплатой не выше её.
- UI: блок «Рынок для похожих» в карточке вакансии с полосой p10–p90 / p25–p75 /
  медианой и точкой самой вакансии.

## Capabilities

### New Capabilities

- `catalog/market-salary` — рыночная вилка вакансии.

### Modified Capabilities

Нет.

## Impact

`backend/db/market_salary.py`, `backend/api/routes/vacancies.py`, `backend/schemas.py`,
frontend (`MarketSalaryBlock`, `VacancyModal`, `api/client.ts`, `types/api.ts`).
Миграций нет: до 4 запросов по GIN-индексам `detected_specializations` / `technologies`.

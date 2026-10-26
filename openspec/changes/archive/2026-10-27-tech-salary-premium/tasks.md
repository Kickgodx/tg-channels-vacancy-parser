# Tasks

## 1. Backend

- [x] 1.1 `backend/db/technology_analytics.py`: премия по грейдам (`grade_adjusted_premium`), спрос по месяцам, рост и падение; схемы в `backend/schemas.py`. Проверка — `tests/test_technology_analytics.py`
- [x] 1.2 `GET /analytics/technologies` через кэш агрегатов, без своего фильтра по технологии. Проверка — `test_own_technology_filter_ignored`, `test_endpoint`

## 2. UI и документация

- [x] 2.1 Секция технологий во вкладке «Аналитика» (бары премии с подсказкой по грейдам, линии спроса, «Растут / Падают»). Проверка — `npm run lint`, `npm run build`, скриншот на живых данных
- [x] 2.2 README (раздел «Аналитика», таблица API)

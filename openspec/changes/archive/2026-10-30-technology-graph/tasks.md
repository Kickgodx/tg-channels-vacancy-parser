# Tasks

## 1. Backend

- [x] 1.1 `backend/db/technology_graph.py`: узлы, пары внутри вакансии (LATERAL), lift, роль узла. Проверка — `tests/test_technology_graph.py` (`test_nodes_edges_and_lift`, `test_node_role`, `test_own_technology_filter_ignored_other_filters_apply`, `test_node_limit_and_empty`)
- [x] 1.2 `GET /analytics/technology-graph` с кэшем агрегатов. Проверка — `test_endpoint`; на живой базе (2,5 тыс. вакансий) — 0,3 с

## 2. UI и документация

- [x] 2.1 Раздел «Какие технологии просят вместе»: силовой граф, порог lift, список пар по клику. Проверка — `npm run lint`, `npm run build`, скриншот на копии живой базы
- [x] 2.2 README (раздел «Аналитика», таблица API)

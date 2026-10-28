# Tasks

## 1. Расчёт и хранение

- [x] 1.1 Зависимость scikit-learn, `poetry lock`
- [x] 1.2 Миграция 048 `market_maps` / `market_map_points`
- [x] 1.3 `build_market_map`: PCA → t-SNE → k-means, ниши, подписи, рост; только последняя карта. Проверка — `tests/test_market_map.py::test_build_map_and_resume_position`, `test_rebuild_keeps_only_latest_and_needs_enough_points`, `test_unique_labels`
- [x] 1.4 Пересчёт лидером по `map_interval_hours`. Проверка — `test_map_is_due`

## 2. API и UI

- [x] 2.1 `GET /market-map` (+ резюме), `POST /market-map/rebuild` (админ). Проверка — `test_build_map_and_resume_position`
- [x] 2.2 Страница «Карта рынка». Проверка — `npm run lint`, `npm run build`
- [x] 2.3 README, config.md

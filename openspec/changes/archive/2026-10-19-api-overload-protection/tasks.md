# Tasks

## 1. Настройки

- [x] 1.1 `API_QUEUE_TIMEOUT_SECONDS` (≥0, по умолчанию 2) и `DB_POOL_TIMEOUT` (>0, по умолчанию по роли) в `backend/settings.py`; `pool_timeout` по роли в `backend/db/session.py` (API 5 с, воркеры 30 с). Проверка: тесты `test_settings_env.py` и `test_db_pool.py`.

## 2. Ограничение одновременных запросов

- [x] 2.1 Код ошибки `OVERLOADED` в `backend/errors.py`; ASGI-middleware `backend/overload.py`: семафор из `app.state`, очередь с тайм-аутом, 503 + `Retry-After`, `/health` без очереди, SSE отдаёт место на начале ответа. Проверка: `tests/test_overload.py`.
- [x] 2.2 Подключить middleware самым внутренним в `backend/main.py`, семафор на `api_thread_limit` создавать в lifespan. Проверка: тест через `api_client` (503 при занятых местах, `/health` отвечает).

## 3. UI

- [x] 3.1 Повтор запроса `me` при `ApiError.retryable` (до 3 раз) в `frontend/src/App.tsx`. Проверка: `npm run lint` и `npm run build`.

## 4. Документация и нагрузочный тест

- [x] 4.1 README (надёжность, переменные), `.env.example`, `scripts/loadtest/README.md` (режим `RATE`). Проверка: описание совпадает с умолчаниями в коде.
- [x] 4.2 Полный прогон: `pytest -n 8`, `ruff check`, frontend lint/build.
- [x] 4.3 Нагрузочный тест на стенде (реальный объём, 4 процесса, OTel): ступени `RATE` выше прежнего порога 325 — нет зависания, лишнее получает 503, после ступени `/health` сразу отвечает. Результаты — в отчёте пользователю.

## Workflow follow-up

- Мерж `feature/api-overload-protection` в master через `--no-ff` и архив изменения — после подтверждения пользователя.

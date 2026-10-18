# Tasks

## 1. Задачи и идемпотентность

- [x] 1.1 `create_run` берёт `actor_for_user(admin.id)`. Проверка — тест в `tests/test_idempotency.py`: «Тот же ключ у другого администратора» (второй админ получает новый прогон); прежние сценарии идемпотентности зелёные
- [x] 1.2 `POST /runs/{id}/cancel` отвечает `RunDetail`, 404 — только без прогона; тип `cancelRun` во frontend. Проверка — тесты в `tests/test_parse_jobs.py`: «Отмена завершённого прогона» (200, `completed`, `cancel_requested` false), «Прогона нет» (404 `NOT_FOUND`), отмена в `queued` возвращает `cancelled`

## 2. Запросы

- [x] 2.1 `check_sort_needs_resume` в `backend/api/params.py` для списка, экспорта и `/runs/{id}/vacancies`. Проверка — тесты в `tests/test_query_validation.py`: «Сортировка по оценке без резюме» (422 на трёх эндпоинтах); с `resume_id` — 200
- [x] 2.2 Имена пресета и резюме обрезаются до проверки длины. Проверка — тест в `tests/test_query_validation.py`: «Имя из пробелов», «Название из пробелов» (создание и переименование — 422, ничего не меняется)

## 3. Ошибка конфигурации

- [x] 3.1 `CODE_CONFIG_INVALID` и обработчик `ConfigLoadError` в `backend/errors.py`; `/config` без своего перехвата; `RunOverrideError` в `backend/config_builder.py`, роут прогона — 400 только для него. Проверка — тесты в `tests/test_api_errors.py`: «Битый конфиг в метриках», «Битый конфиг при запуске прогона» (500 `CONFIG_INVALID`, прогона нет), «Конфигурация для UI при ошибке»; override вне каталога — по-прежнему 400 `VALIDATION`
- [x] 3.2 Frontend: код `CONFIG_INVALID` в типах/разборе ошибок, если коды перечислены. Проверка — `npm run lint` и `npm run build` зелёные

## 4. Документация и итоговая проверка

- [x] 4.1 README: ответ отмены прогона, `match_score` требует `resume_id`, код `CONFIG_INVALID`, идемпотентность по пользователю. Проверка — тексты обновлены
- [x] 4.2 `.venv/Scripts/python.exe -m pytest -n 8 -q`, `.venv/Scripts/ruff.exe check .` и `ruff format --check .` — зелёные
- [x] 4.3 `openspec validate align-api-contracts --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/align-api-contracts` в `master` через `--no-ff`.
- `/opsx:archive align-api-contracts`.

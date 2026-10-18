# Tasks

## 1. config.yaml и каталог моделей

- [x] 1.1 `_unknown_keys` в `tg_jobs_parser/config/loader.py`: обход сырого YAML по `AppConfig`, допустимые `ai.models` и `ai.fallback_model`, ошибка со списком путей. Проверка — тесты в `tests/test_read_caches.py`: «Опечатка в ключе» (оба пути в ошибке), «Старый ключ запасной модели» (загружается, единственная запасная), свободные ключи `dictionaries.specializations` принимаются, `config.yaml` проекта загружается
- [x] 1.2 Проверка `fallback_models`, `match_model` и `cover_letter_model` в `apply_ai_defaults_from_registry`. Проверка — тесты в `tests/test_llm_models_config.py`: «Модель матчинга вне каталога», «Запасная модель вне каталога» (ошибка называет поле и модель), «Конфигурация проекта» (все `resolved_models()` в каталоге)

## 2. Окружение и /health

- [x] 2.1 `Literal["inprocess", "rq"]` и нормализация для `*_JOB_BACKEND` в `backend/settings.py`. Проверка — тесты в `tests/test_settings_env.py`: «Опечатка в бэкенде» (`ValidationError` называет `LLM_JOB_BACKEND`, значения в тексте нет), «Регистр и пробелы» (`" RQ "` → `rq`), пусто → `inprocess`
- [x] 2.2 `/health` вызывает `load_config`. Проверка — тест в `tests/test_api_health.py`: «Конфигурация не загружается» (200, `degraded`, `config_loaded=false`, текста ошибки в ответе нет); исправный конфиг — `ok`

## 3. Валюта по умолчанию

- [x] 3.1 `apply_salary_rub_for_vacancy` подставляет `fx.default_currency_if_missing`. Проверка — тесты в `tests/test_fx_backfill.py`: «Зарплата без валюты» (200 000 при `fx.enabled: false`), «Валюта по умолчанию не рубль» (2 000 × 80), «Валюта не подставляется» (пусто → нет суммы); `salary_currency` в базе остаётся пустой

## 4. Документация и итоговая проверка

- [x] 4.1 `config.md` (неизвестные ключи, проверка моделей, `default_currency_if_missing`, `*_JOB_BACKEND`) и README (`/health`). Проверка — тексты обновлены
- [x] 4.2 `.venv/Scripts/python.exe -m pytest -n 8 -q`, `.venv/Scripts/ruff.exe check .` и `ruff format --check .` — зелёные
- [x] 4.3 `openspec validate reject-silent-config-errors --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/reject-silent-config-errors` в `master` через `--no-ff`.
- `/opsx:archive reject-silent-config-errors`.

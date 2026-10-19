# Заметки для агентов

## Проверка изменений

```bash
.venv/Scripts/python.exe -m pytest -q          # ~3 мин; нужен Postgres (см. ниже)
.venv/Scripts/python.exe -m pytest -n 8 -q     # параллельно (pytest-xdist), ~30 с
.venv/Scripts/python.exe -m pytest -n 8 -q --cov --cov-report=term --cov-report=html  # + покрытие → htmlcov/index.html
.venv/Scripts/ruff.exe check .                 # lint (конфиг в pyproject.toml)
cd frontend && npm run lint                    # oxlint (.oxlintrc.json)
cd frontend && npm run build                   # tsc -b + vite build
bash scripts/check_alerts.sh                   # при правке observability/alerts|prometheus.yml|alertmanager.yml (Docker)
```

- Тесты идут на Postgres 16, как прод. По умолчанию `tests/conftest.py` сам поднимает
  `postgres:16-alpine` через testcontainers (нужен запущенный Docker Desktop, `docker
  compose up` не нужен). Альтернатива — `TEST_DATABASE_URL` на готовый сервер с правом
  CREATEDB (например, compose-Postgres): тесты создают там свои базы `tgjobs_test_*` и
  удаляют их после прогона. Каждый xdist-воркер получает свою базу с миграциями, перед
  тестом — `TRUNCATE`. Фикстуры: `pg_db` (пустая схема + `DATABASE_URL`), `db_session`,
  `api_client`, `pg_empty_db` (база без миграций — для тестов самих миграций). FK
  проверяются: строки прогона/пользователя — через `new_parse_run` / `new_user` из conftest.
- CI — `.github/workflows/ci.yml`, только `workflow_dispatch` (ручной запуск, без
  триггеров на push/PR — намеренно, ради минут GitHub). Новая проверка в списке выше →
  добавить её и в workflow; тесты не должны требовать файлов вне git (`.env`, `sessions/`).
- Виртуальное окружение — `.venv`; вызывать `.venv/Scripts/python.exe -m pip`,
  а не `.venv/Scripts/pip.exe` (последний указывает на устаревший путь).
- Тесты офлайновые: `tests/conftest.py` подставляет `CONFIG_PATH` на конфиг с
  `fx.enabled: false`. Агрегаты (`_prepare_fx`) курсы из сети не тянут — только
  БД. `ensure_latest_rates` по умолчанию тоже предпочитает БД; HTTP к ЦБ —
  только если `exchange_rates` пуст (`prefer_db=False` — догрузить «сегодня»).
  Startup-sync и `refresh_rates_from_api` по-прежнему ходят в сеть. Тест,
  которому нужен курс, должен подменять `fx_repository.ensure_latest_rates`
  или класть строки в БД, а не полагаться на сеть.
- LLM live evals помечены `@pytest.mark.llm` — **не** входят в обычный
  `pytest -q`. Опционально:
  `pytest -m llm` или
  `.venv/Scripts/python.exe -m tg_jobs_parser.evals.run_extraction`.

## LLM hygiene (промпты / golden / evals)

- Версии: `EXTRACT_PROMPT_VERSION`, `MATCH_PROMPT_VERSION`,
  `COVER_LETTER_PROMPT_VERSION` в `tg_jobs_parser/ai/prompts.py`.
  Смысловое изменение промпта → **bump** версии: кэш extraction ключуется
  версией, а сохранённые оценки и письма с прежней версией становятся `stale`
  и пересчитываются при следующем запросе без `force`.
- Golden extraction: `evals/golden/extraction.jsonl`. Новый кейс — новая
  строка JSONL + запись в `evals/fixtures/extraction_outputs.json` (для
  offline CI).
- Scorer: `tg_jobs_parser/evals/score_extraction.py`.

## Миграции

Alembic-цепочка инкрементальная, `upgrade head` применяется при старте backend
(`backend/db/migrate.py`). Новые ревизии просто продолжают цепочку — без
`down -v`. БД — только Postgres: SQL пишется без веток под другие диалекты.
Constraint'ы (FK, индексы) — с **явными именами**. Ошибка миграции останавливает
старт (фолбека на `create_all` нет). `revision` id — **не длиннее 32 символов**
(дефолт `alembic_version.version_num`); `migrate.py` дополнительно расширяет колонку
до VARCHAR(64).

## Зависимости

- Источник версий — `poetry.lock`: Docker (`Dockerfile.backend`) ставит ровно его
  (`poetry export` → `pip install --require-hashes`). Изменили зависимости в
  `pyproject.toml` → `poetry lock` и коммит lock-файла; `.venv` держать в
  синхроне с lock (иначе тесты проверяют не то, что уедет в образ).
- OpenTelemetry: SDK `1.X` и contrib `0.Yb0` — только парой. Инструментация при
  несовместимой версии библиотеки молча не включается — это ловит
  `test_installed_libraries_satisfy_instrumentations` (`tests/test_tracing.py`).
- PowerShell 5 (`Set-Content -Encoding utf8`, `>`) пишет BOM/UTF-16 — TOML/JSON
  править через Python или edit-инструмент.

## Прочее

- Реализованные фичи описываются в `README.md`; из `ROADMAP.md` выполненный
  пункт удаляется (план — только про нереализованное). Настройки — `config.md`.
- Ветки: `feature/<name>` от `master`, мерж через `--no-ff`.
- Окончания строк — LF везде, закреплено в `.gitattributes` (`* text=auto eol=lf`):
  `core.autocrlf` машины на checkout / merge / stash больше не влияет. CRLF в
  рабочей копии — признак того, что файл записан мимо git (см. про PowerShell выше).
- Спеки и планы SDD — через OpenSpec: `openspec/` (`specs/` — текущее поведение,
  `changes/` — предложения и архив) **коммитится** вместе с кодом изменения.
  Команды/скиллы агентов (`.claude/`, `.agents/`, `.cursor/`) сгенерированы
  `openspec init` — обновлять через `openspec update`, а не руками.
- Артефакты плагина superpowers (`docs/superpowers/`, `docs/specs/`, `.superpowers/`) —
  только локально, **не коммитить**. В git они в `.gitignore`.

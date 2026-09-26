# Заметки для агентов

## Проверка изменений

```bash
.venv/Scripts/python.exe -m pytest -q          # ~10 с
.venv/Scripts/python.exe -m pytest -n 8 -q     # параллельно (pytest-xdist), ~6 с
cd frontend && npm run build                   # tsc -b + vite build
```

- Виртуальное окружение — `.venv`; вызывать `.venv/Scripts/python.exe -m pip`,
  а не `.venv/Scripts/pip.exe` (последний указывает на устаревший путь).
- Тесты офлайновые: `tests/conftest.py` подставляет `CONFIG_PATH` на конфиг с
  `fx.enabled: false`. Без этого агрегаты (`_prepare_fx` в
  `backend/db/vacancy_metrics.py`) на каждый вызов ходят за курсами в ЦБ —
  прогон растягивается с 10 с до ~5 мин. Тест, которому нужен курс, должен
  подменять `fx_repository.ensure_latest_rates`, а не полагаться на сеть.
- LLM live evals помечены `@pytest.mark.llm` — **не** входят в обычный
  `pytest -q`. Опционально:
  `pytest -m llm` или
  `.venv/Scripts/python.exe -m tg_jobs_parser.evals.run_extraction`.

## LLM hygiene (промпты / golden / evals)

- Версии: `EXTRACT_PROMPT_VERSION`, `MATCH_PROMPT_VERSION`,
  `COVER_LETTER_PROMPT_VERSION` в `tg_jobs_parser/ai/prompts.py`.
  Смысловое изменение промпта → **bump** версии (кэш ключуется версией).
- Golden extraction: `evals/golden/extraction.jsonl`. Новый кейс — новая
  строка JSONL + запись в `evals/fixtures/extraction_outputs.json` (для
  offline CI).
- Scorer: `tg_jobs_parser/evals/score_extraction.py`.

## Миграции

Alembic-цепочка инкрементальная, `upgrade head` применяется при старте backend
(`backend/db/migrate.py`). Новые ревизии просто продолжают цепочку — без
`down -v`. Колонки с FK добавлять через `op.batch_alter_table` с **именованным**
constraint: SQLite (тесты) не умеет `ALTER TABLE ADD COLUMN` с FK.

## Прочее

- Реализованные фичи описываются в `README.md`; из `ROADMAP.md` выполненный
  пункт удаляется (план — только про нереализованное). Настройки — `config.md`.
- Ветки: `feature/<name>` от `master`, мерж через `--no-ff`.

# Tasks

## 1. X-API-Key на резюме, пресетах и LLM-задачах

- [x] 1.1 Добавить `Depends(verify_api_key)` в `dependencies` роутеров `backend/api/routes/resumes.py` и `backend/api/routes/filters.py`, а в `backend/api/routes/llm_jobs.py` — на `GET /{job_id}` и `POST /{job_id}/cancel` (поток событий не трогать). Проверка — `ruff check` чистый, существующие тесты резюме, пресетов и LLM-задач зелёные
- [x] 1.2 Тест в `tests/test_api_access.py`: при заданном `API_KEY` создание резюме, список пресетов и чтение LLM-задачи без ключа — 401 `NOT_AUTHENTICATED`, резюме не создано; с верным ключом — 201/200. Проверка — тест зелёный
- [x] 1.3 Тест: при заданном `API_KEY` поток событий своей LLM-задачи по тикету из `/auth/sse-ticket` (полученному с ключом) открывается без `X-API-Key`. Проверка — тест зелёный

## 2. Предупреждение перед replace

- [x] 2.1 В `backend/db/transfer.py` — функция подсчёта оценок, писем и LLM-задач с `vacancy_id`, которые удалит каскад при `replace`. Проверка — тест в `tests/test_db_transfer.py`: при 2 оценках, 1 письме и 3 LLM-задачах с вакансией (и одной без) функция возвращает 2, 1, 3
- [x] 2.2 В `tg_jobs_parser/cli/db.py` подтверждение `replace` называет эти числа. Проверка — тест в `tests/test_cli.py` через `CliRunner`: в выводе числа оценок, писем и LLM-задач; ответ «n» — импорт не выполнен, данные на месте; `merge` — без подтверждения
- [x] 2.3 README: описание `replace` — очищаются таблицы дампа, каскадом удаляются оценки, письма и LLM-задачи с вакансией, подтверждение называет их число. Проверка — текст в README

## 3. Итоговая проверка

- [x] 3.1 `.venv/Scripts/python.exe -m pytest -n 8 -q` и `.venv/Scripts/ruff.exe check .` — зелёные
- [x] 3.2 `openspec validate harden-api-key-and-db-replace --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/harden-api-key-and-db-replace` в `master` через `--no-ff`.
- `/opsx:archive harden-api-key-and-db-replace`.

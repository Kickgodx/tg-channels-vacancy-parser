# Tasks

## 1. Схема

- [x] 1.1 Миграция `031_jobs_cancel_requested`: `cancel_requested BOOLEAN NOT NULL DEFAULT false` в `llm_jobs` и `hh_sync_jobs`; `downgrade` переводит `cancelled` в `error` / `failed` и удаляет колонки; поля в `backend/db/models.py`. Проверка — тест миграции на `pg_empty_db` (новый `tests/test_job_cancel.py`): строки, созданные на 030, после `upgrade head` имеют `cancel_requested = false`; `downgrade` до 030 не оставляет `cancelled`

## 2. LLM-задачи: запрос отмены и API

- [x] 2.1 `request_llm_job_cancel` в `llm_job_repository` (design, решение 2): терминальная не меняется, `queued` → `cancelled` условным UPDATE, `running` → флаг; `cancelled` в терминальных наборах, `set_llm_job_status` (fenced) и `purge_finished_llm_jobs`. Проверка — тесты в `tests/test_job_cancel.py`: queued → cancelled; running → флаг и статус `running`; done не меняется; ретенция удаляет старые `cancelled`; после отмены пары `find_active_llm_job` её не находит, и новая задача ставится
- [x] 2.2 `POST /llm-jobs/{id}/cancel` (владелец, чужая — 404), ответ — текущий `LlmJobOut` с `cancel_requested`; публикация SSE-события и best-effort `Job.cancel()` для RQ; SSE `/llm-jobs/{id}/events` завершается на `cancelled`. Проверка — API-тесты: отмена queued → `cancelled`; чужая → 404; повторная отмена и отмена `done` → 200 с текущим статусом; SSE отдаёт `cancelled` и закрывается

## 3. LLM-задачи: остановка в воркере

- [x] 3.1 Импорт: проверка `cancel_requested` перед каждым текстом в `import_service.run_import_job`, выход → `cancelled` (fenced) с итогом по обработанным текстам (design, решение 4). Проверка — тест в `tests/test_vacancy_import.py`: отмена после второго из пяти текстов → `cancelled`, в итоге два текста, их вакансии в базе, квота за остальные не списана
- [x] 3.2 Оценка и письмо: `is_cancelled` в `match_single` / `generate_single`, проверка после ответа модели перед upsert; флаг → без записи результата, задача → `cancelled`; запрос после upsert → `done`. Проверка — тесты в `tests/test_solo_match.py` и `tests/test_cover_letter.py`: отмена во время ответа модели оставляет прежнюю оценку / письмо в прежнем тоне; отмена после сохранения — `done`
- [x] 3.3 Recover: `_reclaim_running_llm_job` переводит зависший `running` с флагом в `cancelled` раньше проверок попыток (design, решение 5); метрики исхода `cancelled`. Проверка — тест: истёкший lease + флаг → `cancelled`, не `queued` и не `error`

## 4. HH sync: запрос отмены, API и остановка

- [x] 4.1 `request_hh_sync_job_cancel` в `hh_sync_job_repository` (как 2.1), `cancelled` в `TERMINAL_STATUSES`, `finish_hh_sync_job` принимает `cancelled`; `reclaim_expired_hh_sync_job` — флаг → `cancelled`. Проверка — тесты в `tests/test_hh_sync_jobs.py`: queued → `cancelled` и новый sync сразу ставится; running → флаг; истёкший lease + флаг → `cancelled`
- [x] 4.2 `POST /hh/sync-jobs/{id}/cancel` (admin), ответ — `HhSyncJobOut` с `cancel_requested`; статус `cancelled` в `HhSyncJobStatus`; SSE завершается на `cancelled`. Проверка — API-тесты: не-админ → 403; отмена `completed` → 200 со статусом `completed`; SSE отдаёт `cancelled`
- [x] 4.3 `run_hh_sync(..., should_stop)`: проверка перед страницей поиска и группой деталей (чтение флага не чаще раза в секунду), при остановке — дописать буфер, пропустить purge, вернуть частичный итог; воркер пишет `cancelled` с итогом (design, решение 4). Проверка — тесты в `tests/test_hh_sync_jobs.py`: отмена после первой группы → `cancelled`, детали следующих групп не запрашиваются, вакансии первой группы в базе, `listings_purged = 0`; отмена во время поиска → следующие страницы не запрашиваются

## 5. Frontend

- [x] 5.1 `api/client.ts`: `cancelLlmJob`, `cancelHhSyncJob`; `cancelled` — терминальный в ожидании LLM-задач и `HH_SYNC_TERMINAL`; `onAccepted(jobId)` в `postInteractiveLlm`; оценка/письмо на `cancelled` → `ApiError`, `importVacancies` — частичный итог с признаком отмены; `types/api.ts` синхронно с `schemas.py` и `hh.py`. Проверка — `npm run lint` и `npm run build` зелёные
- [x] 5.2 `ImportPage.tsx`: кнопка «Отменить» во время ожидания задачи и пометка «Импорт отменён» с итогом по обработанным текстам; `HhPage.tsx`: кнопка «Отменить» у активного sync, метка статуса `cancelled`, «Итог до отмены». Проверка — `npm run lint` и `npm run build`; вручную в UI: отмена импорта из нескольких текстов и отмена HH sync

## 6. Документация

- [x] 6.1 README (разделы ручного импорта и HH, таблица API): отмена, что сохраняется у отменённой задачи, эндпоинты `POST /llm-jobs/{id}/cancel` и `POST /hh/sync-jobs/{id}/cancel`; CLAUDE.md (durable-джобы): `cancel_requested` и `cancelled` у всех трёх типов. Проверка — описание совпадает с поведением тестов групп 2–4

## 7. Интеграционная проверка

- [x] 7.1 `.venv/Scripts/python.exe -m pytest -n 8 -q`, `.venv/Scripts/ruff.exe check .`, `.venv/Scripts/ruff.exe format --check .` — зелёные
- [x] 7.2 Оба бэкенда: отмена выполняемого импорта и HH sync проходит в `inprocess` (тесты групп 3–4) и через RQ-воркеры (кейсы в `tests/test_rq_worker_entrypoints.py`: queued-задача, отменённая до запуска воркера, не выполняется; импорт с флагом останавливается)
- [x] 7.3 `openspec validate llm-hh-job-cancel --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/llm-hh-job-cancel` в `master` через `--no-ff`.
- `/opsx:archive llm-hh-job-cancel`.

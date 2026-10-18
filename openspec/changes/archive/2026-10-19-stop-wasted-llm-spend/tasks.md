# Tasks

## 1. Возврат вызова квоты

- [x] 1.1 Миграция `alembic/versions/032_llm_jobs_quota_day.py` и поле `LlmJobRow.quota_day` (`DATE NULL`). Проверка — `pytest tests/test_db_pool.py` и тесты миграций зелёные, схема поднимается с нуля
- [x] 1.2 `enqueue` записывает `quota_day`, когда вызов списан; `refund_llm_job_quota(session, job_id)` в `backend/db/llm_job_repository.py` и уменьшение счётчика в `backend/db/llm_usage_repository.py` (условный `UPDATE`, не ниже 0). Проверка — тест: повторный возврат не уменьшает счётчик второй раз; администратор — `quota_day` пуст
- [x] 1.3 Возврат при отмене в очереди (`request_llm_job_cancel`) и в `_reclaim_running_llm_job` (`cancelled`, `error`). Проверка — тесты в `tests/test_llm_quota.py`: отмена `queued` и recover зависшей задачи возвращают вызов
- [x] 1.4 Возврат в воркере: нет резюме или вакансии, ошибка модели, отмена до ответа; `LlmJobCancelled(model_answered=True)` из `match_single` и `generate_single` — без возврата. Проверка — тесты: 503 модели → вызов возвращён; отмена после ответа → нет
- [x] 1.5 Возврат при сбое постановки в `LlmJobRunner.enqueue` и возврат в сутки списания. Проверка — тесты: сбой dispatch → вызов возвращён; задача со `quota_day` вчера → уменьшен вчерашний счётчик
- [x] 1.6 Удаление резюме возвращает вызовы задач `queued`, не трогая `running`. Проверка — тест в `tests/test_resume_matching.py`

## 2. Бюджет при постановке

- [x] 2.1 `GlobalRateGate.daily_used`, Redis-реализация, локальный `used()` и `interactive_daily_budget_exhausted` в `tg_jobs_parser/ai/rate_limit.py`. Проверка — тест в `tests/test_llm_quota.py`: после исчерпания бюджета функция возвращает True, через Redis и при его сбое
- [x] 2.2 `enqueue` до списания квоты отказывает `LlmDailyQuotaError` с текстом про общий бюджет. Проверка — тест: оценка новой пары при исчерпанном бюджете → 429 `LLM_QUOTA_EXCEEDED`, задачи нет, квота не списана; сохранённая свежая оценка → 200

## 3. Импорт и пороги

- [x] 3.1 `run_import_job` возвращает вызов, если `stats.calls` не вырос. Проверка — тест в `tests/test_vacancy_import.py`: ответ из кэша → счётчик квоты не изменился
- [x] 3.2 `enrich` поднимает `vacancy_substantive_score` до `parser.vacancy_min_substantive_score` после ответа «вакансия». Проверка — тесты в `tests/test_ai_enricher_mock.py`: пост с содержательной оценкой 0 при минимуме 1 проходит `apply_post_filters`; пост с исключающим словом — нет

## 4. Документация и итоговая проверка

- [x] 4.1 README и `config.md`: возврат квоты, отказ при исчерпанном бюджете, поведение порогов с LLM. Проверка — тексты обновлены
- [x] 4.2 `.venv/Scripts/python.exe -m pytest -n 8 -q` и `.venv/Scripts/ruff.exe check .` — зелёные
- [x] 4.3 `openspec validate stop-wasted-llm-spend --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/stop-wasted-llm-spend` в `master` через `--no-ff`.
- `/opsx:archive stop-wasted-llm-spend`.

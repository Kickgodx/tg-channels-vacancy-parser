# Tasks

## 1. Схема

- [x] 1.1 Миграция `030_llm_results_requested_model`: `requested_model VARCHAR(128) NULL` и `prompt_version VARCHAR(32) NULL` в `vacancy_matches` и `cover_letters`, backfill `requested_model = model`; `downgrade` удаляет колонки; поля в `backend/db/models.py`. Проверка — тест миграции на `pg_empty_db` (новый `tests/test_llm_result_freshness.py`): строки, созданные на 029, после `upgrade head` имеют `requested_model == model` и `prompt_version IS NULL`

## 2. Запись и правило актуальности

- [x] 2.1 `upsert_vacancy_match` / `upsert_cover_letter` принимают и пишут `requested_model` и `prompt_version`; `match_service.match_single` и `cover_letter_service.generate_single` передают `cfg.ai.resolved_match_model()` / `resolved_cover_letter_model()` и `MATCH_PROMPT_VERSION` / `COVER_LETTER_PROMPT_VERSION`. Проверка — тест в `tests/test_solo_match.py` и `tests/test_cover_letter.py`: при ответе fallback-модели `model` = fallback, `requested_model` = настроенная, версия = константа
- [x] 2.2 Модуль `backend/services/llm_result_freshness.py` с `match_is_stale` и `cover_letter_is_stale` (design, решение 3). Проверка — параметризованные тесты в `tests/test_llm_result_freshness.py`: смена модели → stale; смена версии → stale; fallback при той же настройке → не stale; NULL версия → не stale по версии; NULL `requested_model` → сравнение по `model`; у письма — цепочка `cover_letter_model` → `match_model` → `model`

## 3. Оценка соответствия: API

- [x] 3.1 `POST /resumes/{id}/match/{vacancy_id}` без `force`: 200 только для неустаревшей оценки, иначе 202 и пересчёт (design, решение 4). Проверка — тест в `tests/test_resume_matching.py`: свежая → 200 без вызова LLM; после смены `ai.match_model` → 202, по завершении одна строка с новой `requested_model`; после подмены `MATCH_PROMPT_VERSION` → 202
- [x] 3.2 `VacancyMatchOut.stale` в `GET`/`POST` оценки и в итоге LLM-задачи. Проверка — тест: `GET` устаревшей → `stale: true`; итог завершённой задачи и `GET` после неё → `stale: false`

## 4. Письма: API

- [x] 4.1 `POST .../cover-letter/...` без `force`: 200 только если письмо не устарело и тон совпадает, иначе 202 (design, решение 4). Проверка — тест в `tests/test_cover_letter.py`: тот же тон → 200; другой тон → 202 и после задачи одно письмо в новом тоне; смена `ai.cover_letter_model` (и при пустом — `ai.match_model`) → 202
- [x] 4.2 `CoverLetterOut.stale` в `GET`/`POST` письма и в итоге LLM-задачи. Проверка — тест: `GET` письма прежней модели → `stale: true`; после перегенерации → `stale: false`

## 5. Список вакансий и UI

- [x] 5.1 `VacancyListItem.match_stale` в `GET /vacancies` с `resume_id`: репозиторий получает текущую конфигурацию матчинга от роута (design, решение 5); колонка `match_score` и сортировка не меняются. Проверка — тест в `tests/test_resume_matching.py`: после смены модели элемент списка `match_stale: true`, `match_score` и порядок при `sort_by=match_score` прежние; без оценки — `match_stale: null`
- [x] 5.2 Frontend: `stale` в `VacancyMatch` и `CoverLetter`, `match_stale` в `VacancyListItem` (`frontend/src/types/api.ts`, синхронно с `backend/schemas.py`); в `VacancyModal.tsx` подпись у устаревших оценки и письма, начальная оценка из списка несёт `match_stale`. Проверка — `npm run lint` и `npm run build` зелёные; вручную в UI после смены `ai.match_model` карточка показывает пометку

## 6. Документация

- [x] 6.1 README (разделы «Матчинг резюме» и «Сопроводительные письма»): кэш учитывает запрошенную модель, версию промпта и тон; `stale` и пометка в карточке; AGENTS.md (LLM hygiene): bump `MATCH_PROMPT_VERSION` / `COVER_LETTER_PROMPT_VERSION` помечает оценки/письма устаревшими. Проверка — описание совпадает с поведением тестов групп 3–5

## 7. Интеграционная проверка

- [x] 7.1 `.venv/Scripts/python.exe -m pytest -n 8 -q`, `.venv/Scripts/ruff.exe check .`, `.venv/Scripts/ruff.exe format --check .` — зелёные
- [x] 7.2 Оба бэкенда LLM-задач: пересчёт устаревшей оценки проходит и в `inprocess` (тесты 3.1), и через RQ-воркер (кейс в `tests/test_rq_worker_entrypoints.py`: задача матча пишет `requested_model` и `prompt_version`)
- [x] 7.3 `openspec validate llm-result-model-cache --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/llm-result-model-cache` в `master` через `--no-ff`.
- `/opsx:archive llm-result-model-cache`.

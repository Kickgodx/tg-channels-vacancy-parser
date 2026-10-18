# Tasks

## 1. Сбой запуска LLM в primary → прогон failed

- [x] 1.1 Объявить `LlmUnavailableError(RuntimeError)` рядом с `AIEnrichmentService` в `tg_jobs_parser/ai/enricher.py`; проверка — импортируется из `parse_runner` и `import_service` без циклов (`pytest tests/test_ai_enricher_mock.py -q` зелёный)
- [x] 1.2 В `run_parse` при исключении конструктора `AIEnrichmentService` и `ai.mode == "primary"` бросать `LlmUnavailableError` с причиной и подсказкой (design, решение 1); `assist` оставить с предупреждением. Проверка — новые тесты в `tests/test_streaming_parse.py`: в `primary` исключение с исходной причиной и словами `primary`/`assist` в тексте, фейковый fetcher не вызван ни разу; в `assist` прогон доходит до конца, предупреждение «AI не запущен» в `parse_warnings`
- [x] 1.3 Тест уровня воркера в `tests/test_parse_job_resilience.py`: `run_parse_job` с конфигом `primary`, где конструктор `AIEnrichmentService` падает, → строка `parse_runs` `failed`, `error_message` с причиной, `posts_fetched == 0`, `attempts == 1`, нет строк `vacancies` и `telegram_channel_checkpoints` от прогона

## 2. Потолок `ai.max_posts_per_run`

- [x] 2.1 В `AIEnrichmentService.enrich` перенести проверку потолка после чтения кэша и объединить её с `stats.calls += 1` в одну секцию под `stats._lock`; при потолке `skipped_limit += 1`, `primary` → `None`, `assist` → вакансия правил (design, решение 2). Проверка — тесты в `tests/test_ai_enricher_mock.py`: `primary` после потолка → `None` и запись в кэш не вызвана; `assist` → вакансия с `extraction_source == "rules"`; ответ из кэша после потолка используется в обоих режимах; при `concurrency` потоков вызовов не больше потолка
- [x] 2.2 В `parse_runner`: `_llm_posts_lost` истинно в `primary` при `skipped_limit > 0`; `incomplete_reason` для потолка между проверкой квоты и доли ошибок (design, решение 3). Проверка — тест в `tests/test_streaming_parse.py`: `primary`, потолок 2, 5 постов без кэша → `incomplete_reason` упоминает `ai.max_posts_per_run` и 3 поста; 3 из 5 в кэше → причины нет
- [x] 2.3 Чекпоинты при потолке: тест в `tests/test_channel_checkpoints.py` — канал, полностью разобранный до первого пропущенного поста, сдвинут; канал с пропущенным постом — нет; при финализации `partial` чекпоинты не пишутся
- [x] 2.4 Документация: в `config.md` у `max_posts_per_run` и в таблице режимов — что в `primary` посты сверх потолка не сохраняются и прогон `partial`, что ответы из кэша потолок не расходуют и что потолок в `primary` — ограничитель расходов; в README в «Самовосстановлении джоб» дополнить пункт про `partial`. Проверка — описание совпадает с поведением из тестов 2.1–2.3

## 3. Импорт

- [x] 3.1 В `run_import_job` при сбое конструктора `AIEnrichmentService` (AI включён) бросать `LlmUnavailableError` вместо предупреждения и отката на правила (design, решение 4). Проверка — тест в `tests/test_vacancy_import.py`: задача `error`, `error_message` с причиной, текст не в `vacancies`, `llm_user_usage` пуст; ретрай с частично записанным `result` → `error`, `result.items` сохранены
- [x] 3.2 В `_enrich_status` учитывать прирост `stats.skipped_limit`: в `primary` → статус текста `error` с сообщением о лимите `ai.max_posts_per_run`. Проверка — тест в `tests/test_vacancy_import.py`: `primary`, потолок 1, два новых текста → первый `created`, второй `error` с упоминанием лимита и не сохранён
- [x] 3.3 Проверить, что откат на правила при исчерпанной квоте не изменился: существующий `test_import_falls_back_to_rules_when_daily_quota_ends` зелёный; в README (раздел импорта) отметить, что сбой запуска LLM завершает задачу ошибкой, а квота — по-прежнему откатом на правила

## 4. Интеграционная проверка

- [x] 4.1 Полный прогон проверок: `.venv/Scripts/python.exe -m pytest -n 8 -q`, `.venv/Scripts/ruff.exe check .`, `.venv/Scripts/ruff.exe format --check .` — всё зелёное
- [x] 4.2 Оба бэкенда джоб: тесты 1.3 и 3.1 проходят и в `inprocess`, и через RQ-воркер (`tests/test_rq_worker_entrypoints.py` — добавить кейс импорта со сбоем запуска LLM: задача `error`)
- [x] 4.3 `openspec validate primary-llm-unavailable --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/primary-llm-unavailable` в `master` через `--no-ff`.
- `/opsx:archive primary-llm-unavailable` — требования переедут в `openspec/specs/`.

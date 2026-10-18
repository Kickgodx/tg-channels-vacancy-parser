# Design

## Context

Мотивация — в `proposal.md`, требования — в `specs/*`. Текущее устройство:

- Образец — отмена прогона: `parse_runs.cancel_requested`;
  `request_parse_run_cancel` переводит `queued` сразу в `cancelled`, у `running`
  только ставит флаг; воркер поллит флаг отдельным потоком (0,5 с) и
  останавливается; `_reclaim_values` превращает зависший `running` с флагом в
  `cancelled`; queued RQ-джоба снимается `Job.cancel()` best-effort.
- `llm_jobs` и `hh_sync_jobs` устроены так же (claim `queued → running` с lease,
  heartbeat, fenced-запись финального статуса по `lease_owner`, recover по
  истёкшему lease и `queued` без живой RQ-джобы), но флага и статуса отмены нет.
  Статусы: LLM — `queued|running|done|error`, HH — `queued|running|completed|failed`.
  CHECK-ограничений на статус в БД нет.
- Активность: `uq_llm_jobs_one_active_pair` и `uq_hh_sync_jobs_one_active` —
  partial unique по `status IN ('queued','running')`; `find_active_llm_job`
  переиспользует активную задачу пары.
- Где задача может остановиться:
  - импорт (`import_service.run_import_job`) — цикл по текстам, итог по тексту
    коммитится `patch_llm_job_result`;
  - HH (`hh_sync_service.run_hh_sync`) — поиск страницами
    (`_collect_shorts` → `client.iter_vacancy_pages`), затем цикл по группам с
    `report(...)` после каждой и буфером `flush_buffer`, затем purge listings;
  - оценка / письмо (`match_single` / `generate_single`) — один вызов LLM с
    fallback-цепочкой, затем upsert результата.
- Квота LLM: оценка и письмо списывают вызов при постановке, импорт — перед
  каждым текстом.
- Frontend: `postInteractiveLlm` прячет `job_id` (202 → ожидание через SSE/poll);
  терминальные статусы — `done|error` (LLM) и `completed|failed` (HH).
  Страница HH находит активный sync после перезагрузки (`hhSyncJobs(1)`), страница
  импорта знает о задаче только пока ждёт её мутация.

## Goals / Non-Goals

**Goals:**
- Одна схема отмены для всех трёх типов durable-джоб, одинаковая в `rq` и
  `inprocess`.
- Флаг в БД — источник истины: отмена переживает рестарт API и воркера.

**Non-Goals:**
- Прерывание HTTP-запроса к LLM или HH на середине.
- Возврат квоты LLM за отменённую оценку или письмо (вызов уже списан при
  постановке; за импорт неразобранные тексты квоту и так не тратят).
- Кнопка отмены для оценки и письма в UI (решение пользователя) — только API.
- Подхват идущего импорта страницей после ухода с неё и возврата.
- Изменение отмены прогонов парсинга.

## Decisions

### 1. Флаг + статус, как у прогонов

Миграция `031_jobs_cancel_requested`: `cancel_requested BOOLEAN NOT NULL DEFAULT
false` в `llm_jobs` и `hh_sync_jobs`. Новый статус `cancelled` (без
ограничений в БД). `cancelled` — терминальный: добавляется в терминальные наборы
(SSE LLM-задач, `hh_sync_job_repository.TERMINAL_STATUSES`), в ретенцию
(`purge_finished_llm_jobs`, `purge_old_hh_sync_jobs` через `TERMINAL_STATUSES`) и
в `Literal` статусов схем. Partial unique индексы не меняются: `cancelled` вне
`queued/running`, поэтому новый запрос пары или новый sync сразу возможен.

Альтернатива — отдельный статус `cancelling` для `running` с флагом: отвергнута,
у прогонов его нет, а `cancel_requested` в ответе уже это сообщает.

### 2. Запрос отмены в репозитории

`request_llm_job_cancel` / `request_hh_sync_job_cancel` — по образцу
`request_parse_run_cancel`: терминальная задача не меняется; `queued` → сразу
`cancelled` (`finished_at`, снять lease); `running` → только флаг. Перевод
`queued → cancelled` — условным UPDATE (`WHERE status = 'queued'`), чтобы не
перетереть одновременный claim: проиграв claim'у, задача остаётся `running` с
флагом и останавливается воркером.

Сервисный слой после `queued → cancelled` публикует событие статуса (SSE) и
best-effort снимает RQ-джобу (`Job.cancel()`), как `run_service.cancel_run`.
Даже без снятия RQ-джобы воркер её не выполнит: claim требует `queued`.

### 3. Эндпоинты и ответ

`POST /llm-jobs/{id}/cancel` (`get_current_user`, чужая — 404) и
`POST /hh/sync-jobs/{id}/cancel` (`require_admin`). Ответ — `200` с текущим
`LlmJobOut` / `HhSyncJobOut` и в том числе для уже завершённой задачи: UI в гонке
«задача только что закончилась» получает итог, а не ошибку. Обе схемы получают
`cancel_requested: bool`.

Альтернатива — 404/409 для нетерминальных, как `POST /runs/{id}/cancel`:
отвергнута ради идемпотентности; отмену прогонов не трогаем.

### 4. Как воркер узнаёт об отмене

Короткое чтение флага в точках проверки, а не отдельный поток:

- **Импорт** — чтение `cancel_requested` перед каждым текстом (текстов ≤ 50, у
  каждого и так есть несколько коротких сессий). Флаг → выход из цикла, итог уже
  записан по обработанным текстам.
- **HH sync** — `run_hh_sync(..., should_stop)`: колбэк проверяется перед каждой
  страницей поиска и перед каждой группой деталей. Флаг читается из БД не чаще
  раза в секунду (как запись прогресса). При остановке буфер дописывается
  (`flush_buffer(force=True)`: детали уже оплачены запросами к HH), purge listings
  пропускается, функция возвращает частичный итог; воркер пишет `cancelled` с
  этим итогом. Остановка — возврат, а не исключение: путь сохранения буфера и
  освобождения advisory lock остаётся общим.
- **Оценка / письмо** — `match_single` / `generate_single` получают
  `is_cancelled`: проверка после ответа модели, перед upsert. Флаг → результат не
  записывается, задача → `cancelled`. Отмена, пришедшая после upsert, опоздала:
  задача завершается `done` (спеки).

Финальная запись `cancelled` — fenced по `lease_owner`, как `done`/`error`
(`set_llm_job_status` / `finish_hh_sync_job` принимают `cancelled`).

Альтернатива — поток-поллер, как у прогонов: отвергнута, у этих задач нет
долгого блокирующего шага без точек проверки, который поток мог бы прервать
(HTTP к LLM всё равно не прерывается).

### 5. Recover

`_reclaim_running_llm_job` и `reclaim_expired_hh_sync_job`: зависший `running` с
`cancel_requested` → `cancelled` раньше проверок попыток и requeue (как
`_reclaim_values` у прогонов). Recover `queued` без живой RQ-джобы `cancelled` не
видит — он терминальный.

### 6. Метрики

Исход `cancelled` в `record_llm_job` / `record_llm_worker_job` и
`record_hh_sync_finished`; `llm_jobs_by_status` получает статус сам (группировка
по БД). Новых метрик нет.

### 7. Frontend

- `api/client.ts`: `cancelLlmJob(id)`, `cancelHhSyncJob(id)`; `cancelled` —
  терминальный в `pollLlmJob`, SSE-ожидании и `HH_SYNC_TERMINAL`.
  `postInteractiveLlm` получает колбэк `onAccepted(jobId)`; на `cancelled` для
  оценки и письма — `ApiError` «Задача отменена».
- `importVacancies` на `cancelled` резолвится частичным `import_result` с
  признаком отмены (а не ошибкой) — страница показывает итог по обработанным
  текстам.
- `ImportPage.tsx`: кнопка «Отменить», пока ждём задачу (id — из `onAccepted`);
  пометка «Импорт отменён». `HhPage.tsx`: кнопка «Отменить» у активного sync,
  `STATUS_LABEL.cancelled`, итог отменённого sync — как у `failed` («Итог до
  отмены»).
- `types/api.ts`: статусы и `cancel_requested` — синхронно с `schemas.py` / `hh.py`.

## Risks / Trade-offs

- [Отмена оценки во время ответа модели тратит вызов и квоту, результат
  выбрасывается] → это выбранное поведение; UI кнопки для оценки не даёт.
- [Текст импорта, который уже разбирается, доводится до конца] → остановка
  между текстами; задержка — один вызов LLM.
- [HH: одна группа деталей или страница поиска после запроса отмены ещё
  выполняется; флаг читается раз в секунду] → задержка остановки — секунды.
- [Гонка «cancel queued» и claim] → условный UPDATE (решение 2): победитель один,
  проигравшая отмена остаётся флагом и срабатывает в воркере.
- [inprocess: задача уже в пуле потоков] → claim не пройдёт для `cancelled`,
  поток сразу выходит.

## Migration Plan

Миграция `031` применяется при старте backend (`upgrade head`), обратима
(`downgrade` удаляет колонки; строки `cancelled` перед этим переводятся в
`error` / `failed`, чтобы старый код их понимал). Откат кода без отката миграции
безопасен: колонка с DEFAULT, старый код её не пишет.

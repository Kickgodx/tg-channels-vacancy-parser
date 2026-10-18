# Design

## Context

Мотивация — в `proposal.md`. Наблюдения из baseline: этап 2 (`baseline-ingestion`,
Open Questions 2), этап 3 (`baseline-career`, 1, 2 и 4), этап 4
(`baseline-catalog`, 8).

- Квота: `reserve_user_llm_calls` (`backend/services/llm_job_service.py`)
  атомарно увеличивает `llm_user_usage.calls` за сегодня при постановке. Задача не
  помнит, списан ли за неё вызов (администратор и квота `0` — без списания).
- Завершения без ответа модели: отмена в очереди (`request_llm_job_cancel`), ошибки
  и отмена в воркере (`backend/workers/llm_tasks.py`), зависшие задачи
  (`_reclaim_running_llm_job` в `backend/db/llm_job_repository.py`), сбой
  постановки (`LlmJobRunner.enqueue`). Отмена после ответа модели — `LlmJobCancelled`
  из `match_single` и `generate_single` после вызова клиента.
- Бюджет: `OpenRouterRateLimiter._take_daily_budget` списывает запрос через
  `GlobalRateGate.take_daily` (Redis) или локальный счётчик процесса; посмотреть
  остаток без списания нельзя.
- Импорт: `run_import_job` списывает вызов до `_enrich_status`; кэш проверяется
  внутри `AIEnrichmentService.enrich`, вызов модели виден по `stats.calls`.
- Пороги: `AIEnrichmentService.enrich` после ответа поднимает `vacancy_score` до
  `parser.vacancy_score_threshold`, а `vacancy_substantive_score` — нет;
  `apply_post_filters` проверяет оба.

## Goals / Non-Goals

**Goals:**
- Пользователь не теряет квоту на задачах, по которым модель ничего не вернула.
- Исчерпанный бюджет — отказ до списания квоты.
- Ни одного оплаченного вызова, результат которого заведомо отбрасывается
  порогом правил.

**Non-Goals:**
- Возврат квоты задачам, созданным до миграции, — у них нет отметки списания.
- Проверка при постановке отключения LLM по лимиту провайдера: это состояние
  процесса воркера, API его не видит.
- Ошибка после ответа модели (сбой записи результата в базу) отдельно не
  различается и тоже возвращает вызов: пользователь не получил результата, а
  такой сбой редок.

## Decisions

### 1. Отметка списания — колонка `llm_jobs.quota_day`

При списании вызова задача получает `quota_day` — UTC-дату списания; без
списания колонка пуста. Возврат — один условный `UPDATE llm_jobs SET
quota_day = NULL WHERE id = :id AND quota_day IS NOT NULL RETURNING user_id,
quota_day`, затем уменьшение `llm_user_usage.calls` за этот день (не ниже 0).
Условный `UPDATE` делает возврат однократным при гонке воркера, отмены и recover.
Альтернатива — флаг в `params` (JSON) — без миграции, но условие однократности
в JSON хрупче.

### 2. Точки возврата

- отмена `queued` → `cancelled` в `request_llm_job_cancel`;
- в воркере — отсутствие резюме или вакансии, любая ошибка и `LlmJobCancelled`
  без признака ответа модели; `match_single` и `generate_single` поднимают
  `LlmJobCancelled(..., model_answered=True)`;
- `_reclaim_running_llm_job` при исходах `cancelled` и `error`;
- сбой постановки в `LlmJobRunner.enqueue`;
- удаление резюме: возврат по задачам `queued` этого резюме до каскада. Задачи
  `running` не возвращаются: вызов модели уже идёт.

### 3. Остаток бюджета — новый метод порта

`GlobalRateGate.daily_used(key) -> int` (Redis — `GET` счётчика суток), локальный
счётчик — свой `used()`. Функция ядра `interactive_daily_budget_exhausted(budget)`
читает порт, а при его ошибке или отсутствии — локальный счётчик, как при
списании. `enqueue` проверяет её до списания квоты, после поиска активной задачи
пары, и поднимает `LlmDailyQuotaError` с текстом про общий бюджет: роуты уже
отвечают на неё 429 `LLM_QUOTA_EXCEEDED`.

### 4. Кэш в импорте — возврат по `stats.calls`

Вызов по-прежнему списывается до `_enrich_status`; если `ai_service.stats.calls`
не вырос, вызов возвращается сразу. Альтернатива — проверка кэша до списания —
дублирует ключ кэша вне `AIEnrichmentService`.

### 5. Вердикт LLM — подъём substantive-оценки

После ответа «вакансия» `enrich` поднимает и `vacancy_substantive_score` до
`parser.vacancy_min_substantive_score`, как уже делает с `vacancy_score`.
Альтернатива — пропуск порогов в `apply_post_filters` по `extraction_source` —
разносит одно правило по двум модулям.

## Risks / Trade-offs

- [Каталог получит посты, которые раньше отсеивались порогом] → это осознанное
  решение: вердикт LLM главнее эвристики правил; пост-фильтры остаются.
- [Миграция на большой таблице `llm_jobs`] → добавление NULL-колонки без default в
  Postgres — изменение метаданных, без перезаписи таблицы.
- [Числа квоты при гонке возврата и нового списания] → оба — атомарные
  `UPDATE`; счётчик не опускается ниже 0.

## Migration Plan

Миграция `032_llm_jobs_quota_day` добавляет `llm_jobs.quota_day DATE NULL`.
Откат — удаление колонки; код без неё не стартует, поэтому откат кода и миграции
вместе.

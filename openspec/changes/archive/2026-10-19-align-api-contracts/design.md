# Design

## Context

Это наблюдения baseline этапов 1, 4 и 5 (`baseline-invariants`, `baseline-catalog`,
`baseline-platform`).

- `create_run` (`backend/api/routes/runs.py`) берёт `actor =
  idempotency.ACTOR_API`. HH и импорт берут `idempotency.actor_for_user(user.id)`.
- `cancel_run` возвращает `{"ok": true, "run_id"}`, а если
  `request_parse_run_cancel` отдал `None` (прогона нет или он завершён) — 404.
  `cancel_llm_job` и `cancel_hh_sync_job` возвращают текущее состояние задачи.
- `list_vacancies` принимает `sort_by=match_score` при `resume_id=None`, а
  `vacancy_repository` сортирует по оценке, только если `resume_id` задан.
  Экспорт и `/runs/{id}/vacancies` принимают `match_score`, хотя `resume_id` у
  них нет вовсе.
- `SavedFilterCreate.name` и `ResumeCreate/Update.name` проверяют
  `min_length=1` до обрезки, а роуты делают `.strip()` уже после проверки.
- `ConfigLoadError`:
  - `/config` превращает его в `HTTPException(500)`, обработчик ставит код
    `INTERNAL` и `retryable: false`;
  - `/fx/*`, `/hh/*`, метрики (`_prepare_fx`) и остальные его не ловят, поэтому
    срабатывает `unhandled_exception_handler`: `INTERNAL`, `retryable: true`;
  - `POST /runs` отдаёт любую `ConfigLoadError` из `build_config` как 400
    `VALIDATION`, включая сломанный серверный файл.

Решения пользователя (2026-10-19), все по рекомендованным вариантам:
- ключ свой у каждого администратора;
- отмена отвечает 200 с состоянием прогона;
- `match_score` без резюме — 422;
- единый код `CONFIG_INVALID`.

Пустые имена исправляются без вопроса: у этого пункта один разумный вариант.

## Goals / Non-Goals

**Goals:**
- Отмена, идемпотентность и ошибки конфигурации устроены одинаково для всех
  видов задач и эндпоинтов.
- Запрос, который нельзя выполнить как задано, получает 422, а не тихую
  подмену.

**Non-Goals:**
- Проверка `match_score` без `resume_id` в параметрах сохранённого пресета.
  Пресеты, сохранённые раньше, читаются как `VacancyQuery`, и новая проверка
  там сломала бы `GET /filters`. Проверка живёт в обработчиках списка и
  экспорта.
- Перенос ключей идемпотентности с владельцем `api` на конкретных
  администраторов: они истекают за 24 ч.
- Смена кода ошибки 404 у отмены LLM-задач и HH (уже согласованы).

## Decisions

### 1. Ключ прогона — `actor_for_user(admin.id)`

Меняется одна строка в `create_run`. Константа `ACTOR_API` больше не
используется и удаляется, если на неё никто не ссылается.

### 2. Отмена прогона отвечает `RunDetail`

`run_service.cancel_run` различает «нет прогона» и «уже завершён». Роут после
попытки отмены читает прогон и отдаёт его тем же построителем, что
`GET /runs/{id}`. Ответ объявляется как `response_model=RunDetail`. Отмена
завершённого прогона ничего не меняет в строке: флаг `cancel_requested` не
ставится, как и раньше. Во frontend `cancelRun` получает тип `RunDetail`;
`RunPage` использует только успех мутации и перезапрос прогона.

### 3. `match_score` требует `resume_id`

В `backend/api/params.py` появляется `check_sort_needs_resume(sort_by,
resume_id)`: при `match_score` без резюме — `http_error(422, VALIDATION,
"Сортировка по оценке (match_score) требует resume_id")`. Её вызывают
`list_vacancies`, `export_vacancies_xlsx` (`resume_id` там нет, поэтому
`match_score` всегда 422) и `/runs/{id}/vacancies`.

### 4. Имена проверяются после обрезки

`SavedFilterCreate.name`, `ResumeCreate.name` и `ResumeUpdate.name` получают
before-валидатор `str.strip`. После него `min_length=1` и `max_length=200`
проверяют уже обрезанное имя. `.strip()` в роутах становится лишним и
удаляется.

### 5. `CONFIG_INVALID` — общий обработчик исключения

- В `backend/errors.py` появляется код `CODE_CONFIG_INVALID = "CONFIG_INVALID"`
  и обработчик `ConfigLoadError`: 500, `message` — текст ошибки загрузки,
  `retryable: false`. Обработчик регистрируется в
  `register_exception_handlers`.
- `/config` и `/config/defaults` больше не ловят ошибку сами.
- Сообщение об ошибке видит только залогиненный пользователь: всё, кроме
  `/health` и `/auth/*`, требует входа, а `/health` ловит ошибку сам и текст не
  отдаёт.
- В `POST /runs` ошибки override отделяются от ошибок файла. `build_config`
  загружает базу через `load_config`, и её `ConfigLoadError` уходит в общий
  обработчик. Отказ по модели override поднимает подкласс
  `RunOverrideError(ConfigLoadError)`, который роут превращает в 400
  `VALIDATION`. Ключ идемпотентности освобождается в обоих случаях: это уже
  делает `except Exception: abandon`.
- Во frontend новый код добавляется в перечень кодов ошибок, если такой
  перечень есть.

## Risks / Trade-offs

- [Внешний клиент читал `{ok, run_id}` из ответа отмены] → API используют
  только UI проекта; `run_id` есть и в `RunDetail` (как `id`).
- [Ссылка с `sort_by=match_score` без резюме перестанет работать] → UI её не
  строит: при снятии резюме он сбрасывает и сортировку.
- [Текст ошибки конфигурации с путями уходит пользователю] → так уже работает
  `/config`; отвечают только залогиненным.

## Migration Plan

Миграций нет. Откат — откат коммита.

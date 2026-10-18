# Proposal

## Why

Baseline этапов 1, 4 и 5 нашёл места, где API ведёт себя по-разному в похожих
случаях или молча подменяет запрос:
- `Idempotency-Key` прогона общий для всех администраторов (владелец `api`).
  Тот же ключ другого администратора возвращает чужой ответ, а у HH и импорта
  ключ свой у каждого пользователя.
- Отмена завершённого прогона даёт 404. У LLM-задач и HH sync в этом случае 200
  с текущим состоянием задачи.
- `sort_by=match_score` без `resume_id` молча сортирует по дате.
- Имя пресета или резюме из одних пробелов проходит проверку длины и
  сохраняется пустым.
- Битый `config.yaml` даёт 500 по-разному. `/config` отвечает `INTERNAL` с
  `retryable: false`, остальные эндпоинты — необработанной ошибкой с
  `retryable: true`. Запуск прогона отвечает 400 `VALIDATION`, как будто ошибся
  клиент.

## What Changes

- `Idempotency-Key` у `POST /runs` принадлежит администратору, как у HH sync и
  импорта.
- **BREAKING** `POST /runs/{id}/cancel` отвечает 200 с состоянием прогона
  (`RunDetail`), в том числе для уже завершённого. 404 — только если прогона
  нет. Прежний ответ `{ok, run_id}` уходит; UI проверяет только успех.
- **BREAKING** `sort_by=match_score` без `resume_id` даёт 422 `VALIDATION` в
  списке вакансий, экспорте и списке вакансий прогона.
- Имя пресета и название резюме проверяются после обрезки пробелов: из одних
  пробелов — 422.
- Новый код ошибки `CONFIG_INVALID`: любой эндпоинт при незагружаемом
  `config.yaml` отвечает 500 `CONFIG_INVALID`, `retryable: false`. Модель
  override прогона вне каталога остаётся 400 `VALIDATION`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `platform/durable-jobs`: ключ идемпотентности принадлежит пользователю.
- `parse-runs`: ответ отмены прогона.
- `catalog/vacancy-search`: `match_score` без `resume_id`.
- `catalog/saved-filters`: имя пресета из пробелов.
- `career/resumes`: название резюме из пробелов.
- `platform/api-conventions`: код `CONFIG_INVALID` (новое требование).
- `platform/runtime-config`: ответ API при ошибке конфигурации.

## Impact

- Код:
  - `backend/api/routes/runs.py`, `backend/api/routes/vacancies.py`,
    `backend/api/routes/config.py`, `backend/api/params.py`;
  - `backend/errors.py`, `backend/config_builder.py`, `backend/schemas.py`;
  - `frontend/src/api/client.ts`, `frontend/src/types/api.ts` (тип ответа
    отмены прогона и новый код ошибки).
- API: форма ответа отмены прогона, новые 422 и новый код 500 `CONFIG_INVALID`.
- Данные: ключи идемпотентности прогонов, зарезервированные до выката с
  владельцем `api`, истекут через 24 ч. Миграций нет.

# platform/api-conventions Specification

## Purpose
Общие правила HTTP API: префикс версии, формат ошибок, id запроса, HTTP-журнал
без секретов и доступ из браузера.

## Requirements

### Requirement: Ошибки API в едином формате
Эндпоинты API MUST находиться под `/api/v1`. Любой ответ с ошибкой SHALL содержать `detail` — объект `{code, message, retryable}`. Ошибка без своего кода получает код по статусу: 401 — `NOT_AUTHENTICATED`, 403 — `FORBIDDEN`, 404 — `NOT_FOUND`, 422 — `VALIDATION`, 429 — `RATE_LIMITED`, 5xx — `INTERNAL`, прочие — `VALIDATION`, с `retryable: false`. Клиент различает ошибки по `code`, а не по тексту.

#### Scenario: Свой код ошибки
- **WHEN** администратор запускает прогон, пока активен другой
- **THEN** ответ 409, `detail.code` — `CONFLICT_ACTIVE_RUN`, `message` — непустой текст, `retryable` — `false`

#### Scenario: Код по статусу
- **WHEN** запрошен несуществующий прогон
- **THEN** ответ 404, `detail.code` — `NOT_FOUND`, `retryable` — `false`

### Requirement: Ошибка валидации запроса — 422 с полем
Запрос, не прошедший проверку параметров или тела, MUST получать 422 с кодом `VALIDATION` и сообщением вида `<поле>: <причина>` по первой найденной ошибке.

#### Scenario: Неверный параметр
- **WHEN** список вакансий запрошен с `page=0`
- **THEN** ответ 422, `detail.code` — `VALIDATION`, в `message` — имя параметра

### Requirement: Непредвиденная ошибка не раскрывает подробностей
Необработанное исключение MUST давать 500 с кодом `INTERNAL`, сообщением «Внутренняя ошибка сервера» и `retryable: true`; подробности SHALL попадать только в журнал, где ответ отмечен как ошибка.

#### Scenario: Сбой обработчика
- **WHEN** обработчик запроса выбросил исключение
- **THEN** ответ 500, в HTTP-журнале запись ответа со статусом 500 уровня ERROR

### Requirement: У каждого запроса есть id
Каждый ответ MUST нести заголовок `X-Request-ID`: входящий `X-Request-ID`, если это UUID, иначе новый UUID. Все записи журнала, сделанные при обработке запроса, SHALL содержать этот id.

#### Scenario: Клиент передал свой id
- **WHEN** запрос пришёл с `X-Request-ID`, равным UUID
- **THEN** в ответе тот же `X-Request-ID`

#### Scenario: Id не UUID
- **WHEN** запрос пришёл с `X-Request-ID: evil | injected`
- **THEN** в ответе новый UUID, а не присланная строка

### Requirement: HTTP-журнал не содержит секретов
В журнале запроса и ответа MUST маскироваться заголовки `Authorization` (кроме схемы), `Cookie`, `Set-Cookie`, `X-API-Key`, а в JSON, форме и query — значения ключей пароля, секрета и API-ключа (по вхождению в имя), а также `token`, `access_token`, `refresh_token`, `ticket` и подобных, на любой вложенности. Обрезанное тело, которое уже не разбирается как JSON, SHALL маскироваться по тем же ключам.

#### Scenario: Регистрация и запрос с токеном
- **WHEN** пользователь регистрируется с паролем, получает access-токен и запрашивает `/auth/me` с ним в `Authorization` и в query
- **THEN** в журнале пароль, токен в теле ответа, заголовке (`Bearer ***`) и query заменены на `***`, email виден

#### Scenario: Вложенный ключ
- **WHEN** тело запроса — `{"cfg": {"api_key": "k", "max_tokens": 5}, "list": [{"secret": 1}]}`
- **THEN** в журнале `api_key` и `secret` заменены на `***`, `max_tokens` виден

### Requirement: Что попадает в HTTP-журнал
На запрос MUST писаться две записи с общим id: запрос (метод, путь, заголовки, тело) и ответ (статус, длительность, заголовки, тело). Тела SHALL писаться только текстовых типов, при `HTTP_LOG_BODIES` (см. `platform/runtime-config`), не длиннее `HTTP_LOG_BODY_LIMIT` (4096 байт); поток событий и вложения — без тела. Пути `HTTP_LOG_EXCLUDE_PATHS` (здоровье, агрегаты, документация) и CORS preflight не пишутся.

#### Scenario: Большое тело
- **WHEN** ответ длиннее лимита
- **THEN** в журнале начало тела с пометкой об обрезке

#### Scenario: Проверка здоровья
- **WHEN** запрошен `/api/v1/health` или пришёл CORS preflight
- **THEN** HTTP-журнал записей не получает

### Requirement: Браузерный доступ только с разрешённых источников
API MUST разрешать браузерные запросы с учётными данными только с источников из `CORS_ORIGINS` (по умолчанию `http://localhost:5173`, `http://localhost:3000`, `http://127.0.0.1:5173`) и SHALL открывать браузеру заголовки `X-Request-ID` и `X-Export-Truncated`.

#### Scenario: Чужой источник
- **WHEN** браузер шлёт запрос с источника вне `CORS_ORIGINS`
- **THEN** ответ не содержит разрешения CORS для этого источника

### Requirement: Ошибка конфигурации — CONFIG_INVALID
Если запросу нужна конфигурация, а `config.yaml` не загружается (см. `platform/runtime-config`), ответ MUST быть 500 с кодом `CONFIG_INVALID`, причиной загрузки в `message` и `retryable: false`: повтор не поможет, пока файл не исправят. Так SHALL отвечать любой эндпоинт, включая `GET /config`, запуск прогона, курсы, HH и метрики. Модель override прогона вне каталога остаётся 400 `VALIDATION`.

#### Scenario: Битый конфиг в метриках
- **WHEN** в `config.yaml` неизвестный ключ, и пользователь запрашивает метрики
- **THEN** ответ 500, `detail.code` — `CONFIG_INVALID`, `retryable` — `false`, `message` называет ключ

#### Scenario: Битый конфиг при запуске прогона
- **WHEN** `config.yaml` не загружается, и администратор запускает прогон
- **THEN** ответ 500 `CONFIG_INVALID`, прогон не создаётся, `Idempotency-Key` освобождается

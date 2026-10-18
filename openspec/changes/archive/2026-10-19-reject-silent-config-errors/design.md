# Design

## Context

Наблюдения этапов 3–5 baseline (`baseline-career`, `baseline-catalog`,
`baseline-platform`) о молчаливых ошибках конфигурации:

- `AppConfig` и вложенные модели Pydantic по умолчанию отбрасывают неизвестные
  поля (`extra="ignore"`, у `TelegramConfig` — явно, после перехода на Telethon).
  `load_config` (`tg_jobs_parser/config/loader.py`) об отброшенных ключах не
  сообщает.
- `Settings.*_job_backend_normalized` (`backend/settings.py`) превращает любое
  значение, кроме `rq`, в `inprocess`.
- `apply_ai_defaults_from_registry` (`tg_jobs_parser/ai/llm_models_registry.py`)
  сверяет с каталогом только `ai.model`. Умолчания запасных моделей фильтруются
  по каталогу, а явно заданные — нет.
- `GET /health` (`backend/api/routes/health.py`) проверяет только
  `Path(config_path).is_file()`.
- `apply_salary_rub_for_vacancy` (`backend/fx/apply.py`) не использует
  `fx.default_currency_if_missing`. Для зарплаты без валюты `is_ruble_currency`
  ложно, а курс не находится, поэтому сумма пуста. В метриках прогона
  (`enrich_vacancies_salary_rub`) настройка действует, и сумма есть.

Решения пользователя (2026-10-19), все по рекомендованным вариантам:

- неизвестный ключ — ошибка загрузки;
- неизвестный бэкенд — процесс не стартует;
- модели вне каталога — ошибка загрузки;
- настройку валюты применять при записи.

## Goals / Non-Goals

**Goals:**
- Каждая из пяти ошибок конфигурации видна сразу: при загрузке, при старте или в
  `/health`.
- Рублёвая сумма считается одинаково при записи, при пересчёте и в метриках
  прогона.

**Non-Goals:**
- Строгая проверка `config_snapshot` прогона. Снимок собирается из уже
  проверенной `AppConfig`, а строгость сломала бы `queued`-прогоны после
  удаления поля из схемы.
- Проверка лишних переменных окружения. У `Settings` `extra="ignore"` остаётся:
  в `.env` живут переменные compose и других сервисов.
- Автоматический пересчёт старых вакансий без валюты. Его делает существующий
  `POST /fx/recalculate-rub`.
- Пункты групп D и E (`has_salary`, архив при пересчёте, HH, Telegram,
  согласованность API).

## Decisions

### 1. Неизвестные ключи ищет обход YAML по схеме, а не `extra="forbid"`

В `loader.py` функция `_unknown_keys(model, data, prefix)` рекурсивно сверяет
ключи словаря с `model_fields`. Во вложенные модели она заходит, в том числе
через `Optional` и `list[...]`. Поля-словари со свободными ключами
(`dictionaries.specializations` и подобные) не проверяются. Если ключи найдены,
поднимается `ConfigLoadError` со списком путей через точку, например
`Неизвестные ключи config.yaml: fx.enabeld, parser.vacancy_score_treshold`.

Допустимые устаревшие ключи хранятся явным списком `{"ai.models", "ai.fallback_model"}`:
их обрабатывает `AiConfig.migrate_fallback_model`. `llm_model_catalog` в YAML
считается неизвестным ключом: поле заполняется из `llm_models.yaml` и молча
перезаписывалось бы.

Почему не `extra="forbid"` на моделях:
- `AppConfig.model_validate(snapshot)` в воркере стал бы строгим, а это
  Non-Goal;
- Pydantic выдал бы ошибки вперемешку с другими, а `ai.fallback_model` пришлось
  бы удалять в before-валидаторе.

Обход работает только при чтении файла.

### 2. Бэкенд задач проверяет `Settings`

Поля `parse_job_backend`, `llm_job_backend` и `hh_sync_job_backend` получают тип
`Literal["inprocess", "rq"]` и before-валидатор: `strip().lower()`, пусто — это
`inprocess`. Ошибку Pydantic о поле с алиасом назовёт имя переменной, а значение
скроет `hide_input_in_errors=True`. Свойства `*_normalized` остаются и
возвращают поле, чтобы не трогать полтора десятка мест вызова.

### 3. Все модели сверяются в `apply_ai_defaults_from_registry`

После подстановки умолчаний функция проверяет `fallback_models` (каждый
непустой id), `match_model` и `cover_letter_model`, если они заданы. Проверка
идёт до `AppConfig.model_validate`, поэтому значения берутся из сырого словаря
со `strip()`, а устаревший `fallback_model` учитывается. Сообщение называет поле
и модель: `ai.match_model 'x/y' отсутствует в llm_models.yaml`. Тип ошибки
прежний, `LlmModelsLoadError`, а `loader` превращает её в `ConfigLoadError`.

`_reject_unknown_models` в `config_builder` остаётся: overrides прогона
проверяются отдельно (400 `VALIDATION`).

При реализации нашлись ещё два молчаливых сбоя рядом:
- `ai.fallback_model` фактически игнорировался. Каталог подставлял
  `default_fallbacks` раньше, чем `AiConfig.migrate_fallback_model` видел старый
  ключ. Теперь перенос делает `apply_ai_defaults_from_registry`, до подстановки
  умолчаний.
- `LlmModelsLoadError` из `apply_ai_defaults_from_registry` выходил из
  `load_config` как есть, а не `ConfigLoadError`. Уже для `ai.model` это давало
  необработанную ошибку в `/config` и `/runs`. Теперь он оборачивается в
  `ConfigLoadError`.

### 4. `/health` вызывает `load_config`

`config_loaded = True`, если `load_config(settings.config_path)` прошёл без
`ConfigLoadError`. Загрузка кэшируется по mtime и стоит дёшево при каждом
опросе. Ошибка пишется в журнал на уровне warning, ответ остаётся 200.
Анонимному клиенту текст ошибки не отдаётся: в нём пути и id моделей.

### 5. Валюта по умолчанию в `apply_salary_rub_for_vacancy`

Валюта для расчёта — `normalize_currency(v.salary_currency,
default_if_missing=config.default_currency_if_missing)`. По ней проверяется
рубль и ищется курс. Период (`…/HOUR`) по-прежнему берётся из исходной
`salary_currency`. Пустая настройка даёт пустую валюту, суммы нет. Сохранённая
`salary_currency` не меняется: подстановка влияет только на рублёвую сумму.

## Risks / Trade-offs

- [Чужой `config.yaml` с лишними ключами перестанет грузиться] → ошибка
  перечисляет все ключи сразу. `config.yaml` проекта проверен: лишних ключей нет.
- [`.env` с опечаткой в бэкенде не стартует] → это и есть цель; ошибка называет
  переменную.
- [Подстановка RUB у зарплаты без валюты из зарубежного канала завысит или
  занизит сумму] → правила принимают число без валюты только рядом со словом
  «зарплата» или выше порога правдоподобия. Владелец может задать пустую
  настройку.
- [`/health` теперь читает YAML] → чтение кэшируется по mtime и размеру; при
  ошибке результат не кэшируется, каждый опрос читает файл заново (~5 мс).

## Migration Plan

Миграций нет. После выката:
1. API или воркер с неверным конфигом не стартует либо `/health` показывает
   `degraded` — исправить по тексту ошибки.
2. `POST /fx/recalculate-rub` заполнит рублёвые суммы у старых вакансий без
   валюты.

Откат — откат коммита.

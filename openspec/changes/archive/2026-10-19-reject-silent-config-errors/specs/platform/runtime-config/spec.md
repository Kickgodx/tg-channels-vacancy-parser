# Spec Delta

## MODIFIED Requirements

### Requirement: Ошибка в config.yaml останавливает операцию с понятной причиной
Отсутствующий файл, некорректный YAML, корень не-объект, нарушение схемы и неизвестный ключ MUST давать ошибку загрузки конфигурации с причиной; ошибка о ключах перечисляет пути всех неизвестных ключей. Старые ключи `ai.models` и `ai.fallback_model` SHALL приниматься. `GET /config` и `GET /config/defaults` при ошибке отвечают 500. Ошибка SHALL NOT запоминаться: исправленный файл читается сразу. Снимок конфигурации созданного прогона на неизвестные ключи не проверяется.

#### Scenario: Исправленный файл
- **WHEN** `config.yaml` содержал список вместо объекта, затем его исправили
- **THEN** первое чтение — ошибка загрузки, следующее — исправленная конфигурация

#### Scenario: Опечатка в ключе
- **WHEN** в `config.yaml` есть `fx.enabeld: false` и `parser.vacancy_score_treshold: 7`
- **THEN** конфигурация не загружается, ошибка называет `fx.enabeld` и `parser.vacancy_score_treshold`

#### Scenario: Старый ключ запасной модели
- **WHEN** в `config.yaml` задан `ai.fallback_model` вместо `ai.fallback_models`
- **THEN** конфигурация загружается, модель становится единственной запасной

### Requirement: Проверка здоровья
`GET /health` MUST быть доступен без входа и отвечать 200 с `status`: `ok`, если база отвечает и конфигурация загружается без ошибки, иначе `degraded`, а также флагами `database` и `config_loaded`. Причина ошибки конфигурации SHALL записываться в журнал и не попадать в ответ.

#### Scenario: База недоступна
- **WHEN** Postgres не отвечает
- **THEN** ответ 200, `status` — `degraded`, `database` — `false`

#### Scenario: Конфигурация не загружается
- **WHEN** файл `config.yaml` есть, но в нём неизвестный ключ
- **THEN** ответ 200, `status` — `degraded`, `config_loaded` — `false`, текста ошибки в ответе нет

## ADDED Requirements

### Requirement: Бэкенд задач — inprocess или rq
`PARSE_JOB_BACKEND`, `LLM_JOB_BACKEND` и `HH_SYNC_JOB_BACKEND` MUST принимать `inprocess` или `rq` без учёта регистра и пробелов; пустое значение или отсутствие переменной — `inprocess`. Другое значение SHALL быть ошибкой старта процесса, которая называет переменную.

#### Scenario: Опечатка в бэкенде
- **WHEN** `LLM_JOB_BACKEND=redis`
- **THEN** процесс не стартует, ошибка называет `LLM_JOB_BACKEND`

#### Scenario: Регистр и пробелы
- **WHEN** `PARSE_JOB_BACKEND=" RQ "`
- **THEN** прогоны идут через очередь RQ

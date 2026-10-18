# Spec Delta

## MODIFIED Requirements

### Requirement: Ошибка в config.yaml останавливает операцию с понятной причиной
Отсутствующий файл, некорректный YAML, корень не-объект, нарушение схемы и неизвестный ключ MUST давать ошибку загрузки конфигурации с причиной; ошибка о ключах перечисляет пути всех неизвестных ключей. Старые ключи `ai.models` и `ai.fallback_model` SHALL приниматься. API при ошибке отвечает 500 `CONFIG_INVALID` (см. `platform/api-conventions`). Ошибка SHALL NOT запоминаться: исправленный файл читается сразу. Снимок конфигурации прогона на неизвестные ключи не проверяется.

#### Scenario: Исправленный файл
- **WHEN** `config.yaml` содержал список вместо объекта, затем его исправили
- **THEN** первое чтение — ошибка загрузки, следующее — исправленная конфигурация

#### Scenario: Опечатка в ключе
- **WHEN** в `config.yaml` есть `fx.enabeld: false` и `parser.vacancy_score_treshold: 7`
- **THEN** конфигурация не загружается, ошибка называет `fx.enabeld` и `parser.vacancy_score_treshold`

#### Scenario: Старый ключ запасной модели
- **WHEN** в `config.yaml` задан `ai.fallback_model` вместо `ai.fallback_models`
- **THEN** конфигурация загружается, модель становится единственной запасной

#### Scenario: Конфигурация для UI при ошибке
- **WHEN** `config.yaml` не загружается, и запрошен `GET /config/defaults`
- **THEN** ответ 500 `CONFIG_INVALID` с `retryable: false`

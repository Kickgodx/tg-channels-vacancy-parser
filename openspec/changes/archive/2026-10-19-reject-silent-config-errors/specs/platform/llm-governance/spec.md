# Spec Delta

## MODIFIED Requirements

### Requirement: Модели выбираются из каталога
Модели и их цены MUST браться из `llm_models.yaml` (`LLM_MODELS_PATH`, иначе рядом с `config.yaml`, иначе в корне проекта). Пустая `ai.model` SHALL заменяться `default_model` каталога, пустые `ai.fallback_models` — `default_fallbacks` без основной модели. `ai.model`, а также заданные `ai.fallback_models`, `ai.match_model` и `ai.cover_letter_model` вне каталога — ошибка загрузки конфигурации, которая называет поле и модель. Каталог доступен через `GET /config/llm-models`.

#### Scenario: Конфигурация проекта
- **WHEN** загружается `config.yaml` проекта
- **THEN** каталог конфигурации совпадает с `llm_models.yaml`, все модели конфигурации входят в него

#### Scenario: Модель вне каталога
- **WHEN** `ai.model` — id, которого нет в `llm_models.yaml`
- **THEN** конфигурация не загружается, ошибка называет модель

#### Scenario: Модель матчинга вне каталога
- **WHEN** `ai.model` есть в каталоге, а `ai.match_model` — id, которого в каталоге нет
- **THEN** конфигурация не загружается, ошибка называет `ai.match_model` и эту модель

#### Scenario: Запасная модель вне каталога
- **WHEN** в `ai.fallback_models` одна модель из каталога и одна вне его
- **THEN** конфигурация не загружается, ошибка называет `ai.fallback_models` и модель вне каталога

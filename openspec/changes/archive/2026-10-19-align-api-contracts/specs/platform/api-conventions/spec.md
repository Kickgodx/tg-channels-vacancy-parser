# Spec Delta

## ADDED Requirements

### Requirement: Ошибка конфигурации — CONFIG_INVALID
Если запросу нужна конфигурация, а `config.yaml` не загружается (см. `platform/runtime-config`), ответ MUST быть 500 с кодом `CONFIG_INVALID`, причиной загрузки в `message` и `retryable: false`: повтор не поможет, пока файл не исправят. Так SHALL отвечать любой эндпоинт, включая `GET /config`, запуск прогона, курсы, HH и метрики. Модель override прогона вне каталога остаётся 400 `VALIDATION`.

#### Scenario: Битый конфиг в метриках
- **WHEN** в `config.yaml` неизвестный ключ, и пользователь запрашивает метрики
- **THEN** ответ 500, `detail.code` — `CONFIG_INVALID`, `retryable` — `false`, `message` называет ключ

#### Scenario: Битый конфиг при запуске прогона
- **WHEN** `config.yaml` не загружается, и администратор запускает прогон
- **THEN** ответ 500 `CONFIG_INVALID`, прогон не создаётся, `Idempotency-Key` освобождается

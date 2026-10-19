# platform/alerting Specification

## Purpose
Алерты эксплуатации: какие состояния системы (зависшие задачи, очереди, LLM, курсы,
перегрузка API, недоступные процессы, бэкап) поднимают алерт, по каким метрикам и где
алерты видны.

## Requirements

### Requirement: Алерты вычисляются и видны в профиле observability
При `COMPOSE_PROFILES=observability` Prometheus MUST вычислять правила алертов из репозитория и отправлять сработавшие в Alertmanager. Alertmanager SHALL NOT доставлять алерты во внешние каналы: они MUST быть видны в его UI и в Grafana. Каждый алерт MUST иметь метку `severity` (`critical` или `warning`) и аннотацию `summary` на русском. Без профиля observability поведение приложения SHALL NOT меняться.

#### Scenario: Сработавший алерт
- **WHEN** условие алерта выполняется дольше его `for`
- **THEN** алерт в состоянии firing виден в UI Alertmanager и в Grafana, с `severity` и `summary`

#### Scenario: Внешней доставки нет
- **WHEN** алерт сработал
- **THEN** Alertmanager не отправляет сообщений наружу

### Requirement: Задача с истёкшим lease
API MUST публиковать метрику `db_jobs_lease_expired{kind}` — число задач в `running` с истёкшим или пустым lease — для `kind` = `parse`, `llm`, `hh_sync`, включая нули. Алерт `JobLeaseExpired` (`critical`) SHALL срабатывать, когда для любого `kind` значение больше 0 дольше 5 минут: recover (см. `platform/durable-jobs`) за это время такую задачу уже вернул бы в очередь.

#### Scenario: Воркер умер посреди прогона
- **WHEN** прогон в `running`, его lease истёк, и recover не поднимает его 5 минут
- **THEN** срабатывает `JobLeaseExpired` с `kind="parse"`

#### Scenario: Recover работает
- **WHEN** lease LLM-задачи истёк, и recover вернул её в `queued` через минуту
- **THEN** `JobLeaseExpired` не срабатывает

### Requirement: Очереди не разбираются
Алерт `LlmQueueGrowing` (`warning`) SHALL срабатывать, когда очередь RQ `llm` не пустела 15 минут и за эти 15 минут выросла, и это держится 5 минут. Алерт `ParseQueueStuck` (`critical`) SHALL срабатывать, когда в очереди RQ `parse` есть задача дольше 10 минут: прогон активен один, и живой parse-worker берёт его сразу.

#### Scenario: LLM-воркеры не успевают
- **WHEN** глубина очереди `llm` 20 минут не опускается до нуля и растёт
- **THEN** срабатывает `LlmQueueGrowing`

#### Scenario: Короткий всплеск
- **WHEN** в очередь `llm` пришло 30 задач, и через 10 минут она пуста
- **THEN** `LlmQueueGrowing` не срабатывает

#### Scenario: Parse-worker не запущен
- **WHEN** прогон поставлен в очередь `parse` и 10 минут никем не взят
- **THEN** срабатывает `ParseQueueStuck`

### Requirement: Курс ЦБ устарел
Алерт `FxRatesStale` (`warning`) SHALL срабатывать, когда дата последнего курса ЦБ в БД (`fx_latest_rate_timestamp_seconds`) старше 3 суток дольше 1 часа. Пока курсов в БД нет, алерт SHALL NOT срабатывать.

#### Scenario: Синк курсов сломан
- **WHEN** последний курс в `exchange_rates` датирован 4 сутками ранее
- **THEN** срабатывает `FxRatesStale`

### Requirement: LLM отключился при разборе
AI-разбор (прогон, LLM-импорт) MUST увеличивать `llm_enrich_disabled_total{reason}` один раз за разбор, когда LLM перестаёт использоваться: `circuit_breaker` (ошибки подряд), `quota` (квота, дневной лимит провайдера или суточный бюджет), `init_failed` (AI включён, но не запустился). Серии всех `reason` MUST существовать с нуля со старта процесса. Алерт `LlmDisabled` (`warning`) SHALL быть активен 24 часа после такого события.

#### Scenario: Квота провайдера кончилась посреди прогона
- **WHEN** провайдер ответил 429 с дневным лимитом, и прогон дальше разбирает посты без LLM
- **THEN** `llm_enrich_disabled_total{reason="quota"}` вырос на 1, и срабатывает `LlmDisabled`

#### Scenario: Много постов после отключения
- **WHEN** после отключения LLM прогон пропустил ещё 500 постов
- **THEN** счётчик вырос только на 1

#### Scenario: AI не запустился
- **WHEN** `ai.enabled: true`, но клиент LLM не создан (нет ключа)
- **THEN** `llm_enrich_disabled_total{reason="init_failed"}` вырос на 1

### Requirement: Поток отказов из-за перегрузки
API MUST увеличивать `api_overload_rejections_total` на каждый ответ 503 `OVERLOADED` (см. `platform/api-conventions`) и только на него. Алерт `ApiOverloaded` (`warning`) SHALL срабатывать, когда за 5 минут такие отказы составляют больше 1% запросов к API и больше 0,1 в секунду, и это держится 5 минут.

#### Scenario: Устойчивая перегрузка
- **WHEN** 10 минут подряд 5% запросов получают 503 `OVERLOADED`
- **THEN** срабатывает `ApiOverloaded`

#### Scenario: 503 от HH
- **WHEN** HH API недоступен, и `POST /hh/sync` отвечает 503 `UPSTREAM_UNAVAILABLE`
- **THEN** `api_overload_rejections_total` не растёт

### Requirement: Процесс недоступен
Алерт `TargetDown` (`critical`) SHALL срабатывать, когда скрейп backend или воркера (`llm-worker`, `parse-worker`, `hh-worker`) не проходит дольше 5 минут, в том числе у остановленного контейнера. Алерт `WorkerMissing` (`critical`) SHALL срабатывать, когда у типа воркера нет ни одной цели скрейпа дольше 5 минут.

#### Scenario: Backend упал
- **WHEN** `/metrics` backend не отвечает 5 минут
- **THEN** срабатывает `TargetDown` для backend

#### Scenario: Воркер остановлен
- **WHEN** контейнер `hh-worker` остановлен
- **THEN** через 5 минут срабатывает `TargetDown` для `hh-worker`

#### Scenario: Воркер не запускался
- **WHEN** Prometheus стартовал, а контейнера `parse-worker` нет
- **THEN** через 5 минут срабатывает `WorkerMissing` для `parse-worker`

### Requirement: Прогон завершился ошибкой
Алерт `ParseRunFailed` (`warning`) SHALL быть активен, пока среди прогонов, завершённых за последние 24 часа, есть прогон со статусом `failed`.

#### Scenario: Прогон упал
- **WHEN** прогон завершился `failed`
- **THEN** `ParseRunFailed` активен следующие 24 часа

#### Scenario: Прогон отменён
- **WHEN** прогон завершился `cancelled` или `partial`
- **THEN** `ParseRunFailed` не срабатывает

### Requirement: pg-backup публикует состояние
`pg-backup` MUST после каждой попытки бэкапа и при старте записывать в каталог дампов файл метрик в текстовом формате Prometheus: время последнего проверенного дампа, интервал бэкапа, время и успех последней попытки. Итог последней попытки MUST переживать рестарт контейнера. Попытка, не запущенная из-за уже идущего бэкапа, SHALL NOT считаться попыткой.

#### Scenario: Проверка восстановления провалилась
- **WHEN** дамп снят, но восстановление во временный кластер не прошло
- **THEN** файл метрик показывает неуспешную последнюю попытку, а время проверенного дампа прежнее

#### Scenario: Рестарт после провала
- **WHEN** контейнер `pg-backup` перезапущен после неуспешной попытки
- **THEN** файл метрик по-прежнему показывает неуспешную последнюю попытку

### Requirement: Алерты бэкапа
Алерт `PgBackupStale` (`critical`) SHALL срабатывать, когда последний проверенный дамп старше 1,5 интервала бэкапа дольше 1 часа (порог совпадает с healthcheck `pg-backup`). Алерт `PgBackupFailed` (`critical`) SHALL быть активен, пока последняя попытка неуспешна. Алерт `PgBackupMetricsMissing` (`warning`) SHALL срабатывать, когда метрик бэкапа нет в Prometheus дольше 30 минут.

#### Scenario: Бэкап не делается
- **WHEN** интервал 24 ч, и последний проверенный дамп снят 40 часов назад
- **THEN** срабатывает `PgBackupStale`

#### Scenario: Провал и восстановление
- **WHEN** попытка провалилась, а следующая через час прошла
- **THEN** `PgBackupFailed` был активен этот час и затем погас

# tg_jobs_parser

Локальное **Windows-first** приложение для парсинга Telegram-каналов с IT-вакансиями: **Telethon** (ваш аккаунт, MTProto), правиловое извлечение полей, опциональный **AI** (OpenRouter), фильтры, дедуп, метрики, экспорт **Excel/JSON**. Дополнительно — admin-sync вакансий с **HH.ru**, матчинг резюме и сопроводительные письма (фоновые LLM-джобы).

**Основной способ работы** — веб-интерфейс на **React** + **FastAPI** backend + **PostgreSQL** + **Redis** / `parse-worker` + `llm-worker` (Docker Compose). Парсинг ставится в очередь RQ, прогресс пишется в БД (SSE + poll), вакансии сохраняются в PostgreSQL.

Дополнительно — **CLI (Typer)** для первой авторизации Telethon, дампа/импорта БД и отладки без UI.

---

## Быстрый старт (Docker Compose)

### Требования

- Docker Desktop (Windows / macOS / Linux)
- Файл `.env` рядом с `config.yaml` (см. [Telethon](#telethon-что-подготовить-локально))
- Каталог `sessions/` для файла MTProto-сессии (монтируется в backend)

### Запуск

```powershell
cd путь\к\tg_jobs_parser
# Скопируйте .env.example → .env и заполните TELEGRAM_API_ID / TELEGRAM_API_HASH
# В .env должна быть строка COMPOSE_PROFILES=observability (уже есть в .env.example)
docker compose up --build
```

| Сервис   | URL |
|----------|-----|
| **Web UI** | http://localhost:5173 |
| **API** (OpenAPI) | http://localhost:9000/docs |
| **Prometheus** | http://localhost:9090 |
| **Grafana** | http://localhost:3000 (login: `admin` / `admin`) |
| **Loki** | http://localhost:3100 (API; логи смотрятся в Grafana → Explore) |
| **Alloy** | http://localhost:12345 (UI пайплайна сбора логов и трасс) |
| **Tempo** | http://localhost:3200 (API; трассы смотрятся в Grafana → Explore → Tempo) |
| **PostgreSQL** | `127.0.0.1:5432` (user/pass/db: `tgjobs` / `tgjobs` / `tgjobs`) |
| **Redis** | `127.0.0.1:16379` → контейнер `:6379` (очереди RQ: parse + LLM) |

В compose backend слушает контейнерный `:8000`, на хост проброшен **`9000`**. Сервис `parse-worker` (тот же образ, команда `tg-jobs-parse-worker`) выполняет прогоны парсинга через RQ (очередь `parse`, concurrency=1, volume `sessions/` для Telethon); метрики — контейнерный `:9102` (на хост не публикуется). Сервис `llm-worker` (`tg-jobs-llm-worker`) обрабатывает матчинг и письма; метрики — `:9101` (тоже без host mapping, чтобы работал `--scale`).

Полный стек (Prometheus, Grafana, Loki, Alloy, Tempo) включается профилем
`observability` через `COMPOSE_PROFILES=observability` в `.env` (уже есть в
`.env.example`). Без этой переменной `docker compose up` поднимает только
ядро: postgres, redis, backend, workers, frontend.

Если `.env` создан раньше — добавьте строку `COMPOSE_PROFILES=observability`,
иначе Grafana/Prometheus не стартуют.

Backend отдаёт метрики HTTP на `http://localhost:9000/metrics` (для scrape из Docker-сети; в проде лучше не публиковать наружу).

```powershell
# Только ядро (без observability):
$env:COMPOSE_PROFILES=""; docker compose up --build
```

Либо уберите/закомментируйте `COMPOSE_PROFILES` в `.env`.

### Первая авторизация Telethon

В контейнере backend интерактивный ввод телефона и кода удобнее, чем в браузере:

```powershell
docker compose exec backend python -m tg_jobs_parser.cli login --config /app/config.yaml
```

Введите номер (`+79991234567`), код из Telegram/SMS и при необходимости пароль 2FA. Файл сессии появится в `sessions/` на хосте (volume).

После этого парсинг запускайте из UI (**Парсинг** → настройте фильтры → **Запустить парсинг**).

### Переменные backend (`.env`)

```env
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
OPENROUTER_API_KEY=...          # если ai.enabled в config.yaml
HH_API_TOKEN=...                # Bearer-токен HH (admin sync); без client_credentials
HH_CONTACT_EMAIL=you@example.com  # для User-Agent HH API

# Опционально (для docker compose уже задано в compose-файле):
# DATABASE_URL=postgresql+psycopg://tgjobs:tgjobs@postgres:5432/tgjobs
# CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
# APP_ENV=dev                     # dev (локально) или prod — см. «Режимы dev и prod» ниже
# BIND_ADDR=127.0.0.1             # адрес публикации портов UI/API в compose; 0.0.0.0 — открыть в сеть
# INFRA_BIND_ADDR=127.0.0.1       # то же для Postgres, Redis и observability
# API_KEY=                        # если задан — заголовок X-API-Key для API (дополнительно к JWT)
# JWT_SECRET=                     # подпись access-токенов; в dev без него — эфемерный (сессии сгорают при рестарте), в prod обязателен (32+ символа)
# JWT_TTL_MINUTES=60              # TTL access-токена
# AUTH_REGISTRATION_OPEN=true     # false — регистрация закрыта после первого пользователя; умолчание: dev — true, prod — false
# LLM_JOB_BACKEND=rq              # rq (Docker) или inprocess (локально без Redis)
# PARSE_JOB_BACKEND=rq            # rq (Docker + parse-worker) или inprocess (ThreadPool в API)
# PARSE_JOB_STUCK_MINUTES=30      # stale queued без живого RQ (мин)
# PARSE_JOB_LEASE_SECONDS=120     # lease running parse; recover при истечении
# LLM_JOB_LEASE_SECONDS=120       # lease running LLM
# JOB_RECOVER_INTERVAL_SECONDS=60 # периодический recover: в worker (rq) или в API (inprocess)
# ANALYTICS_CACHE_SECONDS=30      # кэш агрегатов дашборда/аналитики в памяти API (сек); 0 — выключить
# LLM_USER_DAILY_CALLS=100        # суточная квота вызовов LLM на не-админа (мэтч, письма, импорт); 0 — без квоты
# PARSE_JOB_MAX_ATTEMPTS=3        # после стольких попыток зависший прогон → failed вместо requeue
# LLM_JOB_MAX_ATTEMPTS=2          # то же для LLM-джоб → error
# PARSE_RQ_JOB_TIMEOUT_SECONDS=43200  # таймаут RQ-джобы parse (сек; по умолчанию 12 ч)
# PARSE_WORKER_METRICS_PORT=9102  # контейнерный порт метрик parse-worker
# REDIS_URL=redis://redis:6379/0  # очереди RQ (parse + LLM); локально — redis://127.0.0.1:16379/0
# VITE_API_KEY=                   # если API_KEY задан — тот же ключ для SSE/export во frontend
# HTTP_LOG_BODIES=true            # тела запросов/ответов (JSON, form, text) в HTTP-логе; умолчание: dev — true, prod — false
# HTTP_LOG_BODY_LIMIT=4096        # макс. символов тела в логе, дальше — «…[truncated, N bytes]»
# HTTP_LOG_EXCLUDE_PATHS=/metrics,/api/v1/health,/api/v1/vacancies/metrics,/api/v1/analytics/salaries,/api/v1/analytics/sources,/api/v1/analytics/companies,/docs,/docs/oauth2-redirect,/redoc,/openapi.json,/favicon.ico
#                                  (те же пути не трассируются)
# OTEL_ENABLED=false              # OpenTelemetry-трассировка (в compose — true)
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # куда слать OTLP (в compose — http://alloy:4318)
# OTEL_TRACES_SAMPLE_RATIO=1.0    # доля сохраняемых трасс (head sampling)
# OTEL_SERVICE_NAME=tg-jobs-parser-backend
```

Первый вход в UI — через регистрацию на странице `/login`; подробнее — в
разделе [Аутентификация и per-user данные](#аутентификация-и-per-user-данные).

### Режимы dev и prod

`APP_ENV` переключает умолчания, сам код один и тот же. Без переменной работает
`dev` — локальная разработка ничего не требует настраивать.

| | `APP_ENV=dev` (по умолчанию) | `APP_ENV=prod` |
|---|---|---|
| Регистрация новых пользователей | открыта | закрыта после первого (админа) |
| Тела запросов/ответов в HTTP-логе | пишутся | не пишутся (резюме и email не уходят в Loki) |
| `JWT_SECRET` | необязателен, эфемерный секрет | обязателен, 32+ символа — иначе backend и воркеры не стартуют |

`AUTH_REGISTRATION_OPEN` и `HTTP_LOG_BODIES`, заданные явно, действуют в любом
режиме — например, `APP_ENV=prod` + `AUTH_REGISTRATION_OPEN=true`, чтобы
временно пустить нового пользователя.

Порты compose публикуются на `127.0.0.1`: с этой машины всё доступно по
`localhost` как раньше, из сети — нет. К Postgres и Redis с хоста подключайтесь
по `127.0.0.1`, а не `localhost`: порт слушает только IPv4, а `localhost`
сначала пробует `::1` — psycopg на Windows ждёт там весь connect timeout на
каждое новое соединение (браузера это не касается). `BIND_ADDR=0.0.0.0` открывает UI и API
(доступ с телефона, сервер без reverse proxy); `INFRA_BIND_ADDR` — то же для
Postgres, Redis и observability, но у них дефолтные пароли или нет
аутентификации, наружу их открывать не стоит.

Минимальный `.env` для сервера:

```env
APP_ENV=prod
JWT_SECRET=...        # python -c "import secrets; print(secrets.token_urlsafe(48))"
# BIND_ADDR=0.0.0.0   # если перед API нет reverse proxy на этой же машине
```

---

## Web UI (React)

Стек: **Vite + TypeScript + React Router + TanStack Query + Recharts**.

| Страница | Описание |
|----------|----------|
| **Вход** | `/login` — вход и регистрация; без токена остальные страницы недоступны |
| **Парсинг** | Форма парсинга (каналы, даты, специализации, формат работы, AI, пороги score); запуск и очистка кэша LLM — только для админа |
| **Прогон** | Статус «В очереди» → running; прогресс (SSE / poll из БД, переживает F5 и рестарт API), метрики, графики, AI (токены), таблица, экспорт |
| **Импорт** | `/import` — вставка текста вакансии вручную (например, с hh.ru): rules + LLM-разбор и запись в общую базу; ниже — последние импортированные |
| **HH** | `/hh` — только для админа: sync вакансий с HH.ru (поиск AQA/Python), без LLM; кэш listings + skip detail |
| **Вакансии** | Все уникальные вакансии из PostgreSQL: поиск, фильтры, пресеты, пагинация, сортировка по клику на заголовок; вкладка **Аналитика** |
| **Резюме** | Список резюме пользователя: просмотр текста, создание, редактирование, удаление |

Возможности таблицы: сортировка asc/desc по дате, зарплате, валюте, формату, грейду, score, match; выбор видимых колонок; модальное окно с полным текстом, оценкой соответствия резюме и ссылками на пост/канал.

---

## Возможности

### Поиск и пресеты фильтров

Поиск по тексту — полнотекстовый (Postgres FTS): функциональный GIN-индекс
`to_tsvector('russian', cleaned_text)`, запрос через `websearch_to_tsquery`,
сортировка по `ts_rank`. Со стеммингом: «python разработчик» находит
«python-разработку». На SQLite (тесты) откатывается на `ILIKE`.

Список вакансий по умолчанию опирается на частичный индекс
`ix_vacancies_active_post_date (post_date, id) WHERE NOT is_duplicate AND archived_at IS NULL`
(ORDER BY + COUNT без seq scan). `GET /vacancies` отдаёт slim-карточки с
`preview`; полный текст — `GET /vacancies/{id}`. COUNT кэшируется ~1 с в
процессе API.

Набор фильтров сохраняется как именованный пресет (`saved_filters`, JSON с
полями `VacancyQuery`) и применяется одной кнопкой. Пресеты приватные —
привязаны к пользователю.

### Аналитика

Вкладка **Аналитика** на странице вакансий: агрегация в SQL, без выгрузки
строк, с теми же фильтрами, что и список. Запросы аналитики всегда идут с
`include_archived=true` (активные **и** архивные), чтобы история по HH
не обрезалась TTL.

- `GET /analytics/salaries` — медиана/квартили/распределение
  `detected_salary_avg_rub` по грейдам, специализациям и формату работы;
  помесячная и **погодовая** динамика; разрезы год×грейд / год×формат и
  месяц×грейд / месяц×формат (для drill-down).
- UI: режим «Все годы (YoY)» или выбор года → месяц к месяцу.
- `GET /analytics/sources` — качество канала: число вакансий, доля с
  зарплатой, средний `vacancy_score`, доля уникальных.
- `GET /analytics/companies` — топ компаний.

Зарплаты приводятся к рублям по курсам ЦБ (`exchange_rates`), метка
`salary_display` показывает дату курса.

Агрегаты (`/vacancies/metrics`, `/analytics/*`) кэшируются в памяти API на `ANALYTICS_CACHE_SECONDS` (30 с) по набору фильтров; запись вакансий в том же процессе сбрасывает кэш сразу, запись из воркера видна не позже чем через TTL. Фильтр по специализации на Postgres идёт по GIN-индексу (`detected_specializations` — `JSONB`, миграция 019). `config.yaml` перечитывается только при изменении файла (кэш по mtime).

### Матчинг резюме к вакансиям

Резюме хранятся текстом и управляются на вкладке **Резюме** (создание,
просмотр, редактирование, удаление); на странице «Вакансии» резюме только
выбирается в панели над таблицей. Дальше оценка идёт
**по одной вакансии**: открываете карточку → кнопка «Оценить резюме» рядом с
«Открыть пост» / «Канал» → синхронный вызов LLM. В карточке появляются
overall score, разбивка по измерениям (роль, навыки, грейд, домен, формат),
confidence оценки, пояснение и списки совпадающих / недостающих навыков
с приоритетом (critical/important/secondary) и evidence, а также сильные
стороны и риски кандидата.

- LLM отвечает только за оценку измерений и объяснение: итоговый
  `overall_score` считает backend детерминированно — взвешенная сумма
  breakdown (роль 30%, навыки 35%, грейд 20%, домен 10%, формат 5%) плюс
  penalty за пробелы по critical-требованиям (1 → cap 69, 2+ → cap 59).
- Оценка сохраняется парой `(resume_id, vacancy_id)`: повторное открытие
  карточки отдаёт её из БД, токены не тратятся. Кнопка «Переоценить резюме»
  (`force=true`) запускает LLM заново.
- Посчитанные скоры видны в колонке **Match**, по ним же работает сортировка
  (`sort_by=match_score` при выбранном резюме).
- Мэтчинг и письма — фоновые джобы (`llm_jobs`): кэш → 200; иначе 202 +
  `job_id`. В Docker: Redis Queue (RQ) + сервис `llm-worker` (тот же образ,
  `tg-jobs-llm-worker`). UI ждёт через SSE `GET /llm-jobs/{id}/events`
  (fallback — poll). Параллелизм: `--scale llm-worker=N` и
  `ai.interactive_per_user_limit`; `ai.interactive_concurrency` — для
  inprocess / ориентир scale. Воркер — RQ ``SimpleWorker`` (без fork: меньше
  пик RAM, метрики в том же процессе). Метрики — контейнерный порт 9101
  (Prometheus `dns_sd` по имени `llm-worker`, все реплики; на хост не
  пробрасывается). Локально без Redis: `LLM_JOB_BACKEND=inprocess`.
- **Лимиты расхода.** `ai.requests_per_minute` при заданном `REDIS_URL` — общий
  на все процессы (API, parse-worker, все реплики llm-worker): счётчик минутного
  окна в Redis, а не лимитер в памяти каждого процесса. Без Redis или при его
  сбое — локальный лимит процесса. Не-админу доступно `LLM_USER_DAILY_CALLS`
  вызовов LLM в сутки (UTC; по умолчанию 100, `0` — без квоты): каждая
  поставленная мэтч/письмо-джоба и каждый текст ручного импорта, ушедший в LLM.
  Сверх квоты — 429 `LLM_QUOTA_EXCEEDED`; импорт дочитывает остаток правилами.
  Ответы из кэша квоту не тратят; админ (владелец ключа) без квоты.
- Модель матчинга задаётся отдельно от модели парсинга: `ai.match_model`
  (+ `ai.match_reasoning_enabled` для reasoning-моделей) — качество здесь
  важнее скорости, вызовов на порядок меньше. Глубина рассуждений —
  `ai.match_reasoning_effort` (`high`, `xhigh`, …), одна на все модели:
  OpenRouter сам сводит уровень к ближайшему, который поддерживает модель.

### Сопроводительные письма

Из карточки вакансии — кнопка «Сопроводительное» рядом с «Оценить резюме»:
LLM пишет черновик отклика под конкретную вакансию по выбранному резюме
(120–200 слов, язык — по языку вакансии, без выдуманного опыта). Тон —
выбором рядом с кнопкой (нейтральный / дружелюбный / формальный). Если пара
уже оценена, в промпт подмешивается сохранённый матч (сильные стороны).

- Письмо хранится парой `(resume_id, vacancy_id)` в `cover_letters`:
  повторное открытие карточки отдаёт его из БД, токены не тратятся.
  «Перегенерировать письмо» (`force=true`) вызывает LLM заново;
  `DELETE` сбрасывает сохранённый вариант.
- Текст открывается в редактируемом поле — правки локальные, для кнопки
  «Копировать»; в БД остаётся исходный вариант LLM.
- Модель — `ai.cover_letter_model` (пусто → `match_model` → `model`),
  reasoning-флаг и уровень общие с матчингом (`ai.match_reasoning_enabled`,
  `ai.match_reasoning_effort`).

### Аутентификация и per-user данные

Своя лёгкая JWT-аутентификация: argon2-хэш пароля (`pwdlib`) + HS256
(`pyjwt`). Первый зарегистрированный аккаунт становится админом и наследует
резюме/пресеты, созданные до появления пользователей.

- Приватное (по `user_id`): резюме, пресеты фильтров, матчи, письма.
- Общее: вакансии, прогоны, аналитика, курсы ЦБ — единый корпус. Читает любой
  залогиненный пользователь; менять (запуск/отмена прогона, курсы, кэш LLM,
  HH sync) может только админ.
- Токен хранится в `localStorage`, уходит в `Authorization: Bearer …`;
  401 → редирект на `/login`. Для SSE (EventSource не умеет заголовки)
  токен передаётся параметром `access_token`.

Локальная разработка frontend без Docker:

```powershell
cd frontend
npm install
# API в Docker Compose (порт на хосте):
# VITE_API_BASE_URL=http://localhost:9000/api/v1
# или локальный uvicorn:
# VITE_API_BASE_URL=http://localhost:8000/api/v1
npm run dev
```

Backend отдельно (без compose порт обычно `8000`; без Redis — `PARSE_JOB_BACKEND=inprocess` и `LLM_JOB_BACKEND=inprocess`):

```powershell
poetry install
# DATABASE_URL=postgresql+psycopg://tgjobs:tgjobs@127.0.0.1:5432/tgjobs
# PARSE_JOB_BACKEND=inprocess
# LLM_JOB_BACKEND=inprocess
uvicorn backend.main:app --reload --port 8000
```

Прогон парсинга: API создаёт строку `parse_runs` со статусом `queued` и ставит RQ-джобу (в Docker — `PARSE_JOB_BACKEND=rq`). `parse-worker` claim'ит прогон → `running`, пишет `progress` в БД (+ Redis pub/sub для SSE). Рестарт backend не обрывает воркер: прогресс продолжает обновляться, cancel идёт через `cancel_requested` в БД. Локально без Redis: `PARSE_JOB_BACKEND=inprocess` (ThreadPool в процессе API).

Самовосстановление джоб (parse и LLM устроены одинаково):

- **Зависший `running`** (lease истёк — воркер упал или завис) recover-цикл возвращает в `queued` и запускает заново. В режиме `rq` цикл живёт в воркерах, в `inprocess` — в самом API: при старте он сразу поднимает `queued`, оставшиеся от прошлого процесса, а оборванный рестартом `running` — после истечения lease (до ~3 минут при настройках по умолчанию).
- **Лимит попыток.** Каждый claim увеличивает `attempts`; прогон, который воркер не довёл до конца `PARSE_JOB_MAX_ATTEMPTS` раз (по умолчанию 3; для LLM-джоб — `LLM_JOB_MAX_ATTEMPTS`, 2), завершается `failed` / `error` с пояснением, а не уходит на очередной круг.
- **Неполный прогон.** Если в `ai.mode: primary` LLM отключился по лимиту, оставшиеся посты не разбираются. Такой прогон получает статус `failed` с причиной в `error_message`; уже разобранные вакансии сохранены, а неразобранные посты не кэшируются — повторный прогон после сброса лимита обработает их.
- **Docker Compose:** у сервисов приложения `restart: unless-stopped` и healthcheck; воркеры стартуют после того, как backend стал healthy (то есть применил миграции).

SSE (`/runs/{id}/events`, `/llm-jobs/{id}/events`): один подписчик `redis.asyncio` на процесс API раздаёт события всем стримам — соединения не занимают потоки, а БД читается вне event loop. Каждые 15 с тишины стрим шлёт комментарий `: ping` (прокси не рвут соединение по idle-timeout), раз в 5 с сверяет статус с БД на случай пропущенного `done`. Без Redis или при его сбое стрим переходит на опрос БД и живёт до терминального статуса.

Инкрементальная вычитка каналов:

- **Чекпоинты.** После `completed`-прогона для каждого канала, прочитанного сплошь до границы окна, сохраняется чекпоинт «прочитано всё с даты X по сообщение N» (`telegram_channel_checkpoints`). Следующий прогон читает из канала только сообщения новее N — ежедневный запуск не перечитывает недельное окно заново. Чекпоинт не двигается, если канал прочитан не до конца или пост потерян (`failed`, отмена, в `primary` — сбой LLM на посте). Полный перечит окна (после смены фильтров или промпта) — галочка «Перечитать окно дат целиком» в форме запуска. Подробности — `parser.incremental_fetch` в [config.md](config.md).
- **Окно в прошлом.** Если `date_to` раньше сегодняшнего дня, обход начинается с `date_to` (`offset_date`), а не перебирает всё, что новее окна, расходуя `max_posts_per_channel`.
- **FloodWait.** Ожидание до `parser.flood_wait_max_seconds` пережидается с продолжением обхода с места остановки; более долгое или ошибка посреди канала возвращают уже прочитанные посты с предупреждением, а не пустой канал.
- **Дедуп** внутри прогона сравнивает fuzzy-ключи только в блоке с тем же набором специализаций (`parser.fuzzy_dedup_block_by_specializations`), а снимок прогресса несёт последние 30 вакансий, а не копию всех уникальных.

---

## Backend API (FastAPI)

Префикс: `/api/v1`.

| Метод | Путь | Назначение |
|-------|------|------------|
| `GET` | `/health` | Статус API и подключения к БД |
| `POST` | `/auth/register` / `/auth/login` | Регистрация / вход → JWT access-токен |
| `GET` | `/auth/me` | Текущий пользователь |
| `GET` | `/config/defaults` | Значения по умолчанию для формы |
| `GET` | `/config/llm-models` | Каталог моделей из `llm_models.yaml` |
| `POST` | `/runs` | Постановка прогона в очередь (admin; `queued` → parse-worker / inprocess) |
| `GET` | `/runs/{id}` | Статус, метрики и durable `progress` прогона |
| `GET` | `/runs/{id}/events` | SSE: прогресс (Redis pub/sub или DB poll); после F5 — resume; Bearer или `?access_token=` |
| `POST` | `/runs/{id}/cancel` | Отмена (admin; флаг в БД; работает после рестарта API) |
| `GET` | `/runs/{id}/vacancies` | Вакансии прогона (`sort_by`, `sort_order`) |
| `GET` | `/runs/{id}/export.xlsx` / `.json` | Скачивание отчёта |
| `GET` | `/vacancies` | Глобальный список (slim: `preview` без полных текстов; фильтры, сортировка, пагинация, `resume_id`) |
| `GET` | `/vacancies/{id}` | Полная вакансия (`raw_text` / `cleaned_text`) для карточки |
| `POST` | `/vacancies/import` | Импорт вакансий из текста: `{texts, source_url?}` → rules + LLM → запись в БД |
| `GET` | `/hh/status` | Статус HH sync (admin): токен задан?, TTL listings, счётчики кэша |
| `POST` | `/hh/sync` | Синхронный sync HH (admin): поиск → dedup → detail только для новых → БД |
| `GET` | `/vacancies/metrics` | Агрегаты для графиков по текущим фильтрам |
| `GET` | `/vacancies/export.xlsx` | Excel всех вакансий в БД (те же фильтры, что у списка) |
| `GET` | `/analytics/salaries` \| `/sources` \| `/companies` | Зарплатные квантили, качество каналов, топ компаний |
| `GET` `POST` `DELETE` | `/filters` | Пресеты фильтров (приватные) |
| `GET` `POST` `PUT` `DELETE` | `/resumes` | Резюме пользователя |
| `POST` `GET` | `/resumes/{id}/match/{vacancy_id}` | Оценка: кэш → 200; иначе фоновая джоба → 202 `{job_id}` (`force=true` — пересчёт) |
| `POST` `GET` `DELETE` | `/resumes/{id}/cover-letter/{vacancy_id}` | Письмо: кэш → 200; иначе 202 `{job_id}` (`force`, `tone`/`extra_notes`) |
| `GET` | `/llm-jobs/{job_id}` | Статус LLM-задачи; при `done` — payload match/letter |
| `GET` | `/llm-jobs/{job_id}/events` | SSE статусов (Bearer или `?access_token=`); poll остаётся fallback |
| `GET` | `/fx/rates`, `POST` `/fx/refresh` \| `/fx/recalculate-rub` | Курсы ЦБ и пересчёт зарплат в ₽ (`POST` — admin) |
| `GET` | `/ai-cache/stats` | Число записей в кэше LLM |
| `POST` | `/ai-cache/clear` | Полная очистка кэша LLM (admin) |

Без логина доступны только `/health`, `/auth/register` и `/auth/login`; всё остальное
требует JWT. Мутации общих данных — `POST /runs`, `POST /runs/{id}/cancel`,
`POST /fx/*`, `POST /ai-cache/clear` и HH — только админ (`require_admin`, иначе 403).
`API_KEY`, если задан, — дополнительный заголовок `X-API-Key` поверх JWT, а не замена ему.

Тело `POST /runs` — whitelist overrides поверх `config.yaml`: `channels`,
`search.date_from|date_to`, `filters.*`,
`parser.vacancy_score_threshold|vacancy_min_substantive_score|incremental_fetch`,
`ai.enabled|model|fallback_models|use_cache` (модели — только id из `llm_models.yaml`).
Любое другое поле → 422: `ai.base_url`, лимиты, concurrency и путь к Telethon-сессии
задаются только на сервере.

Импортированные вручную вакансии помечаются `source_channel = "manual"`
(по нему их можно отфильтровать в списке); дедупликация — по тому же хэшу
текста, что и у постов Telegram. Пост-фильтры прогона (exclude_keywords,
форматы, локации) и порог `vacancy_score` при импорте не применяются:
при включённом AI решение «вакансия или нет» принимает LLM (`is_job_post`),
без него текст сохраняется как есть.

Вакансии с HH (`source_channel = "hh"`) появляются после admin-sync
(`/hh` или `POST /hh/sync`): поля берутся из API без LLM, дедуп мультигорода
как в MVP hhparser, TTL вакансий — общий `retention.vacancy_ttl_days`
(soft-archive: строки остаются в БД для исторических метрик).
Грейд HH мапится из `experience.id`: `noExperience`→`intern`,
`between1And3`→`junior`, `between3And6`→`middle`, `moreThan6`→`senior+`
(при старте/hourly cleanup — идемпотентный бэкфилл по `required_experience`).
Роли HH (`Тестировщик`, `Программист, разработчик`) пишутся в
`detected_specializations` как есть и дополнительно как `QA` /
`Software Engineer` — оба варианта доступны в фильтрах UI.
На вкладке `/hh` окно sync задаётся в интерфейсе: All, 7 / 14 / 30 дней или диапазон дат.
Кэш короткой выдачи (`hh_listings`) живёт `hh.listing_ttl_days` и не даёт
повторно дергать detail для уже загруженных id.

Кэш ответов LLM хранится в PostgreSQL (`ai_extraction_cache`). Параметры в `config.yaml`: `ai.cache_backend: postgres`, `ai.cache_ttl_days` (0 — без срока, иначе дни). Очистка — кнопка на странице **Парсинг** или `POST /ai-cache/clear`. Адаптер Postgres регистрируется при старте backend/CLI (порт `AIExtractionCacheBackend` в core, без зависимости `tg_jobs_parser` → `backend`).

### Схема БД

Миграции инкрементальные (`alembic/versions/001…020`, head —
`020_llm_user_usage`). При старте backend применяет `upgrade head`.

| Таблица | Назначение |
|---------|------------|
| `parse_runs` | Журнал прогонов: статус, метрики, снимок конфигурации |
| `vacancies` | Уникальные вакансии (`id` = content-hash или `sha256(hh:{id})` для HH), `archived_at` — TTL |
| `hh_listings` | Кэш короткой выдачи HH + `detail_fetched_at` (anti-DDOS detail) |
| `exchange_rates` | Курсы ЦБ по датам |
| `ai_extraction_cache` | Кэш ответов LLM при парсинге |
| `saved_filters` | Пресеты фильтров (`user_id`) |
| `resumes` | Резюме пользователя (`user_id`) |
| `vacancy_matches` | Оценки соответствия, PK `(resume_id, vacancy_id)` — он же кэш матчинга |
| `llm_jobs` | Фоновые LLM-задачи мэтчинга/писем (`queued`/`running`/`done`/`error`) |
| `cover_letters` | Сопроводительные письма, PK `(resume_id, vacancy_id)` — он же кэш генерации |
| `users` | Аккаунты: email, argon2-хэш, `is_admin` |
| `telegram_channel_checkpoints` | Чекпоинт канала для инкрементальной вычитки: `last_message_id`, `covered_from` |
| `llm_user_usage` | Вызовы LLM пользователя за сутки — квота `LLM_USER_DAILY_CALLS` |

Служебные таблицы чистятся сами (при старте и раз в час): просроченный кэш LLM и ключи идемпотентности, завершённые LLM-джобы старше `retention.llm_jobs_ttl_days` (30) и прогоны старше `retention.parse_runs_ttl_days` (180) — см. [config.md](config.md). Вакансии только архивируются, не удаляются.

**Если меняете колонки/таблицы** — добавьте новую миграцию (`alembic revision`) и обновите модели в `backend/db/models.py`; при следующем `docker compose up` она применится автоматически, данные сохранятся.

Ядро парсинга (`tg_jobs_parser.parse_runner` + общий `tg_jobs_parser.ingestion`) вызывается из `backend/services/run_service.py`; результаты пишутся в PostgreSQL (UPSERT по `id` вакансии). Ручной импорт использует тот же ingestion с `MANUAL_POLICY`.

---

## Структура проекта

```text
tg_jobs_parser/
  README.md
  pyproject.toml
  config.yaml
  llm_models.yaml
  docker-compose.yml       — postgres, redis, backend, parse-worker, llm-worker,
                             frontend; observability — profile `observability`
  Dockerfile.backend

  backend/                 — FastAPI, SQLAlchemy, RunService, SSE
    api/routes/            — health, auth, config, runs, vacancies, hh,
                             analytics, filters, resumes, fx, ai-cache, llm-jobs
    db/                    — модели, Alembic, bounded repos (+ repository facade),
                             кэш LLM (DbAIExtractionCache), UPSERT
    services/              — run/match/cover_letter/llm_job/interactive_llm,
                             llm_job_events, parse_run_events, auth, import, hh_sync
    workers/               — parse_worker / llm_worker (RQ), parse/llm_tasks, recover
    queue/                 — RQ Redis connection (очереди parse + default)
  alembic/                 — миграции SQL (001…014)

  frontend/                — React UI (Vite)
    src/pages/             — Login, Dashboard, Run, Import, Hh, Vacancies, Resumes
    src/components/        — ParseForm, VacancyTable, VacancyModal, ResumePanel,
                             MetricsCharts, AnalyticsPanel

  tg_jobs_parser/          — ядро парсинга (общее для API и CLI)
    parse_runner.py        — Telethon fetch, dedup, progress; ingest через ingestion/
    ingestion/             — shared RawPost → Vacancy (rules / AI / policy)
    cli/                   — Typer CLI (+ db dump/import)
    config/                — YAML + Pydantic
    parsers/               — Telethon
    extraction/            — rules helpers
    ai/                    — OpenRouter, cache port + registry, token cost
    hh/                    — клиент HH API, mapper → Vacancy
    evals/                 — scorer + live runner golden extraction
    filters/, deduplication/, analytics/, exporters/

  observability/           — Prometheus, Loki, Tempo, Alloy, Grafana dashboards
  evals/golden/            — golden JSONL (extraction; match/cover — см. ROADMAP)
  evals/fixtures/          — offline ответы для pytest
  sessions/                — Telethon session (не в git)
  .cache/ai_extractions/   — дисковый кэш LLM (если cache_backend=disk)
  output/                  — Excel/JSON при экспорте
  tests/
```

---

## Telethon: что подготовить локально

**Не передавайте никому** `api_hash`, коды SMS и файл сессии. Держите их только в `.env` и в `sessions/` (каталог в `.gitignore`).

1. Зарегистрируйте приложение на [https://my.telegram.org/apps](https://my.telegram.org/apps) и получите **API ID** и **API hash**.
2. Создайте `.env` рядом с `config.yaml` (образец в `.env.example`).
3. В `config.yaml` укажите `telegram.telethon_session_file` (например `sessions/tg_jobs.session`).
4. **Первый запуск** — интерактивная авторизация (см. [Docker](#первая-авторизация-telethon) или CLI ниже).

### Сообщения в консоли при первом запуске

- **`Failed to load SSL library` / `cryptg module not installed`** — на Windows Telethon может работать без cryptg; для ускорения: `poetry install --extras telethon`.
- **`Please enter your phone`** — первая авторизация. В `.env` можно задать **`TELEGRAM_PHONE`**; код из SMS вводится один раз (или **`TELEGRAM_2FA_PASSWORD`** при 2FA).

---

## Установка без Docker (Poetry)

```powershell
cd путь\к\tg_jobs_parser
poetry install
poetry install --extras telethon   # cryptg (опционально)
poetry install --extras ai         # OpenRouter
```

**Версии зависимостей фиксирует `poetry.lock`.** Docker-образ backend ставит их из lock-файла: `poetry export` → `pip install --require-hashes`, затем проект `--no-deps` и `pip check`. Поэтому в контейнере те же версии, что локально и в тестах, а не самые свежие из диапазонов `pyproject.toml`. Новая зависимость: `poetry add <pkg>`, обновление: `poetry update <pkg>`. Оба коммитятся вместе с `poetry.lock`, а `poetry lock` без `--regenerate` уже залоченные версии не поднимает.

Проверка:

```powershell
poetry run python -c "import telethon; print(telethon.__version__)"
```

Используйте **`poetry run python -m tg_jobs_parser.cli …`** или активируйте `.venv`.

---

## Конфигурация

Основной файл: `config.yaml` (схема: `tg_jobs_parser/config/schema.py`).

Полное описание секций — в [config.md](config.md).

### Отбор постов (`parser`)

- **`vacancy_score_threshold`** — минимальный общий score правил.
- **`vacancy_min_substantive_score`** — минимум без бонуса за email/@ (отсекает «пустые» контакты).
- **`channel_concurrency`** — сколько каналов опрашивать параллельно в одной
  Telethon-сессии (`1` — последовательно; слишком большое значение повышает риск FloodWait).
- **`db_flush_every_posts`** / **`db_flush_pending_vacancies`** — промежуточный
  UPSERT в БД во время прогона (backend), чтобы при обрыве не терять уже
  обработанные уникальные вакансии.

### Хранение вакансий (`retention`)

- **`vacancy_ttl_days`** (по умолчанию 40) — через сколько дней вакансия
  архивируется (soft delete, `archived_at`): по `post_date`, а без неё — по
  `parsed_at`. `0` — не архивировать. Проверка идёт при старте backend, раз в
  час фоном и после каждого прогона. Архивные строки не попадают в списки,
  пока не передать `include_archived=true`. Вкладка **Аналитика** всегда
  включает архив (активные + архивные), в т.ч. для YoY/MoM по каналу `hh`.
  Повторная публикация возвращает вакансию из архива: если канал снова
  запостил тот же текст (или HH обновил `published_at`), `post_date`
  сдвигается на свежую дату и TTL отсчитывается от неё.

### HH.ru (`hh`)

- **`enabled`** — разрешить admin sync.
- **`listing_ttl_days`** (по умолчанию 40) — TTL кэша `hh_listings` (hard delete
  по `last_seen_at`); после purge detail снова запросится.
- **`search.text` / `professional_role` / `per_page`** — параметры выдачи
  (дефолт — AQA/Python, роли 124 и 96).
- **`max_rps`** — лимит запросов к API (по умолчанию 3).
- **`db_flush_batch_size`** (по умолчанию 30) — промежуточный UPSERT+commit;
  буфер Vacancy в памяти очищается после каждого батча (меньше RAM, прогресс
  не теряется при обрыве/429).
- Секреты: `HH_API_TOKEN`, `HH_CONTACT_EMAIL` в `.env` (и в docker через `env_file`).
- Метрики/дашборд: Grafana «HH.ru sync» (`observability/grafana/.../hh.json`).

### Секция `ai` (опционально)

По умолчанию **`ai.enabled: false`**. При включении — разбор через **OpenRouter** (OpenAI-compatible SDK).

| Поле | Назначение |
|------|------------|
| `enabled` | Включить LLM |
| `mode` | `assist` / `primary` |
| `model` | Id из `llm_models.yaml` — модель парсинга вакансий |
| `match_model` | Модель матчинга резюме ↔ вакансия (обычно reasoning). Пусто — берётся `model` |
| `match_reasoning_enabled` | `true`, если `match_model` — reasoning-модель (OpenRouter `reasoning` вместо `json_object`) |
| `match_reasoning_effort` | Уровень рассуждений матчинга и писем: `max` / `xhigh` / `high` / `medium` / `low` / `minimal` / `none`. Только при `match_reasoning_enabled: true`; пусто — дефолт модели. Неподдерживаемый уровень OpenRouter сводит к ближайшему (напр. Gemini 3: `xhigh` → `high`); модели без выбора уровня его игнорируют. Рассуждения расходуют `max_completion_tokens` — на `xhigh`/`max` лимит стоит поднять |
| `cover_letter_model` | Модель генерации сопроводительных писем. Пусто — `match_model`, затем `model` |
| `fallback_models` | Запасные модели |
| `concurrency`, `requests_per_minute` | Темп и параллелизм парсинга |
| `interactive_concurrency`, `interactive_per_user_limit` | Пул и лимит на пользователя для фоновых мэтч/письмо (in-process; несколько реплик backend → нужна внешняя очередь) |
| `stop_llm_when_rate_limited` | Отключить LLM при 429 до конца прогона. В `mode: primary` оставшиеся посты не разбираются, и прогон завершается `failed` (см. «Самовосстановление джоб») |
| `max_retries_on_error` | Повторы при транзиентном сбое — 5xx, таймаут, обрыв соединения (по умолчанию 2, backoff 2/4/8… с; `0` — без повторов). Действует для парсинга, матчинга и писем |
| `max_completion_tokens` | Лимит выходных токенов |
| `use_cache` | Дисковый/Postgres кэш LLM |
| `prompt_version` | Должен совпадать с `EXTRACT_PROMPT_VERSION` в коде; кэш ключуется константой |

Ключ в `.env`: `OPENROUTER_API_KEY=sk-or-v1-...`

### LLM golden set и evals

Регрессии extraction ловятся без «на глаз»:

| Артефакт | Путь |
|----------|------|
| Golden dataset | `evals/golden/extraction.jsonl` |
| Offline fixtures | `evals/fixtures/extraction_outputs.json` |
| Scorer | `tg_jobs_parser/evals/score_extraction.py` |
| Версии промптов | `EXTRACT_PROMPT_VERSION` / `MATCH_PROMPT_VERSION` / `COVER_LETTER_PROMPT_VERSION` в `tg_jobs_parser/ai/prompts.py` |

```powershell
# Offline (в обычном pytest -q)
.venv\Scripts\python.exe -m pytest tests/test_evals_extraction.py -q

# Live против OpenRouter (дорого; не в CI)
.venv\Scripts\python.exe -m tg_jobs_parser.evals.run_extraction
```

Правило: изменили system/few-shot extraction → bump `EXTRACT_PROMPT_VERSION` → старый кэш не подхватится. Новый кейс — строка в JSONL + fixture для offline.

В UI и в `run_params` доступен блок **`ai_stats`**: вызовы, кэш, токены, **стоимость USD** (input / output / total).

> **Приватность:** текст постов уходит на серверы OpenRouter/провайдера модели.

### `work_formats` в фильтрах

`remote`, `hybrid`, `office`, `relocation`, **`any`**, **`exclude_office`**.

---

## CLI (авторизация Telethon и перенос БД)

Повседневный парсинг — через **React UI** → API. CLI — для **первого входа в Telegram**
(см. [Docker](#первая-авторизация-telethon)) и дампа/импорта PostgreSQL.

```powershell
python -m tg_jobs_parser.cli login --config config.yaml
# или с явным путём к сессии:
python -m tg_jobs_parser.cli login --telegram-session sessions/tg_jobs.session
```

Короткая команда после `poetry install`: `poetry run tg-jobs-parser login`

### Перенос данных между инстансами (дамп / импорт БД)

Экспорт корпуса вакансий и служебных таблиц (прогоны, вакансии, курсы ЦБ, кэш
LLM) в JSON. Пользователи, резюме, матчи, письма, пресеты и `hh_listings` в
дамп **не** входят:

```powershell
# локально (DATABASE_URL в .env)
poetry run tg-jobs-parser db dump -o backup/tgjobs_dump.json

# из контейнера backend
docker compose exec backend tg-jobs-parser db dump -o /app/backup/tgjobs_dump.json
```

Импорт в другой инстанс (целевой `DATABASE_URL`):

```powershell
poetry run tg-jobs-parser db import -f backup/tgjobs_dump.json
# полная замена данных в целевой БД (с подтверждением):
poetry run tg-jobs-parser db import -f backup/tgjobs_dump.json --mode replace
```

По умолчанию `--mode merge` — UPSERT по первичному ключу (существующие строки обновляются).  
`replace` — сначала очищает все четыре таблицы, затем загружает дамп.

Логи: консоль + `logs/app.log` (в Docker — только stdout, их собирает Alloy в Loki). Запись асинхронная: `QueueHandler` на root кладёт запись в очередь, stdout/файл пишет фоновый `QueueListener` — логирование не блокирует event loop. `app.log` ротируется по 10 МБ, хранится 5 частей `app.log.N.gz`. Stdout контейнеров в `docker-compose.yml` — `json-file` с ротацией (5 × 10 МБ, сжатие ротированных частей).

---

## Результаты и экспорт

- **PostgreSQL** — постоянное хранение уникальных вакансий и журнал прогонов (`parse_runs`).
- **Экспорт из UI** — `GET /runs/{id}/export.xlsx` и `.json` (листы и метрики как в `exporters/`).
- **CLI** — при локальном `parse` пишет в `output/` (`vacancies.xlsx`, `vacancies.json`, `output/charts/`).

---

## Observability (Prometheus + Grafana + Loki)

Стек метрик/логов/трасс поднимается вместе с приложением, если в `.env`
задан `COMPOSE_PROFILES=observability` (дефолт из `.env.example`):

| Компонент | Назначение |
|-----------|------------|
| **`prometheus-fastapi-instrumentator`** | Счётчики запросов, статус-коды, latency на backend |
| **`GET /metrics`** | Эндпоинт Prometheus (не путать с `/api/v1/vacancies/metrics`) |
| **Prometheus** | Scrape backend, llm-worker и parse-worker каждые 15 с (`observability/prometheus.yml`) |
| **Grafana** | Дашборды **API**, **LLM**, **LLM jobs / queue**, **Pipeline**, **HH.ru sync**, **DB pool** |
| **`llm_*` метрики** | Вызовы OpenRouter, токены, latency, кэш и пропуски при AI-enrichment |
| **Доменные метрики** | прогоны, UPSERT/TTL, импорт, матчинг, письма, LLM-джобы (`llm_job_enqueue_*`, `llm_worker_*`, `llm_rq_queue_jobs`, `llm_jobs_by_status`, recover), parse recover (`parse_jobs_recover_total`), auth, ЦБ |
| **`db_*` gauges** | COUNT-агрегаты по таблицам на каждый scrape (вакансии, резюме, матчи, письма, прогоны, LLM-кэш, свежесть курсов ЦБ) |
| **`db_pool_*` gauges** | SQLAlchemy QueuePool: занято / свободно / overflow / лимит (`pool_size` + `max_overflow`); на backend, llm-worker и parse-worker |
| **Loki** | Хранение логов (filesystem, retention 30 дней — `observability/loki-config.yml`) |
| **Alloy** | Собирает stdout контейнеров compose-проекта через docker.sock, парсит `level`/`logger` (labels) и `request_id`/`trace_id` (structured metadata) из формата логов и шлёт в Loki; принимает OTLP-трассы от backend и отправляет в Tempo (`observability/alloy-config.alloy`) |
| **Tempo** | Хранение трасс (local, retention 7 дней — `observability/tempo-config.yml`) |

**Затор очереди LLM:** смотреть дашборд **LLM jobs / queue** — растут `llm_rq_queue_jobs` и `llm_jobs_by_status{status="queued"}`; worker latency в `llm_worker_job_duration_seconds`. TraceQL: `{ name = "llm_job.run" }`, `{ name = "llm_job.enqueue" }`.

**Пул БД:** дашборд **DB pool** — `db_pool_checked_out` / `db_pool_checked_in` / `db_pool_overflow` / `db_pool_max_connections`. Утилизация ≈ `checked_out / max_connections` (по job).

Конфигурация: `observability/prometheus.yml`, `observability/loki-config.yml`, `observability/tempo-config.yml`, `observability/alloy-config.alloy`, provisioning Grafana — `observability/grafana/provisioning/` (datasources Prometheus, Loki и Tempo с переходами лог ↔ трасса).

**HTTP-лог** (`backend/http_logging.py`, логгер `backend.http`, вместо `uvicorn.access`). Каждому запросу присваивается `request_id` (UUID; валидный входящий `X-Request-ID` переиспользуется), он возвращается в заголовке ответа `X-Request-ID` и попадает в **каждую** строку лога, записанную во время запроса (и в логи роутов/сервисов): `... | backend.http | request_id=<uuid> | ...`. На запрос — две записи:

- `REQUEST METHOD path {json}` — до обработки: клиент, заголовки, тело;
- `RESPONSE METHOD path status Nms {json}` — после ответа: статус, длительность, заголовки, тело.

Тела пишутся для JSON / form / `text/*` / XML (SSE, бинарные ответы и `Content-Disposition: attachment` — без тела). Маскируются `Authorization` (остаётся схема: `Bearer ***`), `Cookie`/`Set-Cookie`, `X-API-Key`, query-параметры и ключи тела вида `password`/`token`/`access_token`/`secret`/`api_key`. Уровень `RESPONSE`: 4xx — `WARNING`, 5xx — `ERROR`. Не логируются шумные эндпоинты из `HTTP_LOG_EXCLUDE_PATHS` (по умолчанию Prometheus `/metrics`, health-check, агрегаты `/vacancies/metrics` и `/analytics/*`, Swagger/ReDoc/OpenAPI, favicon) и CORS preflight (`OPTIONS` с `Access-Control-Request-Method`).

Alloy кладёт `request_id` в **structured metadata** Loki (не label — уникален на запрос). В Grafana Explore:

```logql
# всё по одному запросу: REQUEST, логи обработчика, RESPONSE
{service="backend"} | request_id="3f2b8c1e-0d4a-4c7e-9a51-2b6f1d9e8a70"

# разобрать JSON HTTP-записей (поля event, status, body_*, headers_*)
{service="backend", logger="backend.http"} | regexp `(?P<http>\{.*\})$` | line_format "{{.http}}" | json
```

### Трассировка (OpenTelemetry → Tempo)

Трасса показывает, на что ушло время внутри одного запроса или прогона: дерево span'ов с длительностями. `backend/tracing.py` поднимает OpenTelemetry SDK при `OTEL_ENABLED=true`. Цепочка: backend → OTLP/HTTP → Alloy (batch) → Tempo → Grafana.

| Что трассируется | Как |
|---|---|
| Входящие HTTP | FastAPI-инструментация; span'ы `send`/`receive` отключены, пути из `HTTP_LOG_EXCLUDE_PATHS` не трассируются, `?access_token=` маскируется в атрибутах. Атрибут `request_id` — тот же, что в логах и `X-Request-ID` |
| SQL | SQLAlchemy-инструментация (текст запроса с плейсхолдерами, без значений параметров) |
| Исходящие HTTP | httpx-инструментация: OpenRouter (через openai SDK), ЦБ |
| LLM | `chat <model>`: `gen_ai.request.model`, `gen_ai.usage.input_tokens`/`output_tokens`, `llm.task` (`extract`/`match`/`cover_letter`), `llm.outcome`. Промпты и ответы в span не пишутся |
| Прогон парсинга | Отдельная трасса `parse_run` (со ссылкой на `POST /runs`): `parse.fetch_posts` (Telethon) → `parse.process_posts` → LLM-вызовы из пула потоков (контекст пробрасывается в воркеры) |
| Матчинг / письма / курсы ЦБ | `vacancy.match`, `cover_letter.generate`, `fx.sync_on_startup` |

Sampling: доля трасс — `OTEL_TRACES_SAMPLE_RATIO`. Sampler отбрасывает SQL/HTTP-span'ы без родителя: COUNT'ы `db_*`-коллектора на каждый scrape `/metrics` и фоновые запросы иначе дали бы тысячи трасс из одного span'а. Без `OTEL_ENABLED` (CLI, тесты) API OpenTelemetry работает как no-op.

**Связь логов и трасс.** Внутри span'а каждая строка лога получает `trace_id`: `... | request_id=<uuid> trace_id=<32 hex> | ...`. Alloy кладёт его в structured metadata. В Grafana:

- в Loki у строки с `trace_id` есть кнопка **«Открыть трассу»** (derived field);
- в Tempo у span'а есть **«Logs for this span»**: это логи с тем же `trace_id`;
- поиск в Tempo (TraceQL): `{ span.request_id = "<uuid>" }`, `{ name = "parse_run" }`, `{ span.llm.task = "match" && duration > 10s }`.

Из health-check, OpenAPI и `/metrics` HTTP-метрики не собираются (шум от probes и документации). LLM-метрики пишутся при парсинге с `ai.enabled: true` в том же процессе backend. При недоступной БД `db_*` gauges отдают пустой набор — scrape не падает.

---

## Тесты

```powershell
pytest
pytest -n 8     # параллельно (pytest-xdist)
```

В том числе: `tests/test_api_health.py`, `tests/test_auth.py`,
`tests/test_solo_match.py`, `tests/test_cover_letter.py`, `tests/test_llm_jobs.py`,
`tests/test_hh_mapper.py`, `tests/test_upsert_vacancies.py`,
`tests/test_db_transfer.py`, тесты ядра extraction/AI. Live LLM evals —
`@pytest.mark.llm` (не в обычном `pytest -q`).

Тесты офлайновые: `tests/conftest.py` подставляет `CONFIG_PATH` на конфиг с
выключенным `fx` — иначе агрегаты ходят за курсами в ЦБ на каждый запрос.

---

## Архитектура

```text
[React UI] ──REST/SSE──► [FastAPI backend] ──enqueue──► Redis (RQ)
                              │                              │
                              ▼                              ├── parse-worker → parse_runner
                         PostgreSQL (SoT: status/progress)   │     ├── Telethon
                              ▲                              │     ├── extraction/ / ai/
                         UPSERT vacancies                    │     └── filters → dedup → metrics
                              │                              └── llm-worker (match / cover letter)
```

- **`extraction/`** — быстрые эвристики (всегда).
- **`ai/`** — опционально: OpenRouter, JSON-схема, кэш, `merge_rules_and_ai`.
- **`hh/`** — sync вакансий HH.ru без LLM (admin).
- **`backend/`** — API, durable прогоны и LLM-джобы через RQ (`parse-worker` /
  `llm-worker`) или `inprocess` без Redis; прогресс прогона — колонка
  `parse_runs.progress` + SSE.

---

## Roadmap

Реализованное описано выше (аналитика, FTS и пресеты, матчинг резюме,
сопроводительные письма, HH sync, фоновые LLM-джобы / RQ, durable parse
через `parse-worker`, JWT / multi-user, observability). Планы — в
[ROADMAP.md](ROADMAP.md): трекер откликов, экспорт в Google Sheets / Notion,
CMS-админка вакансий, WebAuthn, golden/evals для match и cover letter.

Вне списка фичей: расширение Telethon (joinchat, rate-limit).

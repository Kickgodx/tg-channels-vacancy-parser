# tg_jobs_parser

Локальное **Windows-first** приложение для парсинга Telegram-каналов с IT-вакансиями: **Telethon** (ваш аккаунт, MTProto), правиловое извлечение полей, опциональный **AI** (OpenRouter), фильтры, дедуп, метрики, экспорт **Excel/JSON**. Дополнительно — admin-sync вакансий с **HH.ru**, матчинг резюме и сопроводительные письма (фоновые LLM-джобы).

**Основной способ работы** — веб-интерфейс на **React** + **FastAPI** backend + **PostgreSQL** + **Redis** / `parse-worker` + `llm-worker` + `hh-worker` (Docker Compose). Парсинг ставится в очередь RQ, прогресс пишется в БД (SSE + poll), вакансии сохраняются в PostgreSQL.

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
| **Grafana** | http://localhost:3000 (login: `admin` / `GRAFANA_ADMIN_PASSWORD` из `.env`) |
| **Loki** | http://localhost:3100 (API; логи смотрятся в Grafana → Explore) |
| **Alloy** | http://localhost:12345 (UI пайплайна сбора логов и трасс) |
| **Tempo** | http://localhost:3200 (API; трассы смотрятся в Grafana → Explore → Tempo) |
| **PostgreSQL** | `127.0.0.1:5432` (user/pass/db: `tgjobs` / `tgjobs` / `tgjobs`) |
| **Redis** | `127.0.0.1:16379` → контейнер `:6379` (очереди RQ: parse + LLM) |

В compose backend слушает контейнерный `:8000`, на хост проброшен **`9000`**. Сервис `parse-worker` (тот же образ, команда `tg-jobs-parse-worker`) выполняет прогоны парсинга через RQ (очередь `parse`, concurrency=1, volume `sessions/` для Telethon); метрики — контейнерный `:9102` (на хост не публикуется). Сервис `llm-worker` (`tg-jobs-llm-worker`) обрабатывает матчинг и письма; метрики — `:9101` (тоже без host mapping, чтобы работал `--scale`). Сервис `hh-worker` (`tg-jobs-hh-worker`, очередь `hh`) выполняет HH sync; метрики — `:9103`. Redis хранит очереди на томе `redis_data` с AOF (`appendonly yes`): рестарт контейнера их не теряет.

Полный стек (Prometheus, Grafana, Loki, Alloy, Tempo) включается профилем
`observability` через `COMPOSE_PROFILES=observability` в `.env` (уже есть в
`.env.example`). Без этой переменной `docker compose up` поднимает только
ядро: postgres, redis, backend, workers, frontend, pg-backup
([резервное копирование](#резервное-копирование-pg_dump-по-расписанию)).

Если `.env` создан раньше — добавьте строку `COMPOSE_PROFILES=observability`,
иначе Grafana/Prometheus не стартуют.

Метрики backend — на отдельном порту `API_METRICS_PORT` (контейнерный `:9100`), а не на порту API: на хост он не публикуется, Prometheus ходит на `backend:9100` по Docker-сети. `http://localhost:9000/metrics` отвечает 404.

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

# Пароли инфраструктуры compose — обязательны, без них `docker compose` не стартует.
# Только [A-Za-z0-9_-]: python -c "import secrets; print(secrets.token_urlsafe(24))"
POSTGRES_PASSWORD=...
REDIS_PASSWORD=...
GRAFANA_ADMIN_PASSWORD=...      # только с профилем observability; пустой или admin — Grafana не стартует

# Опционально (для docker compose уже задано в compose-файле):
# DATABASE_URL=...                # без него — tgjobs:<POSTGRES_PASSWORD>@127.0.0.1:5432/tgjobs (backend/CLI с хоста)
# CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
# APP_ENV=dev                     # dev (локально) или prod — см. «Режимы dev и prod» ниже
# BIND_ADDR=127.0.0.1             # адрес публикации портов UI/API в compose; 0.0.0.0 — открыть в сеть
# INFRA_BIND_ADDR=127.0.0.1       # то же для Postgres, Redis и observability
# BACKUP_INTERVAL_HOURS=24        # pg-backup: период pg_dump (см. «Резервное копирование»)
# BACKUP_KEEP=7                   # pg-backup: сколько проверенных дампов хранить
# BACKUP_HOST_DIR=./backup/pg     # pg-backup: каталог дампов на хосте
# API_KEY=                        # если задан — заголовок X-API-Key для API (дополнительно к JWT)
# JWT_SECRET=                     # подпись access-токенов; в dev без него — эфемерный (сессии сгорают при рестарте), в prod обязателен (32+ символа)
# JWT_TTL_MINUTES=15              # TTL access-токена (UI продлевает его по refresh-cookie)
# REFRESH_TTL_DAYS=30             # срок refresh-сессии, скользящий
# REFRESH_COOKIE_SECURE=          # Secure у refresh-cookie; по умолчанию dev — false, prod — true
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
# VITE_API_KEY=                   # если API_KEY задан — тот же ключ во frontend (заголовок X-API-Key)
# HTTP_LOG_BODIES=true            # тела запросов/ответов (JSON, form, text) в HTTP-логе; умолчание: dev — true, prod — false
# HTTP_LOG_BODY_LIMIT=4096        # макс. символов тела в логе, дальше — «…[truncated, N bytes]»
# HTTP_LOG_EXCLUDE_PATHS=/api/v1/health,/api/v1/vacancies/metrics,/api/v1/analytics/salaries,/api/v1/analytics/sources,/api/v1/analytics/companies,/docs,/docs/oauth2-redirect,/redoc,/openapi.json,/favicon.ico
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
| refresh-cookie `Secure` | нет (UI по `http://localhost`) | да — нужен HTTPS (`REFRESH_COOKIE_SECURE`) |
| API на не-loopback адресе | backend не стартует (`ALLOW_PUBLIC_DEV=true` — только warning) | разрешено |

`AUTH_REGISTRATION_OPEN` и `HTTP_LOG_BODIES`, заданные явно, действуют в любом
режиме — например, `APP_ENV=prod` + `AUTH_REGISTRATION_OPEN=true`, чтобы
временно пустить нового пользователя.

Порты compose публикуются на `127.0.0.1`: с этой машины всё доступно по
`localhost` как раньше, из сети — нет. К Postgres и Redis с хоста подключайтесь
по `127.0.0.1`, а не `localhost`: порт слушает только IPv4, а `localhost`
сначала пробует `::1` — psycopg на Windows ждёт там весь connect timeout на
каждое новое соединение (браузера это не касается). `BIND_ADDR=0.0.0.0` открывает UI и API
(доступ с телефона, сервер без reverse proxy) — только вместе с `APP_ENV=prod`:
dev-умолчания (открытая регистрация, резюме в логах) безопасны лишь на loopback, и
backend в dev на не-loopback адресе отказывается стартовать. Адрес он узнаёт из
`API_PUBLIC_ADDR` (compose передаёт туда `BIND_ADDR`), без него — из `API_HOST`
(по умолчанию `127.0.0.1`); `uvicorn --host` в обход `API_HOST` проверка не видит.
`INFRA_BIND_ADDR` — то же для Postgres, Redis и observability; у Prometheus, Loki,
Tempo и Alloy аутентификации нет, наружу их открывать не стоит.

Пароли Postgres, Redis и Grafana задаются в `.env` (`POSTGRES_PASSWORD`,
`REDIS_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`), дефолтных нет. Пароли Postgres и
Grafana применяются только при создании пустого тома; сменить на существующем:

```powershell
docker compose exec postgres sh -c 'psql -U tgjobs -c "ALTER ROLE tgjobs PASSWORD ''$POSTGRES_PASSWORD''"'
docker compose exec grafana sh -c 'grafana cli admin reset-admin-password "$GF_SECURITY_ADMIN_PASSWORD"'
```

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
| **Импорт** | `/import` — вставка текста вакансии вручную (например, с hh.ru): rules + LLM-разбор (фоновой джобой, с прогрессом) и запись в общую базу; ниже — последние импортированные |
| **HH** | `/hh` — только для админа: sync вакансий с HH.ru (поиск AQA/Python), без LLM; кэш listings + skip detail |
| **Вакансии** | Все уникальные вакансии из PostgreSQL: поиск, фильтры, пресеты, пагинация, сортировка по клику на заголовок; вкладка **Аналитика** |
| **Резюме** | Список резюме пользователя: просмотр текста, создание, редактирование, удаление |

Возможности таблицы: сортировка asc/desc по дате, зарплате, валюте, формату, грейду, score, match; выбор видимых колонок; модальное окно с полным текстом, оценкой соответствия резюме и ссылками на пост/канал.

---

## Возможности

### Поиск и пресеты фильтров

Поиск по тексту — полнотекстовый (Postgres FTS): функциональный GIN-индекс
`to_tsvector('russian', cleaned_text)`, запрос через `websearch_to_tsquery`,
сортировка по `ts_rank`. Со стеммингом: «разработчик» находит
«разработчиков».

Список вакансий по умолчанию опирается на частичный индекс
`ix_vacancies_active_post_date (post_date DESC NULLS LAST, id) WHERE NOT is_duplicate AND archived_at IS NULL`
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
  зарплатой, средний `vacancy_score`, дубли и доля уникальных постов.
  В `vacancies` попадает только первый пост с данным текстом, а каждое появление
  поста Telegram записывается в `vacancy_sightings`: оригинал или дубль со
  ссылкой на вакансию. Дублем считается похожий текст в другом канале, перепост
  в том же канале и текст, уже бывший в БД с прошлых прогонов. Доля уникальных —
  посты канала с новым текстом от всех его постов; канал-агрегатор, который только
  перепечатывает чужое, виден с нулём вакансий и низкой долей. Канал и даты фильтра
  берутся у самого поста, остальные фильтры — у вакансии. У HH дубли — это
  мультигородные копии.
- `GET /analytics/companies` — топ компаний.

Зарплаты приводятся к рублям по курсам ЦБ (`exchange_rates`), метка
`salary_display` показывает дату курса.

Агрегаты (`/vacancies/metrics`, `/analytics/*`) кэшируются в памяти API на `ANALYTICS_CACHE_SECONDS` (30 с) по набору фильтров; запись вакансий в том же процессе сбрасывает кэш сразу, запись из воркера видна не позже чем через TTL. Фильтр по специализации на Postgres идёт по GIN-индексу (`detected_specializations` — `JSONB`, миграция 019). `config.yaml` перечитывается только при изменении файла (кэш по mtime).

### Матчинг резюме к вакансиям

Резюме хранятся текстом и управляются на вкладке **Резюме** (создание,
просмотр, редактирование, удаление); на странице «Вакансии» резюме только
выбирается в панели над таблицей. Дальше оценка идёт
**по одной вакансии**: открываете карточку → кнопка «Оценить резюме» рядом с
«Открыть пост» / «Канал» → фоновая LLM-джоба (см. ниже). В карточке появляются
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
- Вместе с оценкой хранятся запрошенная модель матчинга (настроенная, а не
  ответившая fallback-модель — та остаётся в `model`) и версия промпта
  (`MATCH_PROMPT_VERSION`). Если с тех пор сменили `ai.match_model` (пусто →
  `ai.model`) или версию промпта, оценка **устарела**: ответы `GET`/`POST`, итог
  LLM-задачи и элемент списка вакансий отдают `stale: true` / `match_stale: true`,
  а `POST` без `force` пересчитывает её (202) вместо ответа из БД. В карточке у
  такой оценки — пометка «переоцените»; колонка Match и сортировка по ней не
  меняются. Оценки до миграции 030 по версии не устаревают (запрошенной
  считается фактическая модель).
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
  Общий потолок пользовательского LLM — `ai.interactive_daily_request_budget`:
  запросов мэтчинга и писем всех пользователей в UTC-сутки (счётчик в Redis; без
  Redis — на процесс). Исчерпан — мэтчинг и письма без LLM до полуночи UTC, после
  сброса снова работают без рестарта. Парсинг доступен только админу и бюджетом
  не ограничен.
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
- `POST` без `force` отдаёт сохранённое письмо, только если совпадает тон и
  письмо не устарело; иначе генерирует заново (202) в запрошенном тоне.
  Устаревание — как у оценки: сменилась модель писем (`ai.cover_letter_model` →
  `match_model` → `model`) или `COVER_LETTER_PROMPT_VERSION`; тогда `stale: true`
  и пометка «перегенерируйте» в карточке. Тон на `stale` не влияет.
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
- Общее: вакансии, аналитика, курсы ЦБ — единый корпус. Читает любой
  залогиненный пользователь; менять (курсы, кэш LLM) может только админ.
- Только админ: прогоны целиком (запуск, отмена, история, экспорт, SSE), ручной
  импорт и HH sync. Вкладки «Парсинг», «Импорт» и «HH» не-админу не показываются,
  прямой переход по URL уводит на «Вакансии».
- Сессия = короткий access JWT (`JWT_TTL_MINUTES`, 15 мин) + refresh-токен
  (`REFRESH_TTL_DAYS`, 30 дней, скользящий). Access хранится только в памяти
  вкладки и уходит в `Authorization: Bearer …`. Refresh — случайная строка в
  httpOnly cookie `tgjp_refresh` (`SameSite=Strict`, `Path=/api/v1/auth`, в prod
  `Secure`), в БД — только её sha256 (`refresh_tokens`). При загрузке страницы и
  на 401 UI молча вызывает `POST /auth/refresh`, повторяет запрос и на `/login`
  уходит, только если сессии нет — несохранённый ввод не теряется раз в час.
- Каждый refresh ротирует cookie. Повтор уже ротированного значения дольше
  30 с спустя (параллельные вкладки укладываются в это окно) считается кражей:
  отзывается вся сессия (все токены этого входа). `POST /auth/logout` отзывает
  сессию на сервере и стирает cookie; просроченные и отозванные строки чистит
  hourly-очистка служебных таблиц.
- Cookie `SameSite=Strict`, поэтому UI ходит в API того же origin: dev-сервер Vite
  проксирует `/api` на backend (`VITE_API_PROXY_TARGET`), в prod то же делает
  reverse proxy. Если задать `VITE_API_BASE_URL` абсолютным URL, UI и API должны
  остаться same-site: UI на `127.0.0.1`, а API на `localhost` — разные сайты, браузер
  отбросит cookie (`SchemefulSameSiteStrict`), и после перезагрузки придётся входить заново.
- SSE: EventSource не умеет заголовки, а JWT в query оседал бы в логах прокси.
  Поэтому UI перед подключением берёт `POST /auth/sse-ticket` (Bearer + `X-API-Key`)
  и открывает стрим с `?ticket=…`. Тикет — JWT с `typ=sse`: живёт 30 с,
  принимается один раз (использованный `jti` — в Redis `SET NX`, без `REDIS_URL` — в
  памяти процесса) и не годится как access-токен. Автопереподключение EventSource
  с тем же тикетом получит 401 — UI в этом случае дочитывает статус опросом.
  `?access_token=` и `?api_key=` больше не принимаются; curl и Swagger могут слать
  `Authorization: Bearer` (+ `X-API-Key`) прямо в SSE-запрос.
- Swagger (`/docs`) → кнопка «Authorize»: либо вставить готовый JWT в `HTTPBearer`
  (без слова `Bearer`), либо войти в `OAuth2PasswordBearer` — email в поле
  `username`, `client_id`/`secret` пустые (form-эндпоинт `POST /auth/token`, те же
  лимиты, что у `/auth/login`). `X-API-Key` — там же, если задан `API_KEY`.
- Отзыв токенов: в JWT — версия `users.token_version`; `POST /auth/change-password`
  и `POST /auth/logout-all` увеличивают её, и все ранее выданные токены
  (включая SSE и refresh-сессии) перестают приниматься. `is_active=false` отключает пользователя целиком.
- Перебор паролей: не больше `AUTH_RATE_LIMIT_PER_MINUTE` (20) попыток входа и
  регистрации с одного IP в минуту и `AUTH_MAX_FAILED_LOGINS` (5) неудачных входов
  на email за `AUTH_FAILED_LOGIN_WINDOW_SECONDS` (15 мин); дальше — 429 с
  `Retry-After`, пароль (argon2) не проверяется. С `REDIS_URL` счётчики общие для
  процессов. За NAT/Docker все клиенты могут приходить с одного IP — лимит по IP
  тогда общий.
- Регистрации сериализуются advisory-lock'ом Postgres: два параллельных «первых»
  пользователя не станут оба админами; повтор email → 409 `EMAIL_TAKEN`, формат
  email проверяется (`email-validator`).

Локальная разработка frontend без Docker:

```powershell
cd frontend
npm install
# UI ходит в /api того же origin, Vite проксирует его на backend.
# API в Docker Compose (порт на хосте):
# $env:VITE_API_PROXY_TARGET="http://localhost:9000"
# или локальный uvicorn — по умолчанию http://localhost:8000
npm run dev
```

Backend отдельно (без compose порт обычно `8000`; без Redis — `PARSE_JOB_BACKEND=inprocess` и `LLM_JOB_BACKEND=inprocess`):

```powershell
poetry install
# DATABASE_URL не нужен для Postgres из compose: собирается из POSTGRES_PASSWORD в .env
# PARSE_JOB_BACKEND=inprocess
# LLM_JOB_BACKEND=inprocess
uvicorn backend.main:app --reload --port 8000
```

Прогон парсинга: API создаёт строку `parse_runs` со статусом `queued` и ставит RQ-джобу (в Docker — `PARSE_JOB_BACKEND=rq`). `parse-worker` claim'ит прогон → `running`, пишет `progress` в БД (+ Redis pub/sub для SSE). Рестарт backend не обрывает воркер: прогресс продолжает обновляться, cancel идёт через `cancel_requested` в БД. Локально без Redis: `PARSE_JOB_BACKEND=inprocess` (ThreadPool в процессе API).

Самовосстановление джоб (parse, LLM и HH sync устроены одинаково):

- **Зависший `running`** (lease истёк — воркер упал или завис) recover-цикл возвращает в `queued` и запускает заново. В режиме `rq` цикл живёт в воркерах, в `inprocess` — в самом API: при старте он сразу поднимает `queued`, оставшиеся от прошлого процесса, а оборванный рестартом `running` — после истечения lease (до ~3 минут при настройках по умолчанию).
- **Отмена.** Прогон, LLM-задача (мэтч, письмо, импорт) и HH sync отменяются флагом `cancel_requested` в БД: задача в очереди сразу получает `cancelled`, выполняемую воркер останавливает в ближайшей точке проверки — импорт перед следующим текстом, HH sync перед следующей страницей поиска или группой деталей (уже полученные вакансии сохраняются, purge listings пропускается), мэтч и письмо после ответа модели (ответ отбрасывается, прежний результат остаётся; если результат уже сохранён — задача `done`). Зависший `running` с запрошенной отменой recover закрывает `cancelled`, а не ставит на повтор. Квота LLM за уже поставленные мэтч/письмо не возвращается.
- **Лимит попыток.** Каждый claim увеличивает `attempts`; прогон, который воркер не довёл до конца `PARSE_JOB_MAX_ATTEMPTS` раз (по умолчанию 3; для LLM-джоб — `LLM_JOB_MAX_ATTEMPTS`, 2; для HH sync — `HH_SYNC_JOB_MAX_ATTEMPTS`, 2), завершается `failed` / `error` с пояснением, а не уходит на очередной круг.
- **Отказ LLM при парсинге.** Транзиентные сбои (5xx, таймаут) повторяются с backoff; после `ai.circuit_breaker_errors` (5) постов подряд с ошибкой срабатывает circuit breaker — LLM отключается до конца прогона, как при исчерпанной квоте, и остальные посты не ждут повторов. 429 определяется по HTTP-статусу ответа.
- **Неполный прогон — `partial`.** Если в `ai.mode: primary` LLM отключился (лимит или circuit breaker), исчерпан потолок вызовов `ai.max_posts_per_run` или доля ошибок LLM выше `ai.max_error_ratio` (10%), часть постов не разобрана. Такой прогон получает статус `partial` с причиной в `error_message`; уже разобранные вакансии сохранены, чекпоинты остаются только у каналов, разобранных до первого потерянного поста, неразобранные посты не кэшируются — повторный прогон обработает их.
- **LLM не запустился в `primary` — `failed`.** Если LLM не удалось запустить (нет ключа, неизвестная модель), прогон в `ai.mode: primary` сразу завершается `failed` — до чтения Telegram, без записи вакансий и чекпоинтов и без повторной попытки; что исправить — в `error_message`. В `assist` прогон в этом случае идёт на правилах с предупреждением.
- **Docker Compose:** у сервисов приложения `restart: unless-stopped` и healthcheck; воркеры стартуют после того, как backend стал healthy (то есть применил миграции).
- **Миграции под замком.** `alembic upgrade head` при старте выполняется под `pg_advisory_lock`: второй процесс или реплика API ждёт, пока первый применит миграции, вместо гонки DDL.
- **Пул соединений по роли процесса.** API — 20 + 20 overflow (по числу потоков sync-эндпоинтов), `parse-worker` — 4 + 12 (LLM-потоки прогона ходят в AI-кэш), `llm-worker` и `hh-worker` — 2 + 2. Так `--scale llm-worker=4` укладывается в `max_connections=100` Postgres. Переопределение — `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`; фактические значения — метрики `db_pool_*`.
- **Liveness воркеров.** Healthcheck воркера проверяет не ответ порта, а возраст heartbeat в `/metrics` (`worker_heartbeat_timestamp_seconds`): его обновляют цикл RQ (в простое раз в 45 с) и продление lease выполняемой джобы. Старше `WORKER_HEARTBEAT_MAX_AGE_SECONDS` (180 с) — `unhealthy`. Docker unhealthy-контейнер сам не перезапускает, поэтому воркер, чей heartbeat молчит дольше `WORKER_STALL_EXIT_SECONDS` (900 с), завершается сам, restart policy поднимает его, а recover возвращает джобу в очередь.

SSE (`/runs/{id}/events`, `/llm-jobs/{id}/events`, `/hh/sync-jobs/{id}/events`): один подписчик `redis.asyncio` на процесс API раздаёт события всем стримам — соединения не занимают потоки, а БД читается вне event loop. Каждые 15 с тишины стрим шлёт комментарий `: ping` (прокси не рвут соединение по idle-timeout), раз в 5 с сверяет статус с БД на случай пропущенного `done`. Без Redis или при его сбое стрим переходит на опрос БД и живёт до терминального статуса.

Потоковая обработка прогона: каналы читаются в фоновом потоке, и каждый прочитанный канал сразу уходит в разбор (rules → LLM → дедуп → UPSERT), не дожидаясь остальных. Очередь между вычиткой и разбором рассчитана на `parser.channel_concurrency` каналов: если разбор (обычно LLM) отстаёт, вычитка ждёт. В памяти держится несколько каналов, а не всё окно прогона, и сбой посреди вычитки не теряет уже разобранное. Пока идёт вычитка, прогресс показывает фазу `fetching` и число прочитанных каналов; общее число постов при этом растёт.

Инкрементальная вычитка каналов:

- **Чекпоинты.** Для каждого канала, прочитанного сплошь до границы окна, сохраняется чекпоинт «прочитано всё с даты X по сообщение N» (`telegram_channel_checkpoints`). Следующий прогон читает из канала только сообщения новее N — ежедневный запуск не перечитывает недельное окно заново. Чекпоинт пишется сразу, как только посты канала разобраны и записаны в БД, поэтому прогон, перезапущенный после падения воркера, не перечитывает готовые каналы. Чекпоинт не двигается, если канал прочитан не до конца или его вакансии не записались; после первого поста, потерянного из-за LLM (в `primary` — сбой LLM на посте, вне `assist` — отключение LLM), чекпоинты до конца прогона больше не двигаются. Полный перечит окна (после смены фильтров или промпта) — галочка «Перечитать окно дат целиком» в форме запуска. Подробности — `parser.incremental_fetch` в [config.md](config.md).
- **Окно в прошлом.** Если `date_to` раньше сегодняшнего дня, обход начинается с `date_to` (`offset_date`), а не перебирает всё, что новее окна, расходуя `max_posts_per_channel`.
- **FloodWait.** Ожидание до `parser.flood_wait_max_seconds` пережидается с продолжением обхода с места остановки; более долгое или ошибка посреди канала возвращают уже прочитанные посты с предупреждением, а не пустой канал.
- **Паузы между страницами.** `parser.sleep_between_requests_seconds` (по умолчанию `1`) — страницы истории канала (100 сообщений) запрашиваются не чаще раза в N секунд; сколько она стоит, видно в логе канала, в span `telegram.channel` и в `telegram_request_pause_seconds_total`.
- **Дедуп** внутри прогона сравнивает fuzzy-ключи только в блоке с тем же набором специализаций (`parser.fuzzy_dedup_block_by_specializations`), а снимок прогресса несёт последние 30 вакансий, а не копию всех уникальных.

---

## Backend API (FastAPI)

Префикс: `/api/v1`.

| Метод | Путь | Назначение |
|-------|------|------------|
| `GET` | `/health` | Статус API и подключения к БД |
| `POST` | `/auth/register` / `/auth/login` | Регистрация / вход → JWT access-токен + refresh-cookie |
| `POST` | `/auth/refresh` | Новый access-токен по refresh-cookie; cookie ротируется (401 — сессии нет) |
| `POST` | `/auth/logout` | Отозвать текущую refresh-сессию, стереть cookie (204) |
| `POST` | `/auth/sse-ticket` | Одноразовый тикет (30 с) для `?ticket=` SSE-эндпоинтов |
| `GET` | `/auth/me` | Текущий пользователь |
| `POST` | `/auth/change-password` | Смена пароля → новый токен и сессия; прежние токены и сессии отозваны |
| `POST` | `/auth/logout-all` | Отозвать все токены и сессии пользователя (204) |
| `GET` | `/config/defaults` | Значения по умолчанию для формы |
| `GET` | `/config/llm-models` | Каталог моделей из `llm_models.yaml` |
| `POST` | `/runs` | Постановка прогона в очередь (admin; `queued` → parse-worker / inprocess) |
| `GET` | `/runs` \| `/runs/{id}` | История прогонов / статус, метрики и durable `progress` (admin) |
| `GET` | `/runs/{id}/events` | SSE: прогресс (admin; Redis pub/sub или DB poll); после F5 — resume; `?ticket=` или Bearer |
| `POST` | `/runs/{id}/cancel` | Отмена (admin; флаг в БД; работает после рестарта API) |
| `GET` | `/runs/{id}/vacancies` | Вакансии прогона (admin; `sort_by`, `sort_order`): все, что прогон встретил, — и новые, и уже известные; повторная встреча в следующем прогоне их из старого не убирает (связка `parse_run_vacancies`), архивные по TTL тоже остаются — с пометкой «архив» у даты |
| `GET` | `/runs/{id}/export.xlsx` / `.json` | Скачивание отчёта (admin) |
| `GET` | `/vacancies` | Глобальный список (slim: `preview` без полных текстов; фильтры, сортировка, пагинация, `resume_id`) |
| `GET` | `/vacancies/{id}` | Полная вакансия (`raw_text` / `cleaned_text`) для карточки |
| `POST` | `/vacancies/import` | Импорт вакансий из текста (admin): `{texts, source_url?}` → rules + LLM → запись в БД |
| `GET` | `/hh/status` | Статус HH sync (admin): токен задан?, TTL listings, счётчики кэша |
| `POST` | `/hh/sync` | HH sync в фоне (admin): 202 `{job_id}`; 409, если sync уже идёт. Поиск → dedup → detail только для новых → БД |
| `GET` | `/hh/sync-jobs` \| `/hh/sync-jobs/{id}` | Последние sync / один sync (admin): статус, прогресс, итог (у `failed` — частичный) |
| `GET` | `/hh/sync-jobs/{id}/events` | SSE статуса HH sync (admin; `?ticket=` или Bearer) |
| `POST` | `/hh/sync-jobs/{id}/cancel` | Отмена HH sync (admin): `queued` → `cancelled` сразу, `running` — флаг; ответ — текущий sync |
| `GET` | `/vacancies/metrics` | Агрегаты для графиков по текущим фильтрам |
| `GET` | `/vacancies/export.xlsx` | Excel всех вакансий в БД (те же фильтры, что у списка) |
| `GET` | `/analytics/salaries` \| `/sources` \| `/companies` | Зарплатные квантили, качество каналов, топ компаний |
| `GET` `POST` `DELETE` | `/filters` | Пресеты фильтров (приватные) |
| `GET` `POST` `PUT` `DELETE` | `/resumes` | Резюме пользователя |
| `POST` `GET` | `/resumes/{id}/match/{vacancy_id}` | Оценка: свежая из БД → 200; нет / устарела (`stale`) → фоновая джоба → 202 `{job_id}` (`force=true` — пересчёт) |
| `POST` `GET` `DELETE` | `/resumes/{id}/cover-letter/{vacancy_id}` | Письмо: свежее того же тона → 200; иначе 202 `{job_id}` (`force`, `tone`/`extra_notes`) |
| `GET` | `/llm-jobs/{job_id}` | Статус LLM-задачи; при `done` — payload match/letter |
| `GET` | `/llm-jobs/{job_id}/events` | SSE статусов (`?ticket=` или Bearer); poll остаётся fallback |
| `POST` | `/llm-jobs/{job_id}/cancel` | Отмена своей LLM-задачи: `queued` → `cancelled` сразу, `running` — флаг; ответ — текущая задача |
| `GET` | `/fx/rates`, `POST` `/fx/refresh` \| `/fx/recalculate-rub` | Курсы ЦБ и пересчёт зарплат в ₽ (`POST` — admin) |
| `GET` | `/ai-cache/stats` | Число записей в кэше LLM |
| `POST` | `/ai-cache/clear` | Полная очистка кэша LLM (admin) |

Без логина доступны только `/health`, `/auth/register` и `/auth/login`; всё остальное
требует JWT. Только админ (`require_admin`, иначе 403): весь `/runs/*`,
`POST /vacancies/import`, `POST /fx/*`, `POST /ai-cache/clear` и HH.
`API_KEY`, если задан, — дополнительный заголовок `X-API-Key` поверх JWT, а не замена ему.

Query-параметры списка, метрик, аналитики и экспорта типизированы на уровне FastAPI:
`page` 1…10 000, `page_size` 1…500 (списки листаются только до 10 000-й позиции: дальше OFFSET дорог, 422 с просьбой уточнить фильтры), даты — `YYYY-MM-DD` (пустое значение = не задано), `q` до 500
символов; нарушение — 422 `VALIDATION` с описанием поля, а не 500. Тексты в телах ограничены:
резюме — 50 000 символов, вакансия в импорте — 30 000.

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

`POST /vacancies/import` без LLM (AI выключен или все тексты уже в базе) отвечает
сразу 200 с итогом. Если нужен LLM — 202 `{job_id}`: разбор идёт LLM-джобой
`kind=import` (та же очередь, lease, recover и SSE, что у мэтчинга), до 50 вызовов
модели не держат HTTP-запрос и транзакцию. Итог по каждому тексту пишется в
`llm_jobs.result` по мере обработки: `GET /llm-jobs/{id}` отдаёт его в
`import_result` ещё до завершения (страница «Импорт» показывает «N из M»), а
ретрай после падения воркера продолжает с первого необработанного текста. Импорт
занимает один слот `ai.interactive_per_user_limit`; таймаут RQ — базовый плюс
60 с на текст.

Если LLM в воркере не запустился (нет ключа, неизвестная модель), импорт-задача
завершается `error` с причиной, а тексты не сохраняются: без LLM нет проверки
«это вакансия?». Итог по уже обработанным текстам остаётся в `result`. Исчерпанная
суточная квота пользователя, наоборот, не ошибка — остаток текстов разбирается
правилами. В `ai.mode: primary` текст сверх `ai.max_posts_per_run` получает
статус `error` с причиной и не сохраняется.

Пока страница «Импорт» ждёт фоновую задачу, рядом с кнопкой разбора есть
«Отменить» (`POST /llm-jobs/{id}/cancel`): импорт останавливается перед следующим
текстом, уже разобранные тексты остаются в базе, страница показывает итог по ним
и пометку об отмене; текст в поле не очищается.

Вакансии с HH (`source_channel = "hh"`) появляются после admin-sync
(`/hh` или `POST /hh/sync`). Sync — фоновая durable-джоба (`hh_sync_jobs`,
lease и recover как у прогонов; в Docker — `hh-worker`, без Redis —
`HH_SYNC_JOB_BACKEND=inprocess`): до ~2000 detail-запросов при 3 rps идут
10+ минут, внутри HTTP-запроса их обрывал бы прокси. Страница `/hh` показывает
ход и итог последнего sync и после перезагрузки; идущий sync можно отменить
кнопкой «Отменить» (`POST /hh/sync-jobs/{id}/cancel`) — итог до отмены сохраняется. Поля берутся из API без LLM, дедуп мультигорода
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

Кэш ответов LLM хранится в PostgreSQL (`ai_extraction_cache`). Параметры в `config.yaml`: `ai.cache_backend: postgres`, `ai.cache_ttl_days` (0 — без срока, иначе дни; по умолчанию 45). Просроченные записи и записи от прежних версий промпта удаляет почасовая фоновая очистка. Очистка — кнопка на странице **Парсинг** или `POST /ai-cache/clear`. Адаптер Postgres регистрируется при старте backend/CLI (порт `AIExtractionCacheBackend` в core, без зависимости `tg_jobs_parser` → `backend`).

### Схема БД

Миграции инкрементальные (`alembic/versions/001…031`, номера 005 нет; head —
`031_jobs_cancel_requested`). При старте backend применяет `upgrade head`.

| Таблица | Назначение |
|---------|------------|
| `parse_runs` | Журнал прогонов: статус, метрики, снимок конфигурации |
| `vacancies` | Уникальные вакансии (`id` = content-hash или `sha256(hh:{id})` для HH), `archived_at` — TTL |
| `hh_listings` | Кэш короткой выдачи HH + `detail_fetched_at` (anti-DDOS detail) |
| `hh_sync_jobs` | Фоновые HH sync: статус, lease, прогресс, итог, `cancel_requested` |
| `exchange_rates` | Курсы ЦБ по датам |
| `ai_extraction_cache` | Кэш ответов LLM при парсинге |
| `saved_filters` | Пресеты фильтров (`user_id`) |
| `resumes` | Резюме пользователя (`user_id`) |
| `vacancy_matches` | Оценки соответствия, PK `(resume_id, vacancy_id)` — он же кэш матчинга; `requested_model` + `prompt_version` — для `stale` |
| `llm_jobs` | Фоновые LLM-задачи: мэтчинг, письма, ручной импорт (`queued`/`running`/`done`/`error`/`cancelled`) |
| `cover_letters` | Сопроводительные письма, PK `(resume_id, vacancy_id)` — он же кэш генерации; `requested_model` + `prompt_version` — для `stale` |
| `users` | Аккаунты: email, argon2-хэш, `is_admin` |
| `telegram_channel_checkpoints` | Чекпоинт канала для инкрементальной вычитки: `last_message_id`, `covered_from` |
| `llm_user_usage` | Вызовы LLM пользователя за сутки — квота `LLM_USER_DAILY_CALLS` |
| `refresh_tokens` | Refresh-сессии: sha256 токена, семья (один вход), ротация и отзыв |
| `idempotency_keys` | Сохранённые ответы мутаций по `Idempotency-Key` (TTL) |
| `parse_run_vacancies` | Какие вакансии встретил прогон (история и экспорт прогона) |
| `vacancy_sightings` | Каждое появление поста Telegram: оригинал или дубль со ссылкой на вакансию |

Служебные таблицы чистятся сами (при старте и раз в час): просроченный кэш LLM и ключи идемпотентности, завершённые LLM-джобы старше `retention.llm_jobs_ttl_days` (30) и прогоны старше `retention.parse_runs_ttl_days` (180) — см. [config.md](config.md). Вакансии только архивируются, не удаляются.

**Если меняете колонки/таблицы** — добавьте новую миграцию (`alembic revision`) и обновите модели в `backend/db/models.py`; при следующем `docker compose up` она применится автоматически, данные сохранятся.

Ядро парсинга (`tg_jobs_parser.parse_runner` + общий `tg_jobs_parser.ingestion`) вызывается из `backend/workers/parse_tasks.py` (`run_service` только ставит прогон в очередь); результаты пишутся в PostgreSQL (UPSERT по `id` вакансии). Ручной импорт использует тот же ingestion с `MANUAL_POLICY`.

---

## Структура проекта

```text
tg_jobs_parser/
  README.md
  pyproject.toml
  config.yaml
  llm_models.yaml
  docker-compose.yml       — postgres, redis, backend, parse-worker, llm-worker, hh-worker,
                             frontend, pg-backup; observability — profile `observability`
  Dockerfile.backend
  scripts/pg_backup.sh     — плановый pg_dump + проверка восстановления (сервис pg-backup)

  backend/                 — FastAPI, SQLAlchemy, RunService, SSE
    api/routes/            — health, auth, config, runs, vacancies, hh,
                             analytics, filters, resumes, fx, ai-cache, llm-jobs
    db/                    — модели, Alembic, bounded repos (+ repository facade),
                             кэш LLM (DbAIExtractionCache), UPSERT
    services/              — run/match/cover_letter/llm_job/interactive_llm,
                             llm_job_events, parse_run_events, auth, import, hh_sync(_job)
    workers/               — parse/llm/hh_worker (RQ, общий runner), *_tasks, recover*,
                             liveness (heartbeat + watchdog), healthcheck
    queue/                 — RQ Redis connection (очереди parse, llm, hh)
  alembic/                 — миграции SQL (001…031)

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
  .cache/ai_extractions/   — файловый кэш LLM (если ai.cache_backend=file)
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
  Это же размер очереди прочитанных, но ещё не разобранных каналов.
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
| `mode` | `assist` — LLM уточняет поля правил; `primary` — LLM основной источник, без LLM пост не разбирается. Какие поля откуда берутся — [config.md](config.md#как-llm-и-правила-делят-поля-mode) |
| `merge.prefer_ai_for`, `merge.min_confidence` | `assist`: в каких полях и с какой уверенностью LLM перекрывает правила |
| `model` | Id из `llm_models.yaml` — модель парсинга вакансий |
| `match_model` | Модель матчинга резюме ↔ вакансия (обычно reasoning). Пусто — берётся `model` |
| `match_reasoning_enabled` | `true`, если `match_model` — reasoning-модель (OpenRouter `reasoning` вместо `json_object`) |
| `match_reasoning_effort` | Уровень рассуждений матчинга и писем: `max` / `xhigh` / `high` / `medium` / `low` / `minimal` / `none`. Только при `match_reasoning_enabled: true`; пусто — дефолт модели. Неподдерживаемый уровень OpenRouter сводит к ближайшему (напр. Gemini 3: `xhigh` → `high`); модели без выбора уровня его игнорируют. Рассуждения расходуют `max_completion_tokens` — на `xhigh`/`max` лимит стоит поднять |
| `cover_letter_model` | Модель генерации сопроводительных писем. Пусто — `match_model`, затем `model` |
| `fallback_models` | Запасные модели |
| `concurrency`, `requests_per_minute` | Темп и параллелизм парсинга |
| `interactive_concurrency`, `interactive_per_user_limit` | Пул потоков фоновых мэтч/письмо/импорт — только при `LLM_JOB_BACKEND=inprocess` (при `rq` параллелизм = число реплик `llm-worker`); лимит активных LLM-джоб на пользователя — в обоих режимах |
| `stop_llm_when_rate_limited` | Отключить LLM при 429 до конца прогона. В `mode: primary` оставшиеся посты не разбираются, и прогон завершается `partial` (см. «Самовосстановление джоб») |
| `circuit_breaker_errors` | Столько постов подряд с ошибкой LLM — и LLM отключается до конца прогона (по умолчанию 5, `0` — выключен) |
| `max_error_ratio` | `mode: primary`: доля ошибок LLM, выше которой прогон — `partial` (по умолчанию 0.1) |
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

### Резервное копирование (pg_dump по расписанию)

Сервис `pg-backup` в `docker-compose.yml` (входит в ядро, не в профиль observability)
раз в `BACKUP_INTERVAL_HOURS` (по умолчанию 24) снимает `pg_dump -Fc` **всей** базы —
пользователи, резюме, матчи, письма, пресеты, HH, чекпоинты каналов, квоты LLM.
Логика — `scripts/pg_backup.sh`.

- **Проверка восстановления — на каждом дампе.** Дамп восстанавливается во временный
  кластер внутри контейнера (боевой Postgres не нагружается), затем сверяются набор
  таблиц и `alembic_version` с источником; таблица, где в источнике есть строки, а после
  восстановления пусто, — провал. Не прошедший проверку дамп остаётся как
  `*.dump.unverified`, повтор — через `BACKUP_RETRY_MINUTES` (60).
- **Где лежит.** `BACKUP_HOST_DIR` на хосте (по умолчанию `./backup/pg`, в `.gitignore`):
  `tgjobs_<UTC-время>.dump` + `.counts` (число строк по таблицам после восстановления).
  Каталог на хосте переживает `docker compose down -v`, но не потерю диска — копию за
  пределы машины нужно настроить отдельно.
- **Ротация.** Хранятся последние `BACKUP_KEEP` (7) проверенных дампов.
- **Мониторинг.** Контейнер `unhealthy`, если последний проверенный дамп старше
  1,5 × `BACKUP_INTERVAL_HOURS`; каждый шаг пишется в stdout (в Loki через Alloy).
  Рестарт контейнера дамп не повторяет — отсчёт идёт от последнего удачного.

```powershell
# внеплановый дамп сейчас
docker compose exec pg-backup /scripts/pg_backup.sh once
# проверить восстановление существующего дампа (любого, в т.ч. снятого вручную)
docker compose exec pg-backup /scripts/pg_backup.sh check /backups/tgjobs_20261015_030000.dump
```

**Восстановление** — в пересозданную базу: `pg_restore --clean` поверх существующей
оставил бы таблицы из миграций новее дампа, и `alembic upgrade` при старте упал бы.

```powershell
docker compose stop backend parse-worker llm-worker hh-worker
docker compose exec pg-backup dropdb --force tgjobs
docker compose exec pg-backup createdb tgjobs
docker compose exec pg-backup pg_restore -d tgjobs --no-owner --exit-on-error /backups/tgjobs_20261015_030000.dump
docker compose start backend parse-worker llm-worker hh-worker
```

Backend при старте догонит схему миграциями, если дамп старше кода.

### Перенос данных между инстансами (дамп / импорт БД)

Это **не** бэкап (см. выше), а перенос корпуса вакансий и служебных таблиц
(прогоны, вакансии, курсы ЦБ, кэш LLM) в JSON. Пользователи, резюме, матчи,
письма, пресеты и `hh_listings` в дамп **не** входят:

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
`replace` — сначала очищает таблицы дампа (прогоны, курсы, вакансии, связки прогон–вакансия,
появления, кэш LLM), затем загружает дамп. Вместе с вакансиями каскадом удаляются оценки
соответствия, письма и LLM-задачи пользователей по вакансиям — в дамп они не входят;
подтверждение называет, сколько их удалится.

Логи: консоль + `logs/app.log` (в Docker — только stdout, их собирает Alloy в Loki). Запись асинхронная: `QueueHandler` на root кладёт запись в очередь, stdout/файл пишет фоновый `QueueListener` — логирование не блокирует event loop. `app.log` ротируется по 10 МБ, хранится 5 частей `app.log.N.gz`. Stdout контейнеров в `docker-compose.yml` — `json-file` с ротацией (5 × 10 МБ, сжатие ротированных частей).

---

## Результаты и экспорт

- **PostgreSQL** — постоянное хранение уникальных вакансий и журнал прогонов (`parse_runs`).
- **Экспорт из UI** — `GET /runs/{id}/export.xlsx` и `.json` (листы и метрики как в `exporters/`).
- **Потолок экспорта.** xlsx списка и прогона и JSON прогона выгружают не больше `EXPORT_MAX_ROWS` вакансий (по умолчанию 50 000) в текущей сортировке. Если под фильтр попало больше, в листе `metrics_summary` появляется строка `truncated` («обрезано до N из M»), в ответе — заголовок `X-Export-Truncated: N/M`, а UI показывает предупреждение. xlsx пишется потоком (openpyxl `write_only`, строки читаются из БД одним запросом порциями) — в памяти не держится весь корпус.

---

## Observability (Prometheus + Grafana + Loki)

Стек метрик/логов/трасс поднимается вместе с приложением, если в `.env`
задан `COMPOSE_PROFILES=observability` (дефолт из `.env.example`):

| Компонент | Назначение |
|-----------|------------|
| **`prometheus-fastapi-instrumentator`** | Счётчики запросов, статус-коды, latency на backend |
| **`GET /metrics`** | Эндпоинт Prometheus на отдельном порту `API_METRICS_PORT` (`backend:9100`, только Docker-сеть; не путать с `/api/v1/vacancies/metrics`) |
| **Prometheus** | Scrape backend, llm-worker, parse-worker и hh-worker каждые 15 с (`observability/prometheus.yml`) |
| **Grafana** | Дашборды **API**, **LLM**, **LLM jobs / queue**, **Pipeline**, **Parse run**, **HH.ru sync**, **DB pool** |
| **`llm_*` метрики** | Вызовы OpenRouter, токены, latency, кэш и пропуски при AI-enrichment; ожидание RPM-лимита (`llm_rate_gate_wait_seconds`), повторы (`llm_retries_total{reason}`), оценка стоимости по ценам `llm_models.yaml` (`llm_cost_usd_total{model}`) |
| **Метрики прогона** | Живые, во время прогона: каналы и посты Telegram (`telegram_channels_fetched_total{result}`, `telegram_posts_fetched_total`, `telegram_channel_fetch_duration_seconds`), паузы между страницами истории (`telegram_request_pause_seconds_total`), FloodWait — в т.ч. короткие, которые Telethon пережидает сам (`telegram_flood_waits_total{handled_by}`), поток обработанных постов (`parse_posts_processed_total{result}`), длительность фаз (`parse_phase_duration_seconds{phase}`) |
| **Доменные метрики** | прогоны, UPSERT/TTL, импорт, матчинг, письма, LLM-джобы (`llm_job_enqueue_*`, `llm_worker_*`, `llm_jobs_by_status`, recover), глубина RQ-очередей (`llm_rq_queue_jobs{queue="llm"|"parse"}`), parse recover (`parse_jobs_recover_total`), auth, ЦБ |
| **`db_*` gauges** | COUNT-агрегаты по таблицам с кэшем `METRICS_DB_STATS_CACHE_SECONDS` (30 с) между scrape (вакансии, резюме, матчи, письма, прогоны, LLM-кэш, свежесть курсов ЦБ); только на backend (`job="tg-jobs-backend"`) |
| **`db_pool_*` gauges** | SQLAlchemy QueuePool: занято / свободно / overflow / лимит (`pool_size` + `max_overflow`); на backend, llm-worker и parse-worker |
| **Loki** | Хранение логов (filesystem, retention 30 дней — `observability/loki-config.yml`) |
| **Alloy** | Собирает stdout контейнеров compose-проекта через `docker-socket-proxy` (только чтение контейнеров, сетей и логов; сам `docker.sock` в Alloy не монтируется, Alloy работает не под root), парсит `level`/`logger` (labels) и `request_id`/`trace_id` (structured metadata) из формата логов и шлёт в Loki; принимает OTLP-трассы от backend и воркеров, отбрасывает шумные span'ы `connect` SQLAlchemy и отправляет в Tempo (`observability/alloy-config.alloy`) |
| **Tempo** | Хранение трасс (local, retention 7 дней — `observability/tempo-config.yml`) |

**Затор очереди LLM:** смотреть дашборд **LLM jobs / queue** — растут `llm_rq_queue_jobs` и `llm_jobs_by_status{status="queued"}`; worker latency в `llm_worker_job_duration_seconds`. TraceQL: `{ name = "llm_job.run" }`, `{ name = "llm_job.enqueue" }`.

**Прогон парсинга:** дашборд **Parse run** — итоги за выбранный период (посты, кандидаты, LLM-вызовы, стоимость, FloodWait), время по фазам, воронка AI, живой поток постов, Telegram fetch, LLM (полный вызов vs ожидание RPM-лимита, повторы, $/мин), воркеры (heartbeat, RAM, CPU, `up`), таблица трасс `parse_run` и логи parse-worker. Трасса: `parse_run` → `parse.fetch_posts` → `telegram.channel` (канал, постов, просмотрено, секунды пауз `telegram.pause_seconds`, events `telegram.flood_wait`); `parse.process_posts` → `chat <model>` (атрибуты `llm.rate_gate.wait_seconds`, `llm.retries`, `llm.cost_usd`) и `parse.flush_vacancies`. У каждого процесса свой `service.name` (`OTEL_SERVICE_NAME`): `tg-jobs-parser-parse-worker` и т.д. TraceQL: `{ resource.service.name = "tg-jobs-parser-parse-worker" && name = "parse_run" }`.

**Пул БД:** дашборд **DB pool** — `db_pool_checked_out` / `db_pool_checked_in` / `db_pool_overflow` / `db_pool_max_connections`. Утилизация ≈ `checked_out / max_connections` (по job).

Конфигурация: `observability/prometheus.yml`, `observability/loki-config.yml`, `observability/tempo-config.yml`, `observability/alloy-config.alloy`, provisioning Grafana — `observability/grafana/provisioning/` (datasources Prometheus, Loki и Tempo с переходами лог ↔ трасса).

**HTTP-лог** (`backend/http_logging.py`, логгер `backend.http`, вместо `uvicorn.access`). Каждому запросу присваивается `request_id` (UUID; валидный входящий `X-Request-ID` переиспользуется), он возвращается в заголовке ответа `X-Request-ID` и попадает в **каждую** строку лога, записанную во время запроса (и в логи роутов/сервисов): `... | backend.http | request_id=<uuid> | ...`. На запрос — две записи:

- `REQUEST METHOD path {json}` — до обработки: клиент, заголовки, тело;
- `RESPONSE METHOD path status Nms {json}` — после ответа: статус, длительность, заголовки, тело.

Тела пишутся для JSON / form / `text/*` / XML (SSE, бинарные ответы и `Content-Disposition: attachment` — без тела). Маскируются `Authorization` (остаётся схема: `Bearer ***`), `Cookie`/`Set-Cookie`, `X-API-Key`, query-параметры и ключи тела вида `password`/`token`/`access_token`/`ticket`/`secret`/`api_key`. Уровень `RESPONSE`: 4xx — `WARNING`, 5xx — `ERROR`. Не логируются шумные эндпоинты из `HTTP_LOG_EXCLUDE_PATHS` (по умолчанию health-check, агрегаты `/vacancies/metrics` и `/analytics/*`, Swagger/ReDoc/OpenAPI, favicon) и CORS preflight (`OPTIONS` с `Access-Control-Request-Method`).

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
| Входящие HTTP | FastAPI-инструментация; span'ы `send`/`receive` отключены, пути из `HTTP_LOG_EXCLUDE_PATHS` не трассируются, секреты в query (`?ticket=` и т.п.) маскируются в атрибутах. Атрибут `request_id` — тот же, что в логах и `X-Request-ID` |
| SQL | SQLAlchemy-инструментация (текст запроса с плейсхолдерами, без значений параметров) |
| Исходящие HTTP | httpx-инструментация: OpenRouter (через openai SDK), ЦБ |
| LLM | `chat <model>`: `gen_ai.request.model`, `gen_ai.usage.input_tokens`/`output_tokens`, `llm.task` (`extract`/`match`/`cover_letter`), `llm.outcome`. Промпты и ответы в span не пишутся |
| Прогон парсинга | Отдельная трасса `parse_run` (со ссылкой на `POST /runs`): `parse.fetch_posts` (Telethon, фоновый поток) и параллельно `parse.process_posts` → LLM-вызовы из пула потоков (контекст пробрасывается в воркеры) |
| Матчинг / письма / курсы ЦБ | `vacancy.match`, `cover_letter.generate`, `fx.sync_on_startup` |

Sampling: доля трасс — `OTEL_TRACES_SAMPLE_RATIO`. Sampler отбрасывает SQL/HTTP-span'ы без родителя: COUNT'ы `db_*`-коллектора при scrape `/metrics` и фоновые запросы иначе дали бы тысячи трасс из одного span'а. Без `OTEL_ENABLED` (CLI, тесты) API OpenTelemetry работает как no-op.

**Связь логов и трасс.** Внутри span'а каждая строка лога получает `trace_id`: `... | request_id=<uuid> trace_id=<32 hex> | ...`. Alloy кладёт его в structured metadata. В Grafana:

- в Loki у строки с `trace_id` есть кнопка **«Открыть трассу»** (derived field);
- в Tempo у span'а есть **«Logs for this span»**: это логи с тем же `trace_id`;
- поиск в Tempo (TraceQL): `{ span.request_id = "<uuid>" }`, `{ name = "parse_run" }`, `{ span.llm.task = "match" && duration > 10s }`.

Из health-check и OpenAPI HTTP-метрики не собираются (шум от probes и документации). LLM-метрики пишутся при парсинге с `ai.enabled: true` в том же процессе backend. При недоступной БД `db_*` gauges отдают пустой набор — scrape не падает.

---

## Тесты

```powershell
pytest
pytest -n 8     # параллельно (pytest-xdist)
```

Тесты идут на Postgres 16: `tests/conftest.py` поднимает `postgres:16-alpine` через
testcontainers (нужен запущенный Docker). Готовый сервер вместо контейнера —
`TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/db` (роль с правом
CREATEDB; тесты создают и удаляют свои базы `tgjobs_test_*`).

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
                              │                              ├── llm-worker (match / cover letter / import)
                              │                              └── hh-worker → HH.ru API
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

# План разработки

Семь фичей, каждая — в своей ветке `feature/<name>` от `master`. Порядок
выстроен по соотношению ценность/трудозатраты; 1–3 независимы и при желании
могут идти в другом порядке. №6 имеет смысл поднять выше, если приложение
перестаёт быть чисто локальным. №7 опирается на `is_admin` из №6.

## Ветки

| # | Ветка | Фича | Изменение схемы БД |
|---|-------|------|--------------------|
| 1 | `feature/analytics-dashboard` | Дашборд зарплат + аналитика каналов/компаний | нет |
| 2 | `feature/application-tracker` | Трекер откликов | да (миграция 005) |
| 3 | `feature/fts-saved-filters` | Полнотекстовый поиск + сохранённые фильтры | да (миграция 004) |
| 4 | `feature/resume-matching` | Оценка соответствия резюме | да (миграция 006) |
| 5 | `feature/external-export` | Экспорт в Google Sheets / Notion | нет |
| 6 | `feature/auth-multiuser` | Аутентификация, per-user резюме/фильтры/матчи | да (миграция 007) |
| 7 | `feature/admin-vacancies` | CMS-админка: редактирование/удаление вакансий | нет |

**Про миграции:** цепочка уже инкрементальная — head сейчас
`007_auth_multiuser`. Новые фичи просто продолжают её
(`alembic revision` → `upgrade head` на старте backend, без `down -v`).

---

## 1. `feature/analytics-dashboard` — дашборд зарплат и источников ✅

Чисто read-only фича: новые агрегирующие эндпоинты + новая страница в UI.
Данные уже есть: `detected_salary_avg_rub` (нормализовано через `fx`),
`detected_grade`, `detected_specializations`, `company`, `source_channel`,
`post_date`. Быстрый результат без риска.

> **Реализовано.** Отступление от плана: вместо отдельной страницы блоки
> добавлены на существующую вкладку «Аналитика» (`VacanciesPage` →
> `AnalyticsPanel`) — там уже были общие фильтры и `MetricsCharts`.
> Эндпоинты: `GET /api/v1/analytics/salaries|sources|companies`.

**Backend**

- `GET /api/v1/analytics/salaries` — медиана/квартили/распределение
  `detected_salary_avg_rub` с группировкой по `detected_grade`,
  специализации и `work_format`; бакеты по месяцам `post_date` для динамики.
- `GET /api/v1/analytics/sources` — по каждому `source_channel`: число
  вакансий, % с зарплатой, средний `vacancy_score`, доля уникальных
  (`is_duplicate = false`) — «качество источника».
- `GET /api/v1/analytics/companies` — топ `company` по числу вакансий.
- Все эндпоинты принимают те же фильтры, что `GET /vacancies` (переиспользовать
  `VacancyQuery`, паттерн `backend/db/vacancy_metrics.py`).

**Frontend**

- Страница `AnalyticsPage.tsx` + пункт меню. Графики на Recharts:
  box-plot/гистограмма зарплат по грейдам, линия медианы по месяцам,
  таблица «качества каналов», топ-компании.

**Готово, когда:** страница показывает зарплатные распределения и рейтинг
каналов по реальным данным; pytest на агрегаты.

---

## 2. `feature/application-tracker` — трекер откликов

Мини-ATS поверх вакансий: статус (new → applied → interview → offer /
rejected / skipped) + заметки.

**БД (миграция 005)**

- Таблица `vacancy_applications`: `vacancy_id` (FK → vacancies),
  `status`, `applied_at`, `notes`, `updated_at`. Отдельная таблица, а не
  колонки — оставляет место под историю статусов позже.

**Backend**

- `PATCH /api/v1/vacancies/{id}/application` — upsert статуса/заметок.
- `GET /api/v1/vacancies` — новый фильтр `application_status` + поля
  статуса в ответе (LEFT JOIN).

**Frontend**

- Колонка «Статус» в `VacancyTable` с выпадающим списком; редактирование
  заметок в `VacancyModal`; фильтр по статусу; опционально отдельная
  страница «Мои отклики» (все с `status != new`).

**Готово, когда:** статус ставится из таблицы и модалки, фильтруется,
переживает перезапуск; миграция применяется `alembic upgrade head` на старте
backend без `down -v`.

---

## 3. `feature/fts-saved-filters` — полнотекстовый поиск и пресеты ✅

Заменить `ILIKE '%q%'` на Postgres FTS и дать именовать наборы фильтров.

> **Реализовано.** Вместо generated-колонки — функциональный GIN-индекс
> на `to_tsvector('russian', cleaned_text)` (совместимо с SQLite-тестами,
> ORM-модель не меняется). Поиск — `websearch_to_tsquery`, сортировка по
> `ts_rank` при заданном `q`. CRUD: `GET/POST/DELETE /api/v1/filters`,
> панель пресетов на `VacanciesPage`.

**БД (миграция 004)**

- `vacancies`: функциональный GIN-индекс `to_tsvector('russian', cleaned_text)`.
- Таблица `saved_filters`: `id`, `name`, `params` (JSON — поля
  `VacancyQuery`), `created_at`.

**Backend**

- `GET /vacancies?q=...` — `plainto_tsquery`/`websearch_to_tsquery` вместо
  ILIKE, сортировка по `ts_rank` при заданном `q`.
- CRUD `/api/v1/filters`: list / create / delete.

**Frontend**

- Панель пресетов на `VacanciesPage`: применить / сохранить текущие фильтры
  / удалить. Поисковая строка уже есть — просто станет быстрее и умнее
  (стемминг, «python разработчик» ≈ «python-разработку»).

**Готово, когда:** FTS отрабатывает по индексу (проверить `EXPLAIN`), пресет
сохраняется и восстанавливает фильтры страницы.

---

## 4. `feature/resume-matching` — матчинг резюме к вакансиям ✅

Самая тяжёлая фича, но инфраструктура готова: OpenRouter-клиент, кэш,
`ai_stats`, каталог моделей в `llm_models.yaml`.

> **Реализовано.** `resumes` + `vacancy_matches` (PK-пара — кэш матчинга,
> повторный прогон оценивает только новые пары). Отдельный промпт
> `build_match_messages` + `VacancyMatchExtraction` (score/explanation/
> skills), `OpenRouterClient.match_with_fallback`. Фоновый воркер
> (`match_service`) с `ai.concurrency`, прогресс по SSE через общий
> `progress_bus` (обобщён до любого снапшота). Эндпоинты:
> `GET/POST/DELETE /api/v1/resumes`, `POST /resumes/{id}/match`,
> `GET /matches/{id}` + `/events` + `/cancel`. `GET /vacancies` принимает
> `resume_id` и `sort_by=match_score`. UI — `ResumePanel` на
> `VacanciesPage`, колонка Match с tooltip-пояснением.

**БД (миграция 006)**

- `resumes`: `id`, `name`, `text`, `skills_json`, `created_at`.
- `vacancy_matches`: `(resume_id, vacancy_id)` PK, `score`, `explanation`,
  `model`, `created_at`.

**Backend**

- `POST /api/v1/resumes` — загрузка текста резюме (MVP — plain text/MD;
  PDF через `pypdf` — отдельным шагом).
- `POST /api/v1/resumes/{id}/match` — пакетный матчинг по фильтру
  (`VacancyQuery`): новый тип промпта в `tg_jobs_parser/ai/prompts.py`,
  кэш-ключ включает sha256 резюме. Ограничить батч (например, top-200 по
  score), отдавать прогресс по SSE как в прогонах.
- `GET /vacancies` — опциональный `resume_id`: сортировка/колонка
  `match_score`.

**Frontend**

- Блок «Резюме» (список, загрузка, кнопка «Оценить совпадение»); колонка
  match-score с tooltip-объяснением.

**Готово, когда:** после загрузки резюме у вакансий появляется скор с
пояснением, повторный матчинг бьёт по кэшу, стоимость видна в `ai_stats`.

---

## 5. `feature/external-export` — Google Sheets / Notion

**Backend**

- Модуль `tg_jobs_parser/exporters/google_sheets.py` — service-account
  credentials (путь к JSON в `.env` / `config.yaml`), upsert в существующий
  spreadsheet или создание нового.
- Опционально `notion.py` — internal integration token, запись в database.
- `POST /api/v1/vacancies/export/sheets` с теми же фильтрами, что и
  `export.xlsx`.

**Frontend**

- Кнопка «Export → Google Sheets» рядом с текущим экспортом; поле ссылок
  на spreadsheet в настройках.

**Готово, когда:** по кнопке создаётся/обновляется лист со всеми вакансиями
по фильтру; секреты только в `.env`.

---

## 6. `feature/auth-multiuser` — аутентификация и per-user данные ✅

> **Реализовано.** `users` + `user_id` в `resumes`/`saved_filters` (nullable;
> legacy-строки наследует первый зарегистрированный — он же `is_admin`,
> задел под №7). `pwdlib[argon2]` + `pyjwt` (HS256, TTL `JWT_TTL_MINUTES`,
> секрет `JWT_SECRET`, без него — эфемерный). Эндпоинты:
> `POST /auth/register` (закрывается `AUTH_REGISTRATION_OPEN=false`),
> `POST /auth/login`, `GET /auth/me`. Зависимость `get_current_user`
> (Bearer-заголовок или `?access_token=` для SSE EventSource); приватные —
> `/filters`, `/resumes`, `/matches`; `GET /vacancies?resume_id=` требует
> владельца резюме. UI: `/login` (вход + регистрация), guard на роутер,
> токен в `localStorage`, 401 → редирект, кнопка «Выйти».

**Когда нужно:** приложение выходит за пределы localhost или им пользуется
несколько человек. Резюме — персональные данные; матчи, пресеты фильтров и
трекер откликов приватны по определению. Для личного локального запуска
достаточно текущего `API_KEY`.

**Выбор решения:** свой лёгкий JWT-auth. Keycloak — несоразмерен (JVM,
>500 МБ RAM, realm-конфиг, отдельная консоль). FastAPI-Users тянет лишние
абстракции для одной таблицы. Внешние IdP — только если появится домен и
несколько сервисов.

**БД (миграция 007)**

- `users`: `id`, `email` (unique), `password_hash`, `is_active`,
  `created_at`.
- `resumes.user_id`, `saved_filters.user_id` → FK users; `vacancy_matches`
  наследует владельца через резюме. Backfill существующих строк на первого
  пользователя.
- Вакансии, прогоны, курсы ЦБ — общий корпус, не размечаем по пользователям.

**Backend**

- `POST /api/v1/auth/register` (или seed первого юзера через CLI-команду),
  `POST /api/v1/auth/login` → JWT access (~1 ч; refresh — опционально),
  `GET /api/v1/auth/me`.
- Зависимость `get_current_user` (вместо/рядом с `verify_api_key`): все
  эндпоинты резюме, фильтров, матчей и (после фичи 2) откликов фильтруют по
  `user_id`. Публичные read-only агрегаты (вакансии, аналитика) — общими.
- Библиотеки: `pwdlib[argon2]` (или `passlib`) + `pyjwt`. Секрет подписи в
  `.env`.

**Frontend**

- Страница логина + guard на роутер; токен в `localStorage`, заголовок
  `Authorization` в `client.ts`; 401 → редирект на логин.

**Альтернативы на будущее (если понадобится внешний IdP)**

- oauth2-proxy / forward-auth + Google/GitHub — ноль auth-кода, но нужен
  домен и провайдер.
- Telegram Login Widget — тематично, но требует бота и публичный домен.
- Logto / Authelia — легче Keycloak, но всё равно отдельный сервис.

**Готово, когда:** два пользователя видят только свои резюме/фильтры/матчи;
незалогиненный запрос → 401; существующие строки после миграции привязаны к
первому пользователю.

---

## 7. `feature/admin-vacancies` — CMS-админка для вакансий

Модерация общего корпуса: сейчас вакансию нельзя ни исправить, ни удалить
(только TTL-архивация). Зависит от №6 — права через `users.is_admin`.

**БД**

- Изменений схемы не нужно: `is_admin` уже есть; правим те же колонки
  `vacancies`. Опционально позже — audit-поля (`edited_by`, `edited_at`),
  если захочется историю правок.

**Backend**

- Зависимость `require_admin` (поверх `get_current_user`, 403 для не-админов).
- `GET /api/v1/admin/vacancies` — тот же `VacancyQuery`, но
  `include_archived`/`is_duplicate` по умолчанию включены.
- `PATCH /api/v1/admin/vacancies/{id}` — частичное редактирование полей
  (grade, специализации, зарплата, company, work_format, `is_duplicate`,
  `archived_at` null/now — «вернуть из архива»).
- `DELETE /api/v1/admin/vacancies/{id}` — hard delete (+ каскад на
  `vacancy_matches`) или `?soft=true` → `archived_at=now`.
- Идемпотентность не критична, но валидация через Pydantic-схему
  `AdminVacancyPatch` (все поля optional).

**Frontend**

- Страница/вкладка «Админка», видимая только при `me.is_admin`: таблица со
  всеми вакансиями (включая архив/дубли), inline-редактирование ключевых
  полей или форма в модалке, кнопки «Архив/Восстановить» и «Удалить» с
  подтверждением.

**Готово, когда:** админ правит поля и удаляет вакансию из UI, изменения
видны в обычном списке; не-админу `/admin/*` отдаёт 403 и пункт меню
скрыт.

---

## Порядок работы над веткой

1. `git checkout -b feature/<name>` от свежего `master`.
2. Миграция (если есть) → backend → тесты → frontend.
3. Проверка: `pytest`, `npm run build`, ручной прогон через UI.
4. Merge в `master`, пункт здесь отмечается `- [x]`.

# План разработки

Нереализованные фичи, каждая — в своей ветке `feature/<name>` от `master`.
Порядок выстроен по соотношению ценность/трудозатраты; №1, №2 и №4 независимы.
№3 опирается на `users.is_admin`.

Уже сделанное в план не входит — описание возможностей см. в
[README](README.md): аналитика, полнотекстовый поиск и пресеты, матчинг
резюме, сопроводительные письма, JWT / multi-user.

## Ветки

| # | Ветка | Фича | Изменение схемы БД |
|---|-------|------|--------------------|
| 1 | `feature/application-tracker` | Трекер откликов | да (новая миграция) |
| 2 | `feature/external-export` | Экспорт в Google Sheets / Notion | нет |
| 3 | `feature/admin-vacancies` | CMS-админка: редактирование/удаление вакансий | нет |
| 4 | `feature/webauthn` | Вход по passkey (WebAuthn/FIDO2) поверх пароля | да (таблица `webauthn_credentials`) |
| 5 | `feature/llm-evals-match` | Golden/evals для match + cover letter | нет |

**Про миграции:** цепочка инкрементальная — head сейчас
`031_jobs_cancel_requested`. Новые фичи просто продолжают её
(`alembic revision` → `upgrade head` на старте backend, без `down -v`).
БД — только Postgres; constraint'ы (FK, индексы) — с явными именами.

---

## 1. `feature/application-tracker` — трекер откликов

Мини-ATS поверх вакансий: статус (new → applied → interview → offer /
rejected / skipped) + заметки.

**БД (новая миграция)**

- Таблица `vacancy_applications`: `vacancy_id` (FK → vacancies), `user_id`
  (FK → users — отклики приватные), `status`, `applied_at`, `notes`,
  `updated_at`. Отдельная таблица, а не колонки — оставляет место под
  историю статусов позже.

**Backend**

- `PATCH /api/v1/vacancies/{id}/application` — upsert статуса/заметок.
- `GET /api/v1/vacancies` — новый фильтр `application_status` + поля
  статуса в ответе (LEFT JOIN по текущему пользователю).

**Frontend**

- Колонка «Статус» в `VacancyTable` с выпадающим списком; редактирование
  заметок в `VacancyModal`; фильтр по статусу; опционально отдельная
  страница «Мои отклики» (все с `status != new`).

**Готово, когда:** статус ставится из таблицы и модалки, фильтруется,
переживает перезапуск; миграция применяется `alembic upgrade head` на старте
backend без `down -v`.

---

## 2. `feature/external-export` — Google Sheets / Notion

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

## 3. `feature/admin-vacancies` — CMS-админка для вакансий

Модерация общего корпуса: сейчас вакансию нельзя ни исправить, ни удалить
(только TTL-архивация). Права — через `users.is_admin`.

**БД**

- Изменений схемы не нужно: `is_admin` уже есть; правим те же колонки
  `vacancies`. Опционально позже — audit-поля (`edited_by`, `edited_at`),
  если захочется историю правок.

**Backend**

- Зависимость `require_admin` уже есть (`backend/api/deps.py`, используется
  HH sync); для CMS — те же эндпоинты `/admin/vacancies`.
- `GET /api/v1/admin/vacancies` — тот же `VacancyQuery`, но
  `include_archived`/`is_duplicate` по умолчанию включены.
- `PATCH /api/v1/admin/vacancies/{id}` — частичное редактирование полей
  (grade, специализации, зарплата, company, work_format, `is_duplicate`,
  `archived_at` null/now — «вернуть из архива»).
- `DELETE /api/v1/admin/vacancies/{id}` — hard delete (+ каскад на
  `vacancy_matches`) или `?soft=true` → `archived_at=now`.
- Валидация через Pydantic-схему `AdminVacancyPatch` (все поля optional).

**Frontend**

- Страница/вкладка «Админка», видимая только при `me.is_admin`: таблица со
  всеми вакансиями (включая архив/дубли), inline-редактирование ключевых
  полей или форма в модалке, кнопки «Архив/Восстановить» и «Удалить» с
  подтверждением.

**Готово, когда:** админ правит поля и удаляет вакансию из UI, изменения
видны в обычном списке; не-админу `/admin/*` отдаёт 403 и пункт меню
скрыт.

---

## 4. `feature/webauthn` — вход по passkey (WebAuthn/FIDO2)

Пароль + JWT остаются, но дополнительно — беспарольный вход: публичный ключ
на сервере, приватный не покидает устройство. Убирает shared secret и даёт
защиту от фишинга; для локального приложения работает на `localhost` без
HTTPS (WebAuthn разрешён на secure context, `localhost` им считается).

**БД (новая миграция)**

- Таблица `webauthn_credentials`: `credential_id` (PK, base64url),
  `user_id` (FK → users, CASCADE), `public_key`, `sign_count`, `name`
  (метка устройства), `transports`, `created_at`, `last_used_at`.
  Пароль при этом остаётся — passkey дополняет, а не заменяет вход.

**Backend**

- Зависимость `webauthn` (py_webauthn) — только для backend; настройки
  `RP_ID`/`RP_NAME`/origin в `Settings` (env `WEBAUTHN_*`, дефолты под
  localhost).
- `POST /api/v1/auth/webauthn/register/options` → `.../register/verify` —
  привязка ключа к текущему пользователю (challenge в памяти/короткий TTL,
  не в БД).
- `POST /api/v1/auth/webauthn/login/options` → `.../login/verify` —
  выдаёт тот же JWT, что и `/auth/login`; `sign_count` обновляется.
- `GET/DELETE /api/v1/auth/webauthn/credentials` — список и отзыв ключей.
- В `auth_events_total` — `event="webauthn_login"` (метрика уже есть,
  лейблы просто расширяются).

**Frontend**

- `@simplewebauthn/browser`; кнопка «Войти по passkey» на форме логина.
- В настройках профиля — список привязанных ключей с именами и кнопкой
  «Привязать устройство».

**Готово, когда:** пользователь привязывает ключ, логинится им после
рестарта backend, отзывает ключ; вход по паролю продолжает работать.

---

## 5. LLM evals: match + cover letter

Extraction уже имеет golden set (`evals/golden/extraction.jsonl`) и offline
scorer. Дальше:

- Golden для матчинга (`evals/golden/match/`): диапазоны score/breakdown,
  ключевые matching/missing skills.
- Cover letter: чеклист или LLM-as-judge (язык, без галлюцинаций, длина).
- Опционально: сэмпл failed prod → кандидат в golden (ручной review).

Не делать: полноценную eval-платформу (LangSmith и т.п.) без явной нужды.

---

## Порядок работы над веткой

1. `git checkout -b feature/<name>` от свежего `master`.
2. Миграция (если есть) → backend → тесты → frontend.
3. Проверка: `pytest`, `npm run build`, ручной прогон через UI.
4. Merge в `master`; описание фичи переезжает в `README.md`, пункт отсюда
   удаляется.

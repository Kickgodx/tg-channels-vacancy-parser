# План разработки

Нереализованные фичи, каждая — в своей ветке `feature/<name>` от `master`.
Порядок выстроен по соотношению ценность/трудозатраты; №1 и №2 независимы.
№3 опирается на `users.is_admin`, №4 — на соло-матчинг из карточки вакансии.

Уже сделанное в план не входит — описание возможностей см. в
[README](README.md): аналитика, полнотекстовый поиск и пресеты, матчинг
резюме (пакетный и соло), JWT / multi-user.

## Ветки

| # | Ветка | Фича | Изменение схемы БД |
|---|-------|------|--------------------|
| 1 | `feature/application-tracker` | Трекер откликов | да (миграция 008) |
| 2 | `feature/external-export` | Экспорт в Google Sheets / Notion | нет |
| 3 | `feature/admin-vacancies` | CMS-админка: редактирование/удаление вакансий | нет |
| 4 | `feature/cover-letter` | Генерация сопроводительного письма под вакансию | да (миграция 009) |

**Про миграции:** цепочка инкрементальная — head сейчас
`007_auth_multiuser`. Новые фичи просто продолжают её
(`alembic revision` → `upgrade head` на старте backend, без `down -v`).
Колонки с FK добавлять через `op.batch_alter_table` с именованным
constraint — SQLite (тесты) не умеет `ALTER TABLE ADD COLUMN` с FK.

---

## 1. `feature/application-tracker` — трекер откликов

Мини-ATS поверх вакансий: статус (new → applied → interview → offer /
rejected / skipped) + заметки.

**БД (миграция 008)**

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

- Зависимость `require_admin` (поверх `get_current_user`, 403 для не-админов).
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

## 4. `feature/cover-letter` — генерация сопроводительного письма

Следующий шаг после соло-матчинга: увидел хороший скор — сразу получил
черновик отклика под конкретную вакансию (инфраструктура та же, что у
матчинга: резюме + текст вакансии → LLM).

**БД (миграция 009)**

- `cover_letters`: `(resume_id, vacancy_id)` PK — как у `vacancy_matches`
  (строка одновременно кэш), `text`, `tone`, `model`, `created_at`.
  Перегенерация перезаписывает строку; история версий — если понадобится.

**Backend**

- Промпт `build_cover_letter_messages(resume_text, vacancy_text, tone,
  language, extra_notes)` + `COVER_LETTER_PROMPT_VERSION` в
  `tg_jobs_parser/ai/prompts.py`. Выход — обычный текст, не JSON: короткое
  письмо (120–200 слов), без выдуманного опыта, с явной привязкой к
  требованиям вакансии; язык — по языку вакансии.
- Модель — `ai.cover_letter_model` (по аналогии с `match_model`; пусто —
  `match_model`, затем `model`).
- `POST /api/v1/resumes/{id}/cover-letter/{vacancy_id}` (синхронно, как
  соло-матчинг; `force=true` — перегенерация, `tone` в теле запроса),
  `GET` — сохранённый вариант, `DELETE` — сброс.
- Опционально подмешивать в промпт последний `vacancy_matches` (сильные и
  слабые стороны уже посчитаны — письмо адресует пробелы).

**Frontend**

- Кнопка «Сопроводительное» в `VacancyModal` рядом с «Оценить резюме».
  Результат — редактируемая `textarea` в модалке, кнопки «Копировать»,
  «Перегенерировать», выбор тона (нейтральный / дружелюбный / формальный).

**Готово, когда:** из карточки вакансии генерируется письмо под связку
резюме+вакансия, повторное открытие отдаёт сохранённое, текст копируется в
буфер одной кнопкой.

---

## Порядок работы над веткой

1. `git checkout -b feature/<name>` от свежего `master`.
2. Миграция (если есть) → backend → тесты → frontend.
3. Проверка: `pytest`, `npm run build`, ручной прогон через UI.
4. Merge в `master`; описание фичи переезжает в `README.md`, пункт отсюда
   удаляется.

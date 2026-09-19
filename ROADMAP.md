# План разработки

Пять фичей, каждая — в своей ветке `feature/<name>` от `master`. Порядок
выстроен по соотношению ценность/трудозатраты; 1–3 независимы и при желании
могут идти в другом порядке.

## Ветки

| # | Ветка | Фича | Изменение схемы БД |
|---|-------|------|--------------------|
| 1 | `feature/analytics-dashboard` | Дашборд зарплат + аналитика каналов/компаний | нет |
| 2 | `feature/application-tracker` | Трекер откликов | да (миграция 005) |
| 3 | `feature/fts-saved-filters` | Полнотекстовый поиск + сохранённые фильтры | да (миграция 004) |
| 4 | `feature/resume-matching` | Оценка соответствия резюме | да (миграция 006) |
| 5 | `feature/external-export` | Экспорт в Google Sheets / Notion | нет |

**Про миграции:** цепочка уже инкрементальная — head сейчас
`003_vacancy_archived_at`. Новые фичи просто продолжают её
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

## 4. `feature/resume-matching` — матчинг резюме к вакансиям

Самая тяжёлая фича, но инфраструктура готова: OpenRouter-клиент, кэш,
`ai_stats`, каталог моделей в `llm_models.yaml`.

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

## Порядок работы над веткой

1. `git checkout -b feature/<name>` от свежего `master`.
2. Миграция (если есть) → backend → тесты → frontend.
3. Проверка: `pytest`, `npm run build`, ручной прогон через UI.
4. Merge в `master`, пункт здесь отмечается `- [x]`.

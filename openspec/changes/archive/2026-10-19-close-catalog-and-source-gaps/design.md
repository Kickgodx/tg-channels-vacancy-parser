# Design

## Context

Это наблюдения этапов 2 и 4 baseline (`baseline-ingestion`, `baseline-catalog`).

Каталог и курсы:
- `vacancy_filter_conditions` (`backend/db/vacancy_filters.py`) применяет
  `has_salary` только при `true` и проверяет `detected_salary_min/max/avg`.
  Аналитика (`catalog/analytics`) считает «с зарплатой» только
  `detected_salary_avg_rub`.
- `backfill_vacancies_salary_rub` (`backend/fx/backfill.py`) выбирает
  `archived_at IS NULL`.
- `_prepare_fx` (`backend/db/vacancy_metrics.py`) подставляет сегодняшнюю дату,
  если курсов нет.
- `apply_salary_rub_for_vacancy` (`backend/fx/apply.py`) при пустом
  `rates_cache` сам вызывает `ensure_latest_rates`. Если ЦБ недоступен, а база
  пуста, это HTTP-запрос с таймаутом на каждую нерублёвую вакансию пачки.
  `preload_fx_rates_for_vacancies` уже сделал одну попытку.

HH:
- `_fetch_into_buffer` (`backend/services/hh_sync_service.py`) при
  `HHApiError` кладёт в буфер `short_to_vacancy`. Upsert HH перезаписывает
  поля, поэтому полная карточка, сохранённая раньше, деградирует до короткой
  выдачи. Повторная загрузка случается, когда строка кэша выдачи протухла или
  содержание в выдаче изменилось.
- `HHClient.iter_vacancy_pages` останавливается на глубине 2000 и ничего не
  сообщает, хотя `found` из ответа известен.
- `_specializations` (`tg_jobs_parser/hh/mapper.py`) без ролей возвращает
  `["AQA", "Python"]`.

Telegram:
- `apply_post_filters` (`tg_jobs_parser/filters/vacancy_filters.py`) вызывает
  `matches_specialization_filter` без учёта `filters.any_specialization`. UI
  при этом флаге шлёт пустой список, поэтому через API проблема не видна, а
  через `config.yaml` видна.
- `clamp_range_days` (`tg_jobs_parser/utils/dates.py`) оставляет начало окна.
- `_scan_channel` (`tg_jobs_parser/parsers/telegram_telethon.py`) при лимите
  только пишет в журнал. Предупреждения FloodWait и частичной ошибки уже
  возвращаются как `warning` и попадают в `parse_warnings` через
  `_take_channel`.

Решения пользователя (2026-10-19), все по рекомендованным вариантам:
- `has_salary` — по рублёвой сумме;
- HH глубже 2000 — предупреждение в итоге;
- HH без ролей — пустой список;
- длинное окно — оставлять свежие дни.

Остальные пункты исправляются единственным разумным способом.

## Goals / Non-Goals

**Goals:**
- Список, метрики, аналитика и экспорт одинаково считают «с зарплатой».
- Ни одна из потерь данных не проходит молча: она исправлена или видна в итоге.

**Non-Goals:**
- Автоматическое дробление окна HH по датам, чтобы обойти лимит 2000
  (отклонено пользователем).
- Пересчёт специализаций старых HH-вакансий без ролей отдельной миграцией. Их
  перезапишет следующий sync, а если вакансия ушла из выдачи, она архивируется
  по TTL.
- Группа E (согласованность API).

## Decisions

### 1. `has_salary` — по `detected_salary_avg_rub`

`true` даёт `detected_salary_avg_rub IS NOT NULL`, `false` — `IS NULL`, `None`
— без условия. Одна функция строит условия для списка, метрик, аналитики и
экспорта, поэтому они совпадают автоматически. Описание поля в
`VacancyQuery` обновляется. Frontend шлёт только `true` («только с
зарплатой»), его код не меняется.

### 2. Архив в пересчёте

Из выборки `backfill_vacancies_salary_rub` убирается условие
`archived_at IS NULL`, условие `is_duplicate = false` остаётся. Счётчики итога
растут на число архивных вакансий с зарплатой, формат ответа прежний.

### 3. Один запрос к ЦБ на пачку

В `apply_salary_rub_for_vacancy` курсы догружаются, только если
`rates_cache is None`, то есть кэш не передан. Пустой словарь означает
«попытка уже была, курсов нет». Все три вызывающих (`upsert_vacancies`,
`upsert_hh_vacancies`, backfill) передают кэш, полученный одним запросом.

### 4. `rates_as_of = None` без курсов

Модель `SalaryDisplayMeta.rates_as_of` уже `str | None`. Frontend при пустой
дате показывает «, ₽» без даты.

### 5. Сбой деталей HH не трогает сохранённую карточку

Если в `_fetch_into_buffer` при `HHApiError` уже есть строка с этим
`source_post_key`, в буфер кладётся `row_to_vacancy(existing)` с флагами дубля,
переданными в вызов. Так сделано в `_append_duplicate`, и upsert перезапишет
поля теми же значениями. Короткая выдача используется, только если строки
нет. Ошибка, как и раньше, пишется в `errors`. `mark_detail_fetched` не
вызывается, поэтому следующий sync попробует снова.

### 6. `warnings` в итоге HH и предупреждение о глубине

`HhSyncResult` получает поле `warnings: list[str]`, `HhSyncResponse` — поле
`warnings` с пустым списком по умолчанию. Старые итоги в `hh_sync_jobs.result`
без этого поля читаются как раньше.

`_collect_shorts` запоминает `found` первой страницы. Если `found` больше
прочитанного, а чтение не остановлено отменой и упёрлось в
`HH_VACANCIES_DEPTH_LIMIT`, добавляется предупреждение: `HH нашёл 5000
вакансий, прочитано 2000 (лимит глубины HH) — сузьте окно или запрос`. Тип
во frontend и блок «Предупреждения» на странице HH добавляются рядом с
«Ошибками».

### 7. Без ролей — пустые специализации

Строка `names = ["AQA", "Python"]` удаляется.

### 8. `any_specialization` в `apply_post_filters`

При `config.filters.any_specialization` фильтр специализаций пропускается.
`matches_specialization_filter` не меняется: его используют и другие места.

### 9. Окно обрезается со стороны `date_from`

`clamp_range_days` возвращает `(dt_to - (max_days - 1), dt_to)`. Функцию
вызывает только `parse_runner`. Текст предупреждения уже содержит итоговые
даты.

### 10. Лимит канала в `warning`

При `scanned > max_scan` `_scan_channel` записывает `warning`:
`@jobs: просмотрено N сообщений (лимит parser.max_posts_per_channel) — канал
прочитан частично: K постов`. Строка попадает в `parse_warnings` тем же путём,
что FloodWait, а `stopped_early` по-прежнему не даёт чекпоинта.

## Risks / Trade-offs

- [Вакансии с зарплатой в валюте без курса пропадут из «только с зарплатой»]
  → после группы C это редкость. Так же они уже не видны в зарплатной
  аналитике.
- [Пересчёт с архивом обрабатывает больше строк] → пересчёт ручной (admin) и
  идёт `yield_per(200)`, объём ограничен TTL курсов и вакансий.
- [Обрезка окна от свежих дней меняет прежнее поведение] → оно задокументировано
  в baseline как наблюдение и не было намеренным. Предупреждение остаётся.

## Migration Plan

Миграций нет.

После выката можно запустить `POST /fx/recalculate-rub`, чтобы обновить
рублёвые суммы архивных вакансий.

Откат — откат коммита. Поле `warnings` в сохранённых итогах HH старый код
проигнорирует.

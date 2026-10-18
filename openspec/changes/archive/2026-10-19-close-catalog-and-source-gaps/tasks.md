# Tasks

## 1. Каталог и курсы

- [x] 1.1 `has_salary` по `detected_salary_avg_rub` (`true` / `false`) в `backend/db/vacancy_filters.py`, описание поля в `VacancyQuery`. Проверка — тест в `tests/test_has_salary_filter.py`: «Зарплата есть только в исходной валюте» (список с `true` и `false`, метрики с `true`)
- [x] 1.2 Пересчёт включает архивные вакансии (`backend/fx/backfill.py`). Проверка — тест в `tests/test_fx_backfill.py`: «Архивная вакансия» (сумма 90 000 после смены курса); «Обновление курсов» — существующие тесты `/fx/refresh` зелёные
- [x] 1.3 `apply_salary_rub_for_vacancy` догружает курсы только при `rates_cache is None`. Проверка — тест в `tests/test_fx_backfill.py`: «ЦБ недоступен при записи пачки» (30 долларовых вакансий, один вызов ЦБ, суммы пусты)
- [x] 1.4 `rates_as_of = None` без курсов (`backend/db/vacancy_metrics.py`). Проверка — тест в `tests/test_fx_metrics.py`: «Курсов нет» (`rates_as_of` — `null` в метриках)

## 2. HH

- [x] 2.1 Сбой деталей сохраняет существующую карточку (`_fetch_into_buffer`). Проверка — тест в `tests/test_hh_review_fixes.py`: «Повторная загрузка деталей не удалась» (текст не изменился, ошибка в `errors`)
- [x] 2.2 `warnings` в `HhSyncResult` и `HhSyncResponse`, предупреждение о глубине в `_collect_shorts`. Проверка — тесты в `tests/test_hh_api.py`: «Выдача больше 2000» (предупреждение с 5000 и 2000), «Выдача в пределах лимита» (`warnings` пуст)
- [x] 2.3 Без ролей — пустые специализации (`tg_jobs_parser/hh/mapper.py`). Проверка — тест в `tests/test_hh_mapper.py`: «Вакансия без ролей»
- [x] 2.4 Frontend: `warnings` в типе итога HH (`frontend/src/types/api.ts`) и блок «Предупреждения» на `HhPage.tsx`. Проверка — `npm run lint` и `npm run build` зелёные

## 3. Telegram

- [x] 3.1 `apply_post_filters` пропускает фильтр специализаций при `any_specialization`. Проверка — тест в `tests/test_vacancy_filters.py`: «Любая IT-роль»
- [x] 3.2 `clamp_range_days` оставляет свежие дни. Проверка — тест в `tests/test_dates.py`: «Слишком длинное окно» (2026-09-20 … 2026-10-19 при `max_days` 30) и предупреждение в `parse_warnings` прогона (`tests/test_streaming_parse.py`)
- [x] 3.3 Предупреждение о лимите `max_posts_per_channel` из `_scan_channel`. Проверка — тест в `tests/test_telethon_incremental.py`: «Лимит просмотра в предупреждениях» (warning называет канал и лимит, чекпоинта нет)

## 4. Документация и итоговая проверка

- [x] 4.1 README (`has_salary`, предупреждения HH, обрезка окна) и `config.md` (`search.max_days`, `parser.max_posts_per_channel`, `filters.any_specialization`). Проверка — тексты обновлены
- [x] 4.2 `.venv/Scripts/python.exe -m pytest -n 8 -q`, `.venv/Scripts/ruff.exe check .` и `ruff format --check .` — зелёные
- [x] 4.3 `openspec validate close-catalog-and-source-gaps --strict` без ошибок

## Workflow follow-up

- Мерж ветки `feature/close-catalog-and-source-gaps` в `master` через `--no-ff`.
- `/opsx:archive close-catalog-and-source-gaps`.

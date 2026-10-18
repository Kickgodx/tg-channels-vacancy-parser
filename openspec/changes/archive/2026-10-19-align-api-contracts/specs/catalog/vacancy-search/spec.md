# Spec Delta

## MODIFIED Requirements

### Requirement: Сортировка списка
`sort_by` MUST принимать `post_date` (по умолчанию), `detected_salary_avg`, `detected_salary_avg_rub`, `work_format`, `detected_grade`, `salary_currency`, `vacancy_score` и `match_score` (см. `career/resume-matching`); другое значение — 422. `match_score` без `resume_id` (в том числе в экспорте и списке вакансий прогона) SHALL давать 422 `VALIDATION`. `sort_order` — `asc` или `desc` (по умолчанию). Пустые значения идут в конце при любом направлении, равные — по `id`.

#### Scenario: Зарплата в рублях по убыванию
- **WHEN** у трёх вакансий рублёвая зарплата 300 000, пусто и 150 000, сортировка — `detected_salary_avg_rub`
- **THEN** порядок: 300 000, 150 000, без зарплаты

#### Scenario: Недопустимое поле
- **WHEN** `sort_by=raw_text`
- **THEN** ответ 422 `VALIDATION`

#### Scenario: Сортировка по оценке без резюме
- **WHEN** список запрошен с `sort_by=match_score` без `resume_id`
- **THEN** ответ 422 `VALIDATION`, сообщение говорит, что нужен `resume_id`

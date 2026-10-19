# Spec Delta

## MODIFIED Requirements

### Requirement: Сортировка списка
`sort_by` MUST принимать `post_date` (по умолчанию), `detected_salary_avg`, `detected_salary_avg_rub`, `work_format`, `detected_grade`, `salary_currency`, `vacancy_score` и `match_score` (см. `career/resume-matching`); другое значение — 422. `match_score` без `resume_id` (в том числе в экспорте и списке вакансий прогона) SHALL давать 422 `VALIDATION`. `sort_order` — `asc` или `desc` (по умолчанию). Пустые значения идут в конце при любом направлении. Равные по полю (кроме `post_date` и `match_score`) SHALL идти от новых постов к старым, затем по `id`; при сортировке по `post_date` и `match_score` равные — по `id`.

Вторая ступень `then_by` (с `then_order`, по умолчанию `desc`) MUST быть доступна в списке, экспорте и пресетах только для пары `post_date` ↔ `detected_salary_avg_rub`; другое сочетание (в том числе поле с самим собой) — 422 `VALIDATION`. `sort_by=post_date` с `then_by=detected_salary_avg_rub` SHALL сортировать по дню поста по UTC, внутри дня — по рублёвой зарплате (пустая — в конце), затем по времени поста в направлении дня. `sort_by=detected_salary_avg_rub` с `then_by=post_date` — при равной зарплате по дате в направлении `then_order`.

#### Scenario: Зарплата в рублях по убыванию
- **WHEN** у трёх вакансий рублёвая зарплата 300 000, пусто и 150 000, сортировка — `detected_salary_avg_rub`
- **THEN** порядок: 300 000, 150 000, без зарплаты

#### Scenario: Равная зарплата
- **WHEN** у двух вакансий рублёвая зарплата 150 000, одна опубликована 10 октября, другая — 12-го, сортировка — `detected_salary_avg_rub`
- **THEN** вакансия от 12 октября идёт раньше
- **AND** с `then_by=post_date&then_order=asc` — позже

#### Scenario: Внутри дня — по зарплате
- **WHEN** 20 октября (UTC) опубликованы A в 10:00 с зарплатой 100 000, B в 08:00 с 300 000 и C в 12:00 без зарплаты, 19 октября в 23:30 UTC — E с 500 000; запрос `sort_by=post_date&then_by=detected_salary_avg_rub`
- **THEN** порядок: B, A, C, E

#### Scenario: Вторая ступень вне пары
- **WHEN** `sort_by=work_format&then_by=detected_salary_avg_rub`
- **THEN** ответ 422 `VALIDATION`

#### Scenario: Недопустимое поле
- **WHEN** `sort_by=raw_text`
- **THEN** ответ 422 `VALIDATION`

#### Scenario: Сортировка по оценке без резюме
- **WHEN** список запрошен с `sort_by=match_score` без `resume_id`
- **THEN** ответ 422 `VALIDATION`, сообщение говорит, что нужен `resume_id`

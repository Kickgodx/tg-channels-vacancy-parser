# Spec Delta

## MODIFIED Requirements

### Requirement: Грейд вакансии HH — по требуемому опыту
Грейд MUST определяться только по опыту HH, без учёта названия вакансии: без опыта — `intern`, 1–3 года — `junior`, 3–6 лет — `middle`, больше 6 — `senior`, иначе `unknown`. Вакансиям HH с грейдом `unknown` и сохранённым текстом опыта грейд SHALL проставляться по этому тексту при старте API и раз в час.

#### Scenario: Опыт 3–6 лет
- **WHEN** у вакансии опыт `between3And6`
- **THEN** грейд `middle`

#### Scenario: Опыт больше 6 лет
- **WHEN** у вакансии «Python Lead» опыт `moreThan6`
- **THEN** грейд `senior`

#### Scenario: Старая строка без грейда
- **WHEN** у сохранённой вакансии HH грейд `unknown`, опыт «От 3 до 6 лет»
- **THEN** после очистки при старте грейд `middle`

# Spec Delta

## ADDED Requirements

### Requirement: Языки и инструменты — технологии, а не специализации
Специализация MUST быть ролью (функцией: `QA`, `Automation QA`, `Backend`, `AI`…). Языки, фреймворки, платформы, БД и инструменты из словаря технологий (`tg_jobs_parser/config/schema.py`) SHALL переноситься из специализаций в технологии везде, откуда приходят специализации: правила, ответ LLM и кэш extraction, роли HH, `filters.specializations`, фильтр списка вакансий. Ключи `dictionaries.specializations`, которые являются технологиями, игнорируются.

#### Scenario: AQA на Python
- **WHEN** LLM вернул специализации `["Automation QA", "Python"]`
- **THEN** специализации — `[Automation QA]`, `Python` — в технологиях

#### Scenario: Только язык
- **WHEN** правила или LLM дали специализации `["Golang", "Java"]`
- **THEN** специализаций нет, технологии — `Go`, `Java`

#### Scenario: Язык в фильтре прогона
- **WHEN** в `filters.specializations` указаны `Python` и `QA`
- **THEN** фильтр специализаций — `[QA]`, фильтр технологий — `[Python]`

### Requirement: Составные имена и роль разработчика на технологии
Составная специализация («C#/.NET») MUST переноситься в технологии, только если технологии все её части. «<технология> developer / engineer / разработчик / программист» и «программист <технология>» SHALL давать технологию, а без другой роли — роль `Software Engineer`; голое имя технологии роли не даёт.

#### Scenario: Разработчик на языке
- **WHEN** специализация — `Python Developer`
- **THEN** специализации — `[Software Engineer]`, технология — `Python`

#### Scenario: Составное имя
- **WHEN** специализации — `C#/.NET` и `UX/UI Designer`
- **THEN** технологии — `C#`, `.NET`, специализация — `UX/UI Designer`

### Requirement: Технологии хранятся в каноническом написании
Технологии MUST приводиться к имени из словаря технологий: алиасы (`TECHNOLOGY_ALIASES`: `Golang` — `Go`, `Apache Kafka` — `Kafka`, `Postgres` — `PostgreSQL`) и регистр (`python` — `Python`); повторы без учёта регистра схлопываются, порядок сохраняется. Неизвестное имя SHALL сохраняться как есть.

#### Scenario: Варианты написания
- **WHEN** технологии вакансии — `golang`, `Go`, `Apache Kafka`
- **THEN** сохраняются `Go`, `Kafka`

### Requirement: Фильтр технологий прогона
Если `filters.technologies` не пуст, пост MUST иметь хотя бы одну технологию из списка (без учёта регистра и алиасов). Фильтр технологий SHALL сочетаться с фильтром специализаций через «и» и действовать и при `filters.any_specialization: true`. Пустой список не ограничивает.

#### Scenario: Роль и технология
- **WHEN** в фильтре специализаций `[Automation QA]`, технологий — `[Python]`, у поста роль `Automation QA` и технология `Java`
- **THEN** пост отбрасывается

#### Scenario: Любая IT-роль с технологией
- **WHEN** `any_specialization: true`, в фильтре технологий `[Python]`, у поста роль `Backend` и технология `Python`
- **THEN** пост проходит

## MODIFIED Requirements

### Requirement: Фильтр специализаций требует пересечения
Если `filters.specializations` не пуст и `filters.any_specialization` выключен, пост MUST иметь хотя бы одну специализацию из списка; пост без распознанных специализаций SHALL отбрасываться. Пустой список и `filters.any_specialization: true` не ограничивают.

#### Scenario: Специализация не распознана
- **WHEN** в фильтре `[Backend]`, а у поста нет распознанных специализаций
- **THEN** пост отбрасывается

#### Scenario: Любая IT-роль
- **WHEN** в фильтре `[Backend]`, `any_specialization: true`, а у поста специализация `QA`
- **THEN** пост проходит фильтр специализаций

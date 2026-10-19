# Spec Delta

## ADDED Requirements

### Requirement: Синонимы специализаций приводятся к каноническим
Специализация MUST сохраняться под каноническим именем: «Тестировщик» и `Manual QA` — это `QA`, `AQA` — `Automation QA`, «программист» в тексте и роль HH «Программист, разработчик» — `Software Engineer`. Прежние имена SHALL приводиться к каноническим везде, откуда приходят специализации: правила, ответ LLM и кэш extraction, `filters.specializations`, `dictionaries.specializations`, фильтр списка вакансий; повторы после приведения схлопываются. В вариантах фильтров UI прежних имён нет.

#### Scenario: Ручное тестирование
- **WHEN** пост «Ищем Manual QA / тестировщика»
- **THEN** специализации — `[QA]`

#### Scenario: Программист
- **WHEN** пост «Вакансия: программист, удалёнка»
- **THEN** специализации — `[Software Engineer]`

#### Scenario: Старое имя от LLM
- **WHEN** LLM или запись кэша extraction вернули `["AQA", "Manual QA"]`
- **THEN** специализации — `[Automation QA, QA]`

#### Scenario: Старое имя в фильтре
- **WHEN** список вакансий запрошен с фильтром `AQA`
- **THEN** находятся вакансии со специализацией `Automation QA`

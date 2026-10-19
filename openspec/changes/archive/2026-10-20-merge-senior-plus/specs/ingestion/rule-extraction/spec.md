# Spec Delta

## MODIFIED Requirements

### Requirement: Грейд — старший из найденных
Грейд MUST выбираться как самый старший из найденных по порядку `intern` < `junior` < `junior+` < `middle` < `middle+` < `senior` < `lead` < `staff` < `principal`; не найден — `unknown`. `staff` SHALL распознаваться только как уровень (Staff Engineer), не как слово «staff» в обычном значении. Отдельного грейда `senior+` нет: «Senior+» в тексте, а также значение `senior+` от LLM или из старых данных MUST приводиться к `senior`.

#### Scenario: Несколько грейдов
- **WHEN** в посте упомянуты Middle и Senior
- **THEN** грейд — `senior`

#### Scenario: Staff как глагол
- **WHEN** в тексте «to staff teams»
- **THEN** грейд не `staff`

#### Scenario: Senior+
- **WHEN** в посте «Senior+ Python Developer»
- **THEN** грейд — `senior`

#### Scenario: Старое значение senior+
- **WHEN** LLM или кэш ответа вернул грейд `senior+`
- **THEN** у вакансии грейд `senior`

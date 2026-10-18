# Spec Delta

## MODIFIED Requirements

### Requirement: Фильтр специализаций требует пересечения
Если `filters.specializations` не пуст и `filters.any_specialization` выключен, пост MUST иметь хотя бы одну специализацию из списка; пост без распознанных специализаций SHALL отбрасываться. Пустой список и `filters.any_specialization: true` не ограничивают.

#### Scenario: Специализация не распознана
- **WHEN** в фильтре `[Python]`, а у поста нет распознанных специализаций
- **THEN** пост отбрасывается

#### Scenario: Любая IT-роль
- **WHEN** в фильтре `[Python]`, `any_specialization: true`, а у поста специализация `QA`
- **THEN** пост проходит фильтр специализаций

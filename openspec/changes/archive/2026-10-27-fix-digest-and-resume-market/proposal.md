# Proposal

## Why

Первый настоящий обзор (неделя 2026-10-19, 519 вакансий) показал две ошибки текста LLM:
VK назван «новичком недели», хотя его нет в `new_companies` (на прошлой неделе у него просто
не было вакансий); общая медиана зарплаты «выросла» с 191 до 215 тыс., хотя по каждому грейду
упала — сменился состав грейдов, а прошлое окно маленькое. Медиана lead считалась по 5
зарплатам. В «Резюме и рынок» Jira помечалась «нет» у резюме с «Atlassian Jira»: словарь
берёт самое длинное совпадение и вложенное имя не видит.

## What Changes

- Промпт обзора `market-digest-v2`: новые компании — только из `new_companies`; зарплаты
  сравниваются по грейдам, общая медиана — только если грейды идут в ту же сторону. Прежние
  тексты становятся `stale`.
- Медиана группы зарплат в обзоре — от 10 вакансий (было 5).
- `in_resume`: словарь или название технологии целым словом в тексте резюме
  (`skill_gaps.mentions`); то же в `coverage`.

## Capabilities

### Modified Capabilities

- `catalog/market-digest` — порог медианы, правила текста.
- `career/resume-market` — поиск технологии в резюме.

## Impact

`tg_jobs_parser/ai/prompts.py`, `tg_jobs_parser/analytics/skill_gaps.py`,
`backend/services/market_digest_service.py`, `backend/services/resume_market_service.py`, README.

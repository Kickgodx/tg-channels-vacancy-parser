# Proposal

## Why

Поиск, аналитика и инструменты для резюме доступны только в UI. Чтобы пользоваться ими из
Claude Desktop / Claude Code («найди Go remote от 300k и напиши письмо в лучшую»), нужен
MCP-сервер и способ дать ему доступ без пароля: JWT живёт 15 минут, а пароль в конфиге
клиента открывает весь аккаунт.

## What Changes

- Персональные токены доступа (PAT, `tgj_…`): таблица `api_tokens` (миграция 049),
  `GET`/`POST`/`DELETE /auth/tokens`, Bearer `tgj_…` в `get_current_user`. Scope `read` —
  GET-эндпоинты, `llm` — эндпоинты с `@pat_scope("llm")` (постановка LLM-задач и отмена своей);
  admin-эндпоинты, управление токенами и прочие мутации PAT не открывает. Смена пароля и «выйти
  везде» отзывают PAT. UI: страница «Токены».
- Пакет `tg_jobs_mcp` (extra `mcp`, SDK `mcp` 2.x): stdio-сервер — клиент HTTP API, 23
  инструмента (поиск и разбор вакансий, рынок, резюме, LLM-задачи с ожиданием).

## Capabilities

### New Capabilities

- `identity/api-tokens` — персональные токены доступа.
- `platform/mcp-server` — MCP-сервер над API.

## Impact

Миграция `049_api_tokens`. Новая зависимость `mcp` — только в extra `mcp` (в образ backend не
входит; CI ставит `--extras dev --extras mcp`); в основном наборе `idna` 3.13 → 3.20.

# Proposal

## Why

Список каналов задаётся вручную, и новые источники находятся случайно. А читаемые каналы
сами пересылают посты из других каналов, и это прямой сигнал: если из канала X часто
пересылают вакансии, его стоит читать напрямую. Telethon отдаёт источник пересылки вместе
с историей, отдельные запросы не нужны.

## What Changes

- `RawPost.forward_from` — канал-источник пересылки (`fwd_from` с `channel_id`; username и
  название из сущностей ответа истории, id поста в источнике). Пересылки от пользователей,
  со скрытым отправителем и из самого канала не учитываются.
- `run_parse` собирает пересланные посты (`ParseResult.forwarded_posts`): наш канал, пост,
  источник, прошёл ли пост как кандидат в вакансии. Воркер прогона пишет их после
  `run_parse`; сбой записи прогон не роняет.
- Таблицы `channel_candidates` (канал по id, последние username и название, статус
  new / ignored) и `telegram_forwards` (PK — наш канал + id поста), миграция 045.
- `GET /channels/candidates` (admin): каналы с публичным username, которых нет в
  `config.yaml` и среди каналов с чекпоинтами; сортировка по числу пересланных вакансий.
  `PATCH /channels/candidates/{id}` — скрыть или вернуть.
- UI: блок «Кандидаты из пересылок» под полем каналов на странице «Парсинг»; «Добавить»
  вписывает канал в форму прогона.

## Capabilities

### New Capabilities

- `sources/channel-discovery` — кандидаты в источники по пересылкам.

## Impact

Миграция `045_channel_candidates`; `tg_jobs_parser/parsers/{base,telegram_telethon}.py`,
`parse_runner.py`, `models/parse_result.py`; backend — модели,
`channel_candidate_repository`, `parse_tasks.py`, роут `channels.py`; frontend —
`ChannelCandidatesPanel` в `ParseForm`.

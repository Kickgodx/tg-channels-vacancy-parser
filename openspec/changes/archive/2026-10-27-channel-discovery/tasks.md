# Tasks

## 1. Ядро

- [x] 1.1 `ForwardSource`, `ForwardedPost`, `RawPost.forward_from`, `forward_source(msg)` в Telethon. Проверка — `tests/test_channel_candidates.py` (`test_forward_source_*`)
- [x] 1.2 Сбор пересылок в `run_parse`, `ParseResult.forwarded_posts`. Проверка — `test_run_parse_collects_forwarded_posts`

## 2. Backend

- [x] 2.1 Таблицы `channel_candidates`, `telegram_forwards` (миграция 045), `save_forwards`, запись в воркере прогона. Проверка — `test_save_forwards_is_idempotent_and_keeps_known_names`, `test_parse_job_saves_forwards`
- [x] 2.2 `GET/PATCH /channels/candidates`. Проверка — `test_list_skips_known_private_and_orders_by_vacancies`, `test_api_admin_only_excludes_read_channels_and_ignores`

## 3. UI и документация

- [x] 3.1 `ChannelCandidatesPanel` в форме прогона. Проверка — `npm run lint`, `npm run build`
- [x] 3.2 README, ROADMAP (head миграций)

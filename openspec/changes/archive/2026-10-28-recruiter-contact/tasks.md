# Tasks

## 1. Контакты

- [x] 1.1 `find_recruiter_contacts`: Telegram и email, отсев ссылок на посты, служебных путей, рекламы канала. Проверка — `tests/test_recruiter_contacts.py::test_find_recruiter_contacts`, `test_known_channels_dropped_and_hinted_first`
- [x] 1.2 Отсев известных каналов и подписей канала по частоте; `contact` только у HH. Проверка — `test_channel_signature_dropped_unless_hinted`, `test_hh_contact_field_used_telegram_contact_field_ignored`
- [x] 1.3 `GET /vacancies/{id}/contacts`. Проверка — `test_endpoint`

## 2. UI и документация

- [x] 2.1 `RecruiterContactBlock` в карточке вакансии. Проверка — `npm run lint`, `npm run build`
- [x] 2.2 README

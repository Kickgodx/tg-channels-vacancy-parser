"""Контакты для отклика: Telegram-аккаунты и email рекрутера из текста вакансии.

Поле ``contact`` вакансии — первая попавшаяся строка (часто подпись канала-агрегатора
«👉@progjob» или «Подписаться на наши каналы / @best_itjob»). Здесь кандидаты отбираются
строже: каналы-источники, ссылки на посты и рекламные подписи отбрасываются, а упоминания
рядом со словами «контакты», «резюме», «отклики» идут первыми. Подписи, которые канал
ставит под каждым постом, отсеивает backend по частоте в канале (``signature_handles``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# t.me/<name>, telegram.me/<name>; t.me/<name>/123 — пост канала, t.me/+…, joinchat, c/ — не люди.
_TG_LINK = re.compile(r"(?:https?://)?(?:t|telegram)\.me/([A-Za-z][A-Za-z0-9_]{3,31})(/\d+)?(?![A-Za-z0-9_])", re.I)
# @name не внутри email («hr@kaspi.kz») и не в пути ссылки («teletype.in/@remoteit»).
_AT = re.compile(r"(?<![\w./@])@([A-Za-z][A-Za-z0-9_]{3,31})(?![A-Za-z0-9_@])(?!\.[A-Za-z]{2,})")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9])")
_TG_SERVICE = {"joinchat", "addlist", "share", "proxy", "socks", "addstickers", "iv", "setlanguage", "contact"}
_EMAIL_SKIP = re.compile(r"^(?:no-?reply|do-?not-?reply|mailer-daemon)@", re.I)
_EMAIL_FILE = re.compile(r"\.(?:png|jpe?g|gif|webp|svg)$", re.I)

# Сколько символов перед упоминанием смотреть на слова-подсказки.
CONTEXT_CHARS = 60
# Реклама канала и подписи агрегатора: такое упоминание — не рекрутер.
_PROMO = re.compile(
    r"подпис|наш(?:и|ем|их)?\s+канал|в\s+канал|канал[еау]?\b|больше\s+вакансий|все\s+вакансии|опубликован|публику|"
    r"разместить|реклам|разбор|чат\b|subscribe|our\s+channel|join\s+us|more\s+jobs|👉",
    re.I,
)
# Слова отклика: такое упоминание почти наверняка контакт рекрутера.
_CONTACT = re.compile(
    r"контакт|резюме|резюмe|cvs?\b|откли|писать|пишите|напиш|вопрос|связ|подробност|лич[кн]|пообщ|"
    r"заинтересовал|telegram|телеграм|\btg\b|\bтг\b|hr\b|рекрутер|recruit|contact|apply|text\s+(?:me|here)|"
    r"\bdm\b|reach|interested|in\s+touch|details|📩|💌|✉|📞",
    re.I,
)

ContactKind = Literal["telegram", "telegram_bot", "email"]

MAX_TELEGRAM = 3
MAX_EMAIL = 2


@dataclass(frozen=True)
class RecruiterContact:
    kind: ContactKind
    # Username без «@» (как в тексте) или email.
    value: str
    # Рядом слова отклика («Контакты:», «резюме —»): кандидат надёжнее.
    has_contact_hint: bool


def _hint(text: str, start: int) -> tuple[bool, bool]:
    """(реклама канала, слова отклика) в окне перед позицией: решает ближайшее к упоминанию.

    «Контакты: hr@x.ru 🔥 Подписаться на наши каналы / @a» — @a реклама, а не контакт.
    """
    window = text[max(0, start - CONTEXT_CHARS) : start]
    # Подсказка относится к ближайшему предложению: «Контакты: x. Наш канал @y» — к @y только канал.
    parts = re.split(r"[.!?\n]\s", window)
    tail = parts[-1]
    # «Interested? DM me! @name»: упоминание сразу после конца предложения — подсказка в нём.
    if len(parts) > 1 and not re.search(r"\w", tail):
        tail = f"{parts[-2]} {tail}"
    promo = max((m.end() for m in _PROMO.finditer(tail)), default=-1)
    contact = max((m.end() for m in _CONTACT.finditer(tail)), default=-1)
    return promo > contact, contact > promo


def find_recruiter_contacts(
    text: str, *, known_channels: set[str] | frozenset[str] = frozenset()
) -> list[RecruiterContact]:
    """Кандидаты в контакты рекрутера в порядке надёжности.

    ``known_channels`` — usernames каналов (источники прогонов, кандидаты из пересылок) в
    нижнем регистре: упоминание канала — не контакт.
    """
    if not text:
        return []
    found: dict[str, tuple[int, RecruiterContact]] = {}

    def add(handle: str, start: int) -> None:
        key = handle.lower()
        if key in known_channels or key in _TG_SERVICE or key in found:
            return
        promo, hint = _hint(text, start)
        if promo and not hint:
            return
        kind: ContactKind = "telegram_bot" if key.endswith("bot") else "telegram"
        found[key] = (start, RecruiterContact(kind=kind, value=handle, has_contact_hint=hint))

    for m in _TG_LINK.finditer(text):
        if m.group(2):  # ссылка на пост — это канал
            continue
        add(m.group(1), m.start())
    for m in _AT.finditer(text):
        add(m.group(1), m.start())

    emails: dict[str, tuple[int, RecruiterContact]] = {}
    for m in _EMAIL.finditer(text):
        email = m.group(0).rstrip(".")
        key = email.lower()
        if key in emails or _EMAIL_SKIP.search(email) or _EMAIL_FILE.search(email):
            continue
        _, hint = _hint(text, m.start())
        emails[key] = (m.start(), RecruiterContact(kind="email", value=email, has_contact_hint=hint))

    def ordered(items: dict[str, tuple[int, RecruiterContact]], limit: int) -> list[RecruiterContact]:
        rows = sorted(items.values(), key=lambda p: (not p[1].has_contact_hint, p[0]))
        return [c for _, c in rows[:limit]]

    return ordered(found, MAX_TELEGRAM) + ordered(emails, MAX_EMAIL)

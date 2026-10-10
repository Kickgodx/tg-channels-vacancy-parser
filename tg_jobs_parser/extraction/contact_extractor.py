"""Контакты: email, telegram, телефон (упрощённо)."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_PHONE = re.compile(r"(?:\+?\d[\d\s\-()]{8,}\d)")
_TG = re.compile(r"(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{3,})", re.I)
_AT = re.compile(r"(?<!\w)@([a-zA-Z][a-zA-Z0-9_]{3,})\b")


def extract_contact(text: str) -> str | None:
    """Первая найденная контактная строка."""
    parts: list[str] = []
    if m := _EMAIL.search(text):
        parts.append(m.group(0))
    if m := _TG.search(text):
        parts.append(f"@{m.group(1)}")
    if not parts and (m := _AT.search(text)):
        parts.append(f"@{m.group(1)}")
    if not parts and (m := _PHONE.search(text)):
        parts.append(m.group(0).strip())
    return ", ".join(dict.fromkeys(parts)) if parts else None

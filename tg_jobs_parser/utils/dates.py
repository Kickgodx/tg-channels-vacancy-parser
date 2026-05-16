"""Помощники для диапазонов дат и ISO."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def utc_now() -> datetime:
    """Текущий момент в UTC."""
    return datetime.now(timezone.utc)


def clamp_range_days(dt_from: date, dt_to: date, max_days: int) -> tuple[date, date]:
    """Ограничивает длину диапазона ``max_days`` днями (включительно)."""
    if dt_from > dt_to:
        dt_from, dt_to = dt_to, dt_from
    span = (dt_to - dt_from).days + 1
    if span <= max_days:
        return dt_from, dt_to
    return dt_from, dt_from + timedelta(days=max_days - 1)


_TODAY_ALIASES = frozenset({"today", "сегодня", "сейчас", "now"})


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s.strip())


def resolve_search_date_to(s: str | None) -> date | None:
    """
    Верхняя граница поиска: None, пустая строка, ``today`` / ``сегодня`` → сегодня;
    иначе ISO ``YYYY-MM-DD``.
    """
    if not s or not str(s).strip():
        return date.today()
    raw = str(s).strip()
    if raw.lower() in _TODAY_ALIASES:
        return date.today()
    return date.fromisoformat(raw)


def date_in_range(d: datetime | date | None, start: date, end: date) -> bool:
    if d is None:
        return True
    if isinstance(d, datetime):
        dd = d.date()
    else:
        dd = d
    return start <= dd <= end

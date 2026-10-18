"""Помощники для диапазонов дат и ISO."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def utc_now() -> datetime:
    """Текущий момент в UTC."""
    return datetime.now(UTC)


def clamp_range_days(dt_from: date, dt_to: date, max_days: int) -> tuple[date, date]:
    """Ограничивает длину диапазона ``max_days`` днями (включительно), оставляя самые свежие дни.

    Для поиска вакансий важнее свежие посты: обрезается начало окна, ``dt_to`` сохраняется.
    """
    if dt_from > dt_to:
        dt_from, dt_to = dt_to, dt_from
    span = (dt_to - dt_from).days + 1
    if span <= max_days:
        return dt_from, dt_to
    return dt_to - timedelta(days=max_days - 1), dt_to


_TODAY_ALIASES = frozenset({"today", "сегодня", "сейчас", "now"})
_WEEK_AGO_ALIASES = frozenset({"week_ago", "week", "today-7d", "today_minus_week", "неделя", "weekago"})


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s.strip())


def resolve_search_date_from(s: str | None) -> date | None:
    """
    Нижняя граница поиска: None, пустая строка — без нижней границы;
    ``week_ago`` / ``week`` / ``today-7d`` / ``неделя`` → сегодня минус 7 дней;
    иначе ISO ``YYYY-MM-DD``.
    """
    if not s or not str(s).strip():
        return None
    raw = str(s).strip()
    if raw.lower() in _WEEK_AGO_ALIASES:
        return date.today() - timedelta(days=7)
    return date.fromisoformat(raw)


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
    dd = d.date() if isinstance(d, datetime) else d
    return start <= dd <= end

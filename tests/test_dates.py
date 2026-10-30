"""Тесты разбора дат поиска."""

from datetime import date, timedelta

import pytest

from jobdesk.utils.dates import (
    parse_date,
    resolve_search_date_from,
    resolve_search_date_to,
)


def test_resolve_search_date_from_week_ago_aliases():
    expected = date.today() - timedelta(days=7)
    assert resolve_search_date_from("week_ago") == expected
    assert resolve_search_date_from("week") == expected
    assert resolve_search_date_from("today-7d") == expected
    assert resolve_search_date_from("Неделя") == expected


def test_resolve_search_date_from_empty():
    assert resolve_search_date_from(None) is None
    assert resolve_search_date_from("") is None
    assert resolve_search_date_from("   ") is None


def test_resolve_search_date_from_custom_iso():
    assert resolve_search_date_from("2026-01-15") == date(2026, 1, 15)


def test_resolve_search_date_from_invalid():
    with pytest.raises(ValueError):
        resolve_search_date_from("not-a-date")


def test_resolve_search_date_to_today_aliases():
    assert resolve_search_date_to("today") == date.today()
    assert resolve_search_date_to("Сегодня") == date.today()
    assert resolve_search_date_to(None) == date.today()
    assert resolve_search_date_to("") == date.today()
    assert resolve_search_date_to("   ") == date.today()


def test_resolve_search_date_to_custom_iso():
    assert resolve_search_date_to("2026-01-15") == date(2026, 1, 15)


def test_parse_date_unchanged():
    assert parse_date(None) is None
    assert parse_date("2026-01-15") == date(2026, 1, 15)


def test_resolve_search_date_to_invalid():
    with pytest.raises(ValueError):
        resolve_search_date_to("not-a-date")


def test_clamp_range_days_keeps_newest_days():
    from jobdesk.utils.dates import clamp_range_days

    assert clamp_range_days(date(2026, 1, 1), date(2026, 10, 19), 30) == (date(2026, 9, 20), date(2026, 10, 19))
    assert clamp_range_days(date(2026, 10, 1), date(2026, 10, 19), 30) == (date(2026, 10, 1), date(2026, 10, 19))

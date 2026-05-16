"""Тесты разбора дат поиска."""

from datetime import date

import pytest

from tg_jobs_parser.utils.dates import parse_date, resolve_search_date_to


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

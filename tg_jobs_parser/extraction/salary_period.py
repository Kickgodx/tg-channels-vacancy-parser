"""Период зарплаты: почасовая / месячная (суффикс в ``salary_currency``)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from tg_jobs_parser.fx.convert import normalize_currency

if TYPE_CHECKING:
    from tg_jobs_parser.models.vacancy import Vacancy

# «В час» — только целое слово (не «в части»).
_HOURLY_IN_HOUR_PHRASE = r"в\s+час\b"

# Хвост ставки «за час» (₽/час, руб/час, $/hr, …).
_HOURLY_RATE_TAIL = (
    r"(?:"
    + _HOURLY_IN_HOUR_PHRASE
    + r"|"
    r"(?:₽|руб\.?|rub|rur|\$|usd|€|eur)\s*/\s*(?:час|ч)\b|"
    r"/\s*h(?:r|our)?s?\b|"
    r"per\s+hour|"
    r"hourly(?:\s+rate|\s+pay)?|"
    r"почасов(?:ая|ой|ую|ые|но)?(?:\s+оплат\w*)?"
    r")"
)

# Маркеры почасовой оплаты в тексте вакансии.
_HOURLY_IN_TEXT = re.compile(
    r"(?:"
    + _HOURLY_RATE_TAIL
    + r"|ставка\s+за\s+час|"
    r"(?:от|from)\s*(?:\$|₽|руб\.?|rub)?\s*\d[\d\s.,]*\s*(?:до|[-–])\s*"
    r"(?:\$|₽|руб\.?|rub)?\s*\d[\d\s.,]*\s*(?:\$|usd|₽|руб\.?|rub)?\s*(?:"
    + _HOURLY_RATE_TAIL
    + r")"
    r")",
    re.I,
)

# Месячная вилка: «200 тр», «300к гросс», оклад, gross/net.
_MONTHLY_IN_TEXT = re.compile(
    r"(?:"
    r"\bвилк\w*|"
    r"\bоклад\w*|"
    r"\bгросс\b|\bнет\b|\bна\s+руки\b|"
    r"\d[\d\s.,]*\s*(?:тр|тыс\.?)\b|"
    r"\d[\d\s.,]*\s*к\s*(?:\s*(?:гросс|нет|руб|₽)|\b)|"
    r"(?:от|до)\s+\d[\d\s.,]*\s*(?:тр|тыс|к)\b"
    r")",
    re.I,
)


def monthly_salary_markers_in_text(text: str) -> bool:
    return bool(text and _MONTHLY_IN_TEXT.search(text.lower()))


def hourly_markers_in_text(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    if not _HOURLY_IN_TEXT.search(low):
        return False
    if monthly_salary_markers_in_text(text):
        return False
    return True


def currency_with_hourly_suffix(currency: str | None, *, hourly: bool) -> str | None:
    if not currency:
        return None
    if "/" in str(currency):
        return str(currency).upper().strip()
    base = normalize_currency(currency, default_if_missing="")
    if not base:
        return currency
    if hourly:
        return f"{base}/HOUR"
    return base


def apply_hourly_currency_from_text(vacancy: Vacancy) -> None:
    """
    Если в тексте явно почасовая оплата — ``RUB`` → ``RUB/HOUR`` (и аналоги).

    Не трогает валюту, если период уже задан (``…/HOUR``).
    """
    cur = vacancy.salary_currency
    if cur and "/" in str(cur):
        return
    text = vacancy.raw_text or vacancy.cleaned_text or ""
    if not hourly_markers_in_text(text):
        return
    base = normalize_currency(cur, default_if_missing="RUB")
    if base:
        vacancy.salary_currency = f"{base}/HOUR"


def sanitize_salary_currency_period(vacancy: Vacancy) -> None:
    """
    Убирает ложный ``/HOUR`` (AI или старый прогон), если в тексте месячная вилка.
    """
    cur = vacancy.salary_currency
    if not cur or "/HOUR" not in str(cur).upper():
        return
    text = vacancy.raw_text or vacancy.cleaned_text or ""
    if not text or not monthly_salary_markers_in_text(text):
        return
    if hourly_markers_in_text(text):
        return
    base = normalize_currency(cur, default_if_missing="")
    if base:
        vacancy.salary_currency = base

"""Курсы валют ЦБ РФ (JSON через cbr-xml-daily.ru)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import httpx

_LOG = logging.getLogger(__name__)

CBR_DAILY_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
CBR_ARCHIVE_URL = "https://www.cbr-xml-daily.ru/archive/{y}/{m:02d}/{d:02d}/daily_json.js"


def _parse_cbr_payload(data: dict, *, rate_date: date) -> dict[str, float]:
    """Возвращает курс RUB за 1 единицу валюты (USD, EUR, …)."""
    out: dict[str, float] = {"RUB": 1.0}
    valutes = data.get("Valute") or {}
    for code, item in valutes.items():
        if not isinstance(item, dict):
            continue
        try:
            nominal = float(item.get("Nominal") or 1)
            value = float(str(item.get("Value", "")).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if nominal <= 0:
            continue
        out[str(code).upper()] = value / nominal
    out.setdefault("RUB", 1.0)
    return out


def fetch_cbr_rates(
    rate_date: date | None = None,
    *,
    timeout: float = 20.0,
) -> tuple[date, dict[str, float]]:
    """
    Загружает курсы на дату.

    ``rate_date`` None или сегодня — ``daily_json.js``; иначе — архив ЦБ.
    """
    today = datetime.now(UTC).date()
    target = rate_date or today
    if target > today:
        target = today

    url = CBR_DAILY_URL if target == today else CBR_ARCHIVE_URL.format(y=target.year, m=target.month, d=target.day)

    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    # В daily_json поле Date — дата котировки
    raw_date = data.get("Date")
    if isinstance(raw_date, str):
        try:
            parsed = datetime.strptime(raw_date, "%d.%m.%Y").date()
            target = parsed
        except ValueError:
            pass

    rates = _parse_cbr_payload(data, rate_date=target)
    return target, rates

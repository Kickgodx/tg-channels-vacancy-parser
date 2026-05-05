"""Извлечение зарплаты регэкспами (без конвертации валют)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import NamedTuple


class SalaryResult(NamedTuple):
    min_value: float | None
    max_value: float | None
    avg_value: float | None
    currency: str | None


def _to_float(s: str) -> float | None:
    s = s.replace(" ", "").replace("\u202f", "").replace(",", ".")
    try:
        return float(Decimal(s))
    except (InvalidOperation, ValueError):
        return None


def _scale_if_k(amount: float, fragment: str) -> float:
    if re.search(r"\bk\b|\bтыс\b|\bк\b", fragment, re.I) and amount < 1000:
        return amount * 1000
    return amount


def _guess_currency(text_lower: str, explicit: str | None) -> str | None:
    cur = _norm_currency(explicit)
    if cur:
        return cur
    if "$" in text_lower or "usd" in text_lower:
        return "USD"
    if "€" in text_lower or "eur" in text_lower:
        return "EUR"
    if "руб" in text_lower or "rub" in text_lower or "₽" in text_lower:
        return "RUB"
    return None


def _norm_currency(token: str | None) -> str | None:
    if not token:
        return None
    t = token.lower().strip()
    if t in {"$", "usd"}:
        return "USD"
    if t in {"€", "eur"}:
        return "EUR"
    if t in {"rub", "руб", "₽"}:
        return "RUB"
    return token.upper()


def extract_salary(text: str) -> SalaryResult:
    """Best-effort: вилка или одиночное значение."""
    low = text.lower()
    low_compact = re.sub(r"(\d+)\s*к\b", r"\1k", low)
    low_compact = re.sub(r"(\d+)\s*k\b", r"\1k", low_compact)

    range_m = re.search(
        r"(\d[\d\s]*(?:[.,]\d+)?)\s*[-–]\s*(\d[\d\s]*(?:[.,]\d+)?)"
        r"(?:\s*k)?"
        r"(?:\s*(usd|eur|rub|€|\$|руб))?",
        low_compact,
        re.I,
    )
    if range_m:
        ctx = range_m.group(0)
        a = _to_float(range_m.group(1))
        b = _to_float(range_m.group(2))
        cur_raw = range_m.group(3)
        if a is not None and b is not None:
            a, b = min(a, b), max(a, b)
            a = _scale_if_k(a, ctx)
            b = _scale_if_k(b, ctx)
            avg = (a + b) / 2
            return SalaryResult(a, b, avg, _guess_currency(low_compact, cur_raw))

    from_m = re.search(
        r"\b(?:от|from)\s*:?\s*(\d[\d\s]*(?:[.,]\d+)?)(?:\s*k)?(?:\s*(usd|eur|rub|€|\$|руб))?",
        low_compact,
        re.I,
    )
    if from_m:
        ctx = from_m.group(0)
        a = _to_float(from_m.group(1))
        cur_raw = from_m.group(2)
        if a is not None:
            a = _scale_if_k(a, ctx)
            return SalaryResult(a, None, a, _guess_currency(low_compact, cur_raw))

    to_m = re.search(
        r"\b(?:до)\s*:?\s*(\d[\d\s]*(?:[.,]\d+)?)(?:\s*k)?(?:\s*(usd|eur|rub|€|\$|руб))?",
        low_compact,
        re.I,
    )
    if to_m:
        ctx = to_m.group(0)
        a = _to_float(to_m.group(1))
        cur_raw = to_m.group(2)
        if a is not None:
            a = _scale_if_k(a, ctx)
            return SalaryResult(None, a, a, _guess_currency(low_compact, cur_raw))

    single_m = re.search(r"(?:\$|€)?\s*(\d[\d\s]{2,})\s*(usd|eur|rub|€|\$|руб)?", low_compact)
    if single_m:
        a = _to_float(single_m.group(1))
        cur_raw = single_m.group(2)
        if a is not None:
            return SalaryResult(a, None, a, _guess_currency(low_compact, cur_raw))

    return SalaryResult(None, None, None, None)

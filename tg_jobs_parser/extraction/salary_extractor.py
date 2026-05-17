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


# Международные/локальные телефоны: «+998…», «+7 (495) …», «8-800-…», «+1 555 1234567».
# Маскируем их пробелами, чтобы цифры из контактов не попадали в кандидаты на зарплату.
_PHONE_NUMBER = re.compile(r"(?:\+\d|\b8)[\d\s\-()]{7,}\d")

# URL (в т.ч. id вакансии в path: …/software/76348/) — не источник зарплаты.
_URL = re.compile(r"https?://\S+", re.I)

# Случаи, когда зарплата прямо помечена как «не указана»: её нельзя восстанавливать
# из случайных чисел в тексте.
_NEGOTIABLE = re.compile(
    r"(?:по\s+договорённост|по\s+договоренност|договорн(?:ая|ой|ые)|"
    r"по\s+результата(?:м|х)\s+собеседов|по\s+итогам\s+(?:собеседов|интервью)|"
    r"обсужда(?:ется|емо)|обговар|negotiable|"
    r"competitive\s+(?:salary|compensation|package)|"
    r"depend(?:s|ing)?\s+on\s+experience|"
    r"salary\s+(?:is\s+)?(?:tbd|to\s+be\s+discussed|will\s+be\s+discussed))",
    re.I,
)

# «Якорные» слова — рядом с ними число почти наверняка действительно зарплата.
_SALARY_ANCHOR = (
    r"зарплат\w*|з\s*[/.]\s*п\b|зп\b|оклад\w*|доход\w*|вилка\w*|"
    r"salary|compensation|rate\b|pay\b|comp\b|tc\b|"
    r"на\s+руки|до\s+вычета|gross|net\b"
)

_CURRENCY_TOKEN = r"usd|eur|rub|руб|₽|€|\$|gbp|kzt|сум|тенге"


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


def _looks_like_year(amount: float) -> bool:
    """Целое 1900–2099 — почти наверняка год, а не зарплата."""
    return 1900 <= amount <= 2099 and amount == int(amount)


def _is_plausible_anchored_salary(amount: float) -> bool:
    """Числа рядом с валютой/якорем доверяем шире: исключаем только годы и явный мусор."""
    if amount <= 0:
        return False
    if _looks_like_year(amount):
        return False
    return amount <= 1_000_000_000


def _is_plausible_bare_salary(amount: float) -> bool:
    """
    Для «голых» чисел без валюты/якоря порог жёстче.

    Нижняя граница 10_000 отсекает «опыт 3-5 лет», «команда из 50 человек», часы и проценты;
    верхняя 100_000_000 — реквизиты счетов/ИНН/ID. Это эвристика на типичные RUB-зарплаты
    (от ~30k до ~5M в месяц).
    """
    if _looks_like_year(amount):
        return False
    return 10_000 <= amount <= 100_000_000


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

    if _NEGOTIABLE.search(low):
        return SalaryResult(None, None, None, None)

    # Чистим телефоны и URL перед поиском чисел: id в ссылке не должен быть «зарплатой».
    cleaned = _PHONE_NUMBER.sub(" ", low)
    cleaned = _URL.sub(" ", cleaned)

    low_compact = re.sub(r"(\d+)\s*к\b", r"\1k", cleaned)
    low_compact = re.sub(r"(\d+)\s*k\b", r"\1k", low_compact)

    range_m = re.search(
        r"(\d[\d\s]*(?:[.,]\d+)?)\s*[-–]\s*(\d[\d\s]*(?:[.,]\d+)?)"
        r"(?:\s*k)?"
        r"(?:\s*(" + _CURRENCY_TOKEN + r"))?",
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
            # Если рядом нет валютного токена — фильтруем диапазоны вроде «3-5 лет», «5–10 человек».
            if cur_raw or (_is_plausible_bare_salary(a) and _is_plausible_bare_salary(b)):
                avg = (a + b) / 2
                return SalaryResult(a, b, avg, _guess_currency(low_compact, cur_raw))

    from_m = re.search(
        r"\b(?:от|from)\s*:?\s*(\d[\d\s]*(?:[.,]\d+)?)(?:\s*k)?(?:\s*(" + _CURRENCY_TOKEN + r"))?",
        low_compact,
        re.I,
    )
    if from_m:
        ctx = from_m.group(0)
        a = _to_float(from_m.group(1))
        cur_raw = from_m.group(2)
        if a is not None:
            a = _scale_if_k(a, ctx)
            if cur_raw or _is_plausible_bare_salary(a):
                return SalaryResult(a, None, a, _guess_currency(low_compact, cur_raw))

    to_m = re.search(
        r"\b(?:до)\s*:?\s*(\d[\d\s]*(?:[.,]\d+)?)(?:\s*k)?(?:\s*(" + _CURRENCY_TOKEN + r"))?",
        low_compact,
        re.I,
    )
    if to_m:
        ctx = to_m.group(0)
        a = _to_float(to_m.group(1))
        cur_raw = to_m.group(2)
        if a is not None:
            a = _scale_if_k(a, ctx)
            if cur_raw or _is_plausible_bare_salary(a):
                return SalaryResult(None, a, a, _guess_currency(low_compact, cur_raw))

    # Якорь зарплаты рядом с числом ("salary 150000", "оклад 200к", "rate 50/hr").
    # Допускает до 30 не-цифровых символов между ключевым словом и числом.
    anchored = re.search(
        r"(?:" + _SALARY_ANCHOR + r")[^\d\n]{0,30}"
        r"(\d[\d\s]*(?:[.,]\d+)?)\s*(k)?\s*(" + _CURRENCY_TOKEN + r")?",
        low_compact,
        re.I,
    )
    if anchored:
        ctx = anchored.group(0)
        a = _to_float(anchored.group(1))
        cur_raw = anchored.group(3)
        if a is not None:
            a = _scale_if_k(a, ctx)
            if _is_plausible_anchored_salary(a):
                return SalaryResult(a, None, a, _guess_currency(low_compact, cur_raw))

    # Число с явной валютой или k-маркером (без якорного слова рядом).
    currency_before = re.search(
        r"(\$|€|₽|usd|eur|rub|руб)\s*(\d[\d\s]{2,}(?:[.,]\d+)?)",
        low_compact,
        re.I,
    )
    if currency_before:
        a = _to_float(currency_before.group(2))
        if a is not None and _is_plausible_anchored_salary(a):
            return SalaryResult(a, None, a, _guess_currency(low_compact, currency_before.group(1)))

    currency_after = re.search(
        r"(\d[\d\s]{2,}(?:[.,]\d+)?)\s*(k\b|\$|€|₽|usd|eur|rub|руб)",
        low_compact,
        re.I,
    )
    if currency_after:
        ctx = currency_after.group(0)
        a = _to_float(currency_after.group(1))
        if a is not None:
            a = _scale_if_k(a, ctx)
            if _is_plausible_anchored_salary(a):
                tail = currency_after.group(2)
                cur_raw = None if tail.lower().strip() == "k" else tail
                return SalaryResult(a, None, a, _guess_currency(low_compact, cur_raw))

    # Финальный фоллбэк: голое число (например, «150000» без слова «зарплата» и без валюты).
    # Телефоны уже замаскированы; здесь применяем строгий диапазон, чтобы не ловить
    # количество подписчиков/часы/проценты/годы и пр.
    for m in re.finditer(r"\b(\d[\d\s]{3,}(?:[.,]\d+)?)\b", low_compact):
        a = _to_float(m.group(1))
        if a is not None and _is_plausible_bare_salary(a):
            return SalaryResult(a, None, a, _guess_currency(low_compact, None))

    return SalaryResult(None, None, None, None)

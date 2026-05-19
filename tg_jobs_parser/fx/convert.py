"""Конвертация сумм зарплаты в RUB."""

from __future__ import annotations

from datetime import date, datetime, timezone

# Коды и символы валют → канонический код в exchange_rates / ЦБ.
_CURRENCY_ALIASES: dict[str, str] = {
    "RUR": "RUB",
    "₽": "RUB",
    "$": "USD",
    "US$": "USD",
    "€": "EUR",
    "USDT": "USD",
    "USDC": "USD",
}

# Почасовая ставка → месяц (среднее: 21 раб. день × 8 ч).
WORKING_DAYS_PER_MONTH = 21
HOURS_PER_WORK_DAY = 8
HOURS_PER_MONTH = WORKING_DAYS_PER_MONTH * HOURS_PER_WORK_DAY

_HOURLY_SUFFIXES = frozenset({"H", "HR", "HOUR", "HOURS", "Ч", "ЧАС", "ЧАСА", "ЧАСОВ"})
_MONTHLY_SUFFIXES = frozenset({"M", "MON", "MONTH", "MONTHLY", "МЕС", "МЕСЯЦ"})
_YEAR_SUFFIXES = frozenset({"Y", "YR", "YEAR", "ANNUAL", "ГОД", "ГОДОВАЯ"})


def _period_from_suffix(suffix: str) -> str:
    s = suffix.upper().strip()
    if s in _HOURLY_SUFFIXES:
        return "hour"
    if s in _YEAR_SUFFIXES:
        return "year"
    if s in _MONTHLY_SUFFIXES:
        return "month"
    return "month"


def parse_salary_currency(currency: str | None) -> tuple[str | None, str]:
    """
    Разбор ``salary_currency``: ISO-код и период (month | hour | year).

    Примеры: ``RUB/HOUR`` → (``RUB``, ``hour``), ``USD`` → (``USD``, ``month``).
    """
    if not currency or not str(currency).strip():
        return None, "month"
    raw = str(currency).upper().strip()
    if "/" in raw:
        base, suffix = raw.split("/", 1)
        base = base.strip()
        period = _period_from_suffix(suffix)
    else:
        base = raw
        period = "month"
    if not base:
        return None, period
    iso = _CURRENCY_ALIASES.get(base, base)
    return iso, period


def currency_for_rate_lookup(code: str | None) -> str | None:
    """Код валюты для поиска курса в ``exchange_rates`` / ответе ЦБ."""
    iso, _ = parse_salary_currency(code)
    return iso


def normalize_currency(currency: str | None, *, default_if_missing: str = "RUB") -> str | None:
    iso, _ = parse_salary_currency(currency)
    if not iso:
        return default_if_missing
    return iso


def is_ruble_currency(currency: str | None) -> bool:
    """RUB и RUR — одна валюта (рубль)."""
    return normalize_currency(currency, default_if_missing="") == "RUB"


def to_monthly_salary_amount(amount: float, currency: str | None) -> float:
    """Приводит сумму к месячному эквиваленту по суффиксу в ``salary_currency``."""
    _, period = parse_salary_currency(currency)
    if period == "hour":
        return float(amount) * HOURS_PER_MONTH
    if period == "year":
        return float(amount) / 12.0
    return float(amount)


def effective_salary_amount(
    *,
    salary_avg: float | None = None,
    salary_min: float | None = None,
    salary_max: float | None = None,
) -> float | None:
    """Одно число для зарплаты (как в metrics_builder / vacancy_metrics)."""
    if salary_avg is not None:
        return float(salary_avg)
    if salary_min is not None and salary_max is not None:
        return (float(salary_min) + float(salary_max)) / 2.0
    if salary_min is not None:
        return float(salary_min)
    if salary_max is not None:
        return float(salary_max)
    return None


def monthly_salary_amount(
    amount: float | None,
    currency: str | None,
) -> float | None:
    """Сумма зарплаты в месячном выражении (для сравнения и конвертации в RUB)."""
    if amount is None:
        return None
    return to_monthly_salary_amount(amount, currency)


def rate_date_for_vacancy(post_date: datetime | None) -> date:
    """Календарный день поста (для архива/отладки; не для отображения зарплаты)."""
    if post_date is None:
        return datetime.now(timezone.utc).date()
    if getattr(post_date, "tzinfo", None):
        return post_date.astimezone(timezone.utc).date()
    return post_date.date()


def rate_date_for_live_display() -> date:
    """Дата курса для отображения вакансий: сегодня (последний снимок ЦБ в кэше)."""
    return datetime.now(timezone.utc).date()


def salary_amount_to_rub(
    amount: float | None,
    currency: str | None,
    rates: dict[str, float],
    *,
    default_currency_if_missing: str = "RUB",
) -> float | None:
    if amount is None:
        return None
    monthly = monthly_salary_amount(amount, currency)
    if monthly is None:
        return None
    cur = normalize_currency(currency, default_if_missing=default_currency_if_missing)
    if cur is None:
        return None
    if is_ruble_currency(cur):
        return monthly
    lookup = currency_for_rate_lookup(cur)
    rate = rates.get(lookup) if lookup else None
    if rate is None:
        return None
    return monthly * float(rate)

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


def currency_for_rate_lookup(code: str | None) -> str | None:
    """Код валюты для поиска курса в ``exchange_rates`` / ответе ЦБ."""
    if not code or not str(code).strip():
        return None
    u = str(code).upper().strip()
    return _CURRENCY_ALIASES.get(u, u)


def normalize_currency(currency: str | None, *, default_if_missing: str = "RUB") -> str | None:
    if not currency or not str(currency).strip():
        return default_if_missing
    u = str(currency).upper().strip()
    return _CURRENCY_ALIASES.get(u, u)


def is_ruble_currency(currency: str | None) -> bool:
    """RUB и RUR — одна валюта (рубль)."""
    return normalize_currency(currency, default_if_missing="") == "RUB"


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
    cur = normalize_currency(currency, default_if_missing=default_currency_if_missing)
    if cur is None:
        return None
    if is_ruble_currency(cur):
        return float(amount)
    lookup = currency_for_rate_lookup(cur)
    rate = rates.get(lookup) if lookup else None
    if rate is None:
        return None
    return float(amount) * float(rate)

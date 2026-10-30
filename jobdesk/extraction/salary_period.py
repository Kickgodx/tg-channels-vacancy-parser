"""Период зарплаты: почасовая / годовая / месячная (суффикс в ``salary_currency``)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from jobdesk.fx.convert import normalize_currency, parse_salary_currency

if TYPE_CHECKING:
    from jobdesk.models.vacancy import Vacancy

# «В час» — только целое слово (не «в части»).
_HOURLY_IN_HOUR_PHRASE = r"в\s+час\b"

# Хвост ставки «за час» (₽/час, руб/час, $/hr, …).
_HOURLY_RATE_TAIL = (
    r"(?:" + _HOURLY_IN_HOUR_PHRASE + r"|"
    r"(?:₽|руб\.?|rub|rur|\$|usd|€|eur)\s*/\s*(?:час|ч)\b|"
    r"/\s*h(?:r|our)?s?\b|"
    r"per\s+hour|"
    r"hourly(?:\s+rate|\s+pay)?|"
    r"почасов(?:ая|ой|ую|ые|но)?(?:\s+оплат\w*)?"
    r")"
)

# Маркеры почасовой оплаты в тексте вакансии.
_HOURLY_IN_TEXT = re.compile(
    r"(?:" + _HOURLY_RATE_TAIL + r"|ставка\s+за\s+час|"
    # «Оплата 400 - 550/ч» — без знака валюты перед «/ч».
    r"\d\s*/\s*(?:час|ч)\b|"
    r"(?:от|from)\s*(?:\$|₽|руб\.?|rub)?\s*\d[\d\s.,]*\s*(?:до|[-–])\s*"
    r"(?:\$|₽|руб\.?|rub)?\s*\d[\d\s.,]*\s*(?:\$|usd|₽|руб\.?|rub)?\s*(?:" + _HOURLY_RATE_TAIL + r")"
    r")",
    re.I,
)

# Месячная вилка: «200 тр», «300к гросс», оклад, gross/net.
_MONTHLY_IN_TEXT = re.compile(
    r"(?:"
    r"\bвилк\w*|"
    r"\bоклад\w*|"
    r"\bгросс\b|\bнет\b|\bна\s+руки\b|"
    r"/\s*мес\w*|\bв\s+месяц\b|\bper\s+month\b|/\s*month\b|"
    r"\d[\d\s.,]*\s*(?:тр|тыс\.?)\b|"
    r"\d[\d\s.,]*\s*к\s*(?:\s*(?:гросс|нет|руб|₽)|\b)|"
    r"(?:от|до)\s+\d[\d\s.,]*\s*(?:тр|тыс|к)\b"
    r")",
    re.I,
)

# Явные годовые маркеры (не «6 year(s)» опыта, не голый $125k — это решает LLM).
_ANNUAL_IN_TEXT = re.compile(
    r"(?:"
    r"\bannually\b|"
    r"\bannual(?:\s+(?:salary|pay|compensation|package|base))\b|"
    r"\bper\s+year\b|"
    r"/\s*y(?:ea)?r\b|"
    r"\byearly\b|"
    r"\bв\s+год\b|"
    r"\bгодов(?:ая|ой|ую|ые|ый)\b|"
    r"\bза\s+год\b"
    r")",
    re.I,
)

# JPY: месячная никогда не бывает миллионами — безопасно делить по потолку.
_JPY_MONTHLY_CEILING = 2_500_000.0

# Западные валюты: делим только если сумма явно выше любого реалистичного месяца
# (директор ~250–450k/год → ~20–37k/мес; 35k может быть и годом джуна, и месяцем директора).
_WESTERN_ANNUAL_FLOOR = 80_000.0
_WESTERN_ISO = frozenset({"USD", "EUR", "GBP", "CAD"})


def monthly_salary_markers_in_text(text: str) -> bool:
    return bool(text and _MONTHLY_IN_TEXT.search(text.lower()))


def annual_markers_in_text(text: str) -> bool:
    return bool(text and _ANNUAL_IN_TEXT.search(text))


def hourly_markers_in_text(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    if not _HOURLY_IN_TEXT.search(low):
        return False
    return not monthly_salary_markers_in_text(text)


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
    # «от $20/час или от $2000/мес» — сохранена месячная сумма: $2000 за час не бывает.
    amount = _salary_reference_amount(vacancy)
    max_hourly = _MAX_HOURLY_RUB if base == "RUB" else _MAX_HOURLY_OTHER
    if amount is not None and amount >= max_hourly:
        return
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


def _salary_reference_amount(vacancy: Vacancy) -> float | None:
    if vacancy.detected_salary_avg is not None:
        return float(vacancy.detected_salary_avg)
    if vacancy.detected_salary_min is not None and vacancy.detected_salary_max is not None:
        return (float(vacancy.detected_salary_min) + float(vacancy.detected_salary_max)) / 2.0
    if vacancy.detected_salary_min is not None:
        return float(vacancy.detected_salary_min)
    if vacancy.detected_salary_max is not None:
        return float(vacancy.detected_salary_max)
    return None


def _safe_to_convert_annual_amount(amount: float, currency_iso: str | None) -> bool:
    """
    Консервативно: только суммы, которые не пересекаются с месячными после /12.

    Низкие западные годы (26k–50k) и «уже месяц» после директора (~20–37k) —
    зона LLM, код не трогает.
    """
    iso = (currency_iso or "").upper()
    val = float(amount)
    if iso == "JPY":
        return val > _JPY_MONTHLY_CEILING
    if iso in _WESTERN_ISO:
        return val >= _WESTERN_ANNUAL_FLOOR
    return False


def normalize_annual_salary_to_monthly(vacancy: Vacancy) -> bool:
    """
    Годовые суммы → месячные (÷12), валюта без периода.

    Безопасные триггеры:
    - суффикс ``…/YEAR`` (явный период от LLM);
    - явный annual-маркер в тексте + сумма вне зоны неоднозначности
      (JPY ≫ месяца; USD/EUR/GBP/CAD ≥ 80k).

    ``$125k`` без Annually и низкие годы (30k USD Annually) — только промпт.
    """
    cur = vacancy.salary_currency
    if not cur:
        return False
    iso, period = parse_salary_currency(cur)
    if period == "hour":
        return False

    text = vacancy.raw_text or vacancy.cleaned_text or ""
    explicit_year = period == "year"
    if not explicit_year and not annual_markers_in_text(text):
        return False

    amount = _salary_reference_amount(vacancy)
    if amount is None:
        return False
    # /YEAR всегда означает «суммы годовые»; иначе — только безопасные пороги.
    if not explicit_year and not _safe_to_convert_annual_amount(amount, iso):
        return False

    changed = False
    for attr in ("detected_salary_min", "detected_salary_max", "detected_salary_avg"):
        val = getattr(vacancy, attr)
        if val is None:
            continue
        setattr(vacancy, attr, float(val) / 12.0)
        changed = True
    if iso and vacancy.salary_currency != iso:
        vacancy.salary_currency = iso
        changed = True
    return changed


# Сумма без валюты ниже этой — не месячная зарплата: «8» из «Posted: 08», «2500» без $/€/₽
# посчитался бы как 2 500 ₽. Тот же порог, что у «голых» чисел в правилах.
_MIN_MONTHLY_WITHOUT_CURRENCY = 10_000.0
# Месячная зарплата в рублях не бывает меньше тысячи (стипендии и парт-тайм — от нескольких тысяч).
_MIN_MONTHLY_RUB = 1_000.0
# Рублёвая «месячная» сумма ниже этой при почасовом маркере в тексте — ставка за час;
# от неё и выше (и от 1 000 в валюте) почасовой суффикс по тексту не ставится.
_MAX_HOURLY_RUB = 10_000.0
_MAX_HOURLY_OTHER = 1_000.0


def _clear_salary(vacancy: Vacancy) -> None:
    vacancy.detected_salary_min = None
    vacancy.detected_salary_max = None
    vacancy.detected_salary_avg = None
    vacancy.salary_currency = None


def sanitize_salary_amounts(vacancy: Vacancy, *, default_currency_if_missing: str | None = "RUB") -> bool:
    """
    Последняя проверка сумм после разбора (правила или LLM) — вызывать после нормализации периода.

    - ``min > max`` → меняются местами (подборки, где смешались вилки двух вакансий);
    - ``avg`` всегда выводится из ``min``/``max``;
    - рублёвая «месячная» сумма до 10 000 при почасовом маркере в тексте → ``RUB/HOUR``;
    - без валюты меньше 10 000 или в рублях меньше 1 000 в месяц — зарплата сбрасывается.
      Порог «без валюты» — только когда такая сумма считается в рублях
      (``fx.default_currency_if_missing: RUB``): при ``USD`` 2 000 без валюты — нормальный месяц.
    """
    before = (
        vacancy.detected_salary_min,
        vacancy.detected_salary_max,
        vacancy.detected_salary_avg,
        vacancy.salary_currency,
    )
    smin, smax = vacancy.detected_salary_min, vacancy.detected_salary_max
    if smin is None and smax is None:
        if vacancy.detected_salary_avg is None:
            return False
    else:
        if smin is not None and smax is not None and smin > smax:
            smin, smax = smax, smin
        vacancy.detected_salary_min = smin
        vacancy.detected_salary_max = smax
        if smin is not None and smax is not None:
            avg = (float(smin) + float(smax)) / 2.0
        else:
            avg = float(smin if smin is not None else smax)
        if vacancy.detected_salary_avg is None or abs(vacancy.detected_salary_avg - avg) > 0.01:
            vacancy.detected_salary_avg = avg

    iso, period = parse_salary_currency(vacancy.salary_currency)
    missing_is_rub = normalize_currency(default_currency_if_missing, default_if_missing="") == "RUB"
    amount = float(vacancy.detected_salary_avg)
    if period == "month":
        text = vacancy.raw_text or vacancy.cleaned_text or ""
        # Сумма уже мала для месяца, поэтому месячные слова в тексте («вилка») не отменяют «/ч».
        if iso == "RUB" and amount < _MAX_HOURLY_RUB and _HOURLY_IN_TEXT.search(text.lower()):
            vacancy.salary_currency = "RUB/HOUR"
        elif (iso is None and missing_is_rub and amount < _MIN_MONTHLY_WITHOUT_CURRENCY) or (
            iso == "RUB" and amount < _MIN_MONTHLY_RUB
        ):
            _clear_salary(vacancy)

    after = (
        vacancy.detected_salary_min,
        vacancy.detected_salary_max,
        vacancy.detected_salary_avg,
        vacancy.salary_currency,
    )
    return after != before

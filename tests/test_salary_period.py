from datetime import datetime, timezone

from tg_jobs_parser.extraction.salary_period import (
    hourly_markers_in_text,
    sanitize_salary_currency_period,
)
from tg_jobs_parser.models.vacancy import Vacancy


def _vacancy(**kwargs) -> Vacancy:
    base = dict(
        id="ch-1",
        source_post_key="ch-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text="text",
        cleaned_text="text",
        parsed_at=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    return Vacancy(**base)


def test_v_chasti_is_not_hourly_marker():
    text = "критерии перевода в регресс) в части контроля качества"
    assert hourly_markers_in_text(text) is False


def test_sanitize_strips_false_hourly_from_ai():
    text = "Вилка: от 200 тр до 300 тр"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        salary_currency="RUB/HOUR",
        detected_salary_avg=250_000,
    )
    sanitize_salary_currency_period(v)
    assert v.salary_currency == "RUB"

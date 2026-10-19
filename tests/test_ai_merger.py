"""Тесты слияния rules + AI."""

from datetime import UTC, datetime

from tg_jobs_parser.ai.merger import merge_rules_and_ai
from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.vacancy import Vacancy


def _vacancy(**kwargs) -> Vacancy:
    base = dict(
        id="ch-1",
        source_post_key="ch-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text="text",
        cleaned_text="text",
        detected_specializations=["Backend"],
        vacancy_score=2,
        parsed_at=datetime.now(UTC),
    )
    base.update(kwargs)
    return Vacancy(**base)


def test_merge_ai_salary_and_specs():
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "mode": "assist"})})
    v = _vacancy(detected_salary_min=None, work_format=None)
    ai = VacancyAIExtraction(
        is_job_post=True,
        detected_specializations=["Fullstack", "Go", "Rust"],
        salary_min=300000,
        salary_max=400000,
        salary_currency="RUB",
        work_format="remote",
        application_urls=["https://hh.ru/vacancy/1"],
        confidence=0.85,
    )
    out = merge_rules_and_ai(v, ai, cfg)
    assert out.detected_specializations == ["Backend", "Fullstack"]
    assert out.technologies == ["Go", "Rust"]
    assert out.detected_salary_min == 300000
    assert out.salary_currency == "RUB"
    assert out.work_format == "remote"
    assert out.application_urls == ["https://hh.ru/vacancy/1"]
    assert out.extraction_source == "hybrid"


def test_primary_grade_from_ai_not_rules():
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "mode": "primary"})})
    v = _vacancy(detected_grade="staff")
    ai = VacancyAIExtraction(
        is_job_post=True,
        detected_grade="senior",
        confidence=0.9,
    )
    out = merge_rules_and_ai(v, ai, cfg)
    assert out.detected_grade == "senior"


def test_primary_unknown_grade_not_rules_fallback():
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "mode": "primary"})})
    v = _vacancy(detected_grade="staff")
    ai = VacancyAIExtraction(is_job_post=True, detected_grade="unknown", confidence=0.9)
    out = merge_rules_and_ai(v, ai, cfg)
    assert out.detected_grade == "unknown"


def test_primary_specs_only_from_ai_not_rules():
    """В primary rule-based ложные QA/аналитик не должны попадать в итог."""
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "mode": "primary"})})
    v = _vacancy(
        detected_specializations=[
            "QA",
            "System Analyst",
            "Business Analyst",
        ],
    )
    ai = VacancyAIExtraction(
        is_job_post=True,
        detected_specializations=["Backend"],
        technologies=["golang"],
        confidence=0.95,
    )
    out = merge_rules_and_ai(v, ai, cfg)
    assert out.detected_specializations == ["Backend"]
    assert out.technologies == ["Go"]


def test_primary_does_not_apply_regex_hourly_from_v_chasti():
    """В primary regex «в части» не должен превращать RUB от LLM в RUB/HOUR."""
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "mode": "primary"})})
    text = "Вилка: от 200 тр до 300 тр. критерии перевода в регресс) в части контроля качества"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=200_000.0,
        detected_salary_max=300_000.0,
        detected_salary_avg=250_000.0,
        salary_currency="RUB/HOUR",
    )
    ai = VacancyAIExtraction(
        is_job_post=True,
        detected_specializations=["QA"],
        salary_min=200_000,
        salary_max=300_000,
        salary_currency="RUB",
        confidence=0.9,
    )
    out = merge_rules_and_ai(v, ai, cfg)
    assert out.salary_currency == "RUB"
    assert out.detected_salary_avg == 250_000.0


def test_primary_clears_rules_salary_when_ai_null():
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "mode": "primary"})})
    v = _vacancy(
        detected_salary_min=76348.0,
        detected_salary_max=None,
        detected_salary_avg=76348.0,
        salary_currency=None,
    )
    ai = VacancyAIExtraction(
        is_job_post=True,
        detected_specializations=["Go"],
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        confidence=0.95,
    )
    out = merge_rules_and_ai(v, ai, cfg)
    assert out.detected_salary_min is None
    assert out.detected_salary_max is None
    assert out.detected_salary_avg is None
    assert out.extraction_source == "ai"


def test_parse_extraction_coerces_list_required_experience():
    from tg_jobs_parser.ai.clients.openrouter import _parse_extraction

    raw = """{
        "is_job_post": true,
        "required_experience": ["3 years of QA experience", "Redis and Hazelcast"],
        "confidence": 0.8
    }"""
    ext = _parse_extraction(raw)
    assert ext.required_experience == "3 years of QA experience; Redis and Hazelcast"


def test_merge_ai_notes():
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "mode": "assist"})})
    v = _vacancy()
    ai = VacancyAIExtraction(
        is_job_post=True,
        notes="Salary range unclear; company name not in post.",
        confidence=0.7,
    )
    out = merge_rules_and_ai(v, ai, cfg)
    assert out.ai_notes == "Salary range unclear; company name not in post."


def test_openrouter_parse_json_fence():
    from tg_jobs_parser.ai.clients.openrouter import _parse_extraction

    raw = """```json
{"is_job_post": true, "detected_specializations": ["QA"], "confidence": 0.8}
```"""
    ext = _parse_extraction(raw)
    assert ext.is_job_post is True
    assert "QA" in ext.detected_specializations


def test_primary_small_rub_with_hourly_text_becomes_hourly():
    """Регресс: LLM вернул «400–550 RUB» на «Оплата 400 - 550/ч» — это ставка за час, а не 475 ₽/мес."""
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "mode": "primary"})})
    text = "QA парт-тайм. Оплата 400 - 550/ч. Контакты @hr"
    v = _vacancy(raw_text=text, cleaned_text=text)
    ai = VacancyAIExtraction(
        is_job_post=True,
        detected_specializations=["QA"],
        salary_min=400,
        salary_max=550,
        salary_currency="RUB",
        confidence=0.8,
    )
    out = merge_rules_and_ai(v, ai, cfg)
    assert out.salary_currency == "RUB/HOUR"
    assert out.detected_salary_avg == 475


def test_primary_drops_small_salary_without_currency():
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "mode": "primary"})})
    v = _vacancy(raw_text="Compensation: from 2500 gross", cleaned_text="Compensation: from 2500 gross")
    ai = VacancyAIExtraction(
        is_job_post=True,
        detected_specializations=["QA"],
        salary_min=2500,
        salary_currency=None,
        confidence=0.8,
    )
    out = merge_rules_and_ai(v, ai, cfg)
    assert out.detected_salary_min is None
    assert out.detected_salary_avg is None

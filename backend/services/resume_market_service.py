"""Резюме против рынка: что платят и что требуют в вакансиях, ближайших к резюме по смыслу.

Ближайшие — уникальные вакансии (с архивом: это история рынка) за ``WINDOW_DAYS`` по
косинусному расстоянию эмбеддингов резюме и вакансии. Из них — профиль (грейды, роли),
частые технологии с отметкой «есть в резюме» и квантили рублёвой зарплаты. LLM не нужен:
вектор резюме уже хранится для «Подбора под резюме».
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.db.models import ResumeRow, VacancyEmbeddingRow, VacancyRow
from backend.db.vacancy_metrics import _prepare_fx
from backend.db.vector_type import query_vector
from backend.schemas import MarketShare, ResumeMarketOut, ResumeMarketTechnology
from backend.services import embedding_service
from backend.services.skill_gap_service import technology_matcher

WINDOW_DAYS = 365
# Профиль и технологии — по стольким ближайшим вакансиям.
PEERS = 50
# Квантили зарплаты — по стольким ближайшим вакансиям с зарплатой (из CANDIDATES ближайших).
SALARY_PEERS = 30
MIN_SALARY_PEERS = 8
CANDIDATES = 200
TOP_PROFILE = 3
TOP_TECHNOLOGIES = 12
# Технология реже этой доли ближайших — не «частая», а шум одной-двух вакансий.
MIN_TECH_SHARE = 0.1


def quantile(sorted_values: list[float], p: float) -> float:
    """Квантиль с линейной интерполяцией — как ``percentile_cont`` в Postgres."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = p * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _shares(counter: Counter, total: int, limit: int) -> list[MarketShare]:
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [MarketShare(name=name, share=n / total) for name, n in items]


def _nearest(session: Session, vector: list[float], *, model: str, now: datetime) -> list:
    e = VacancyEmbeddingRow
    v = VacancyRow
    distance = e.embedding.cosine_distance(query_vector(vector))
    # HNSW отдаёт не больше ef_search строк (по умолчанию 40) — поднимаем до числа кандидатов.
    session.execute(text(f"SET LOCAL hnsw.ef_search = {CANDIDATES}"))
    stmt = (
        select(v.detected_grade, v.detected_specializations, v.technologies, v.detected_salary_avg_rub)
        .select_from(e)
        .join(v, v.id == e.vacancy_id)
        .where(
            e.model == model,
            v.is_duplicate.is_(False),
            v.post_date >= now - timedelta(days=WINDOW_DAYS),
        )
        .order_by(distance, e.vacancy_id)
        .limit(CANDIDATES)
    )
    return session.execute(stmt).all()


def resume_market(session: Session, resume: ResumeRow) -> ResumeMarketOut:
    """Профиль рынка для резюме; ``EmbeddingsUnavailable`` — эмбеддинги выключены или недоступны."""
    vector = embedding_service.resume_embedding(session, resume)
    model = embedding_service._ai().embeddings.model
    rows = _nearest(session, vector, model=model, now=datetime.now(UTC))
    salary_display = _prepare_fx(session)

    resume_techs = technology_matcher(session).find(resume.text)
    own = {t.lower() for t in resume_techs}
    peers = rows[:PEERS]
    out = ResumeMarketOut(
        resume_id=resume.id,
        window_days=WINDOW_DAYS,
        peers=len(peers),
        resume_technologies=resume_techs,
        salary_display=salary_display,
    )
    if not peers:
        return out

    grades = Counter(g for g, *_ in peers if g and g != "unknown")
    roles = Counter(r for _, specs, *_ in peers for r in set(specs or []))
    techs = Counter(t for _, _, ts, _ in peers for t in set(ts or []))
    coverages = [sum(1 for t in set(ts) if t.lower() in own) / len(set(ts)) for _, _, ts, _ in peers if ts]
    top_techs = [
        ResumeMarketTechnology(name=s.name, share=s.share, in_resume=s.name.lower() in own)
        for s in _shares(techs, len(peers), TOP_TECHNOLOGIES)
        if s.share >= MIN_TECH_SHARE
    ]
    # Ближайшие с зарплатой — из всех кандидатов: у большинства вакансий зарплаты нет.
    salaries = sorted([float(s) for *_, s in rows if s is not None][:SALARY_PEERS])
    update: dict = {
        "grades": _shares(grades, len(peers), TOP_PROFILE),
        "specializations": _shares(roles, len(peers), TOP_PROFILE),
        "technologies": top_techs,
        "coverage": sum(coverages) / len(coverages) if coverages else None,
    }
    if len(salaries) >= MIN_SALARY_PEERS:
        update.update(
            salary_count=len(salaries),
            p10=quantile(salaries, 0.1),
            p25=quantile(salaries, 0.25),
            median=quantile(salaries, 0.5),
            p75=quantile(salaries, 0.75),
            p90=quantile(salaries, 0.9),
        )
    return out.model_copy(update=update)

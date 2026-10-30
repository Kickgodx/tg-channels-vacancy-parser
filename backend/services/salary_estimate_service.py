"""Ориентировочная зарплата вакансии без вилки: kNN по эмбеддингам среди вакансий с зарплатой.

Похожие — ближайшие по косинусному расстоянию уникальные вакансии (с архивом) с рублёвой
зарплатой и вектором текущей модели, опубликованные за год до поста вакансии. При известном
грейде — только того же грейда: эмбеддинг хорошо ловит роль и стек, но плохо — уровень, а
зарплата зависит от него сильнее всего. Оценка — медиана и p25–p75 зарплат ``NEIGHBOURS``
ближайших; меньше ``MIN_NEIGHBOURS`` — оценки нет.

Оценки хранит ``vacancy_salary_estimates``: их пересчитывает лидер API после индексации
эмбеддингов (новые, изменённые и старше ``REFRESH_DAYS``), карточка при отсутствии свежей
оценки считает её на лету. В аналитику оценки не входят — только флаг списка ``salary_estimates``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import VacancyEmbeddingRow, VacancyRow, VacancySalaryEstimateRow
from backend.db.session import session_scope
from backend.db.vector_type import query_vector

_LOG = logging.getLogger(__name__)

NEIGHBOURS = 15
MIN_NEIGHBOURS = 8
WINDOW_DAYS = 365
REFRESH_DAYS = 7
MAX_PER_PASS = 1000
# Сколько ближайших показать в карточке рядом с оценкой.
EXAMPLES = 5
_COMMIT_EVERY = 100


@dataclass
class Estimate:
    """Оценка по соседям; ``median`` None — соседей меньше ``MIN_NEIGHBOURS``."""

    grade: str | None
    neighbours: int
    p25: float | None = None
    median: float | None = None
    p75: float | None = None
    neighbour_ids: list[str] = field(default_factory=list)


def percentile(sorted_values: list[float], p: float) -> float:
    """Интерполированный перцентиль, как ``percentile_cont`` в Postgres."""
    pos = (len(sorted_values) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _grade(vacancy: VacancyRow) -> str | None:
    return vacancy.detected_grade if vacancy.detected_grade and vacancy.detected_grade != "unknown" else None


def _neighbours(session: Session, vacancy: VacancyRow, vector: list[float], *, model: str) -> list[tuple[str, float]]:
    """(id, зарплата ₽) ближайших вакансий с зарплатой, ближайшие первыми."""
    # Фильтры (зарплата, грейд, окно) отсекают большую часть кандидатов HNSW: без итеративного
    # обхода индекс отдал бы не больше ef_search строк, и после фильтра соседей бы не хватило.
    session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    e, v = VacancyEmbeddingRow, VacancyRow
    anchor = vacancy.post_date or datetime.now(UTC)
    distance = e.embedding.cosine_distance(query_vector(vector))
    cond = [
        e.model == model,
        v.id != vacancy.id,
        v.is_duplicate.is_(False),
        v.detected_salary_avg_rub.isnot(None),
        v.post_date >= anchor - timedelta(days=WINDOW_DAYS),
        v.post_date <= anchor + timedelta(days=1),
    ]
    grade = _grade(vacancy)
    if grade:
        cond.append(v.detected_grade == grade)
    rows = session.execute(
        select(v.id, v.detected_salary_avg_rub, distance)
        .join(v, v.id == e.vacancy_id)
        .where(*cond)
        .order_by(distance, v.id)
        .limit(NEIGHBOURS)
    ).all()
    # relaxed_order допускает небольшой беспорядок — порядок восстанавливается здесь.
    return [(vid, float(sal)) for vid, sal, _ in sorted(rows, key=lambda r: (r[2], r[0]))]


def estimate(session: Session, vacancy: VacancyRow, vector: list[float], *, model: str) -> Estimate:
    """Оценка зарплаты вакансии по её вектору (сама вакансия в соседи не входит)."""
    found = _neighbours(session, vacancy, vector, model=model)
    out = Estimate(grade=_grade(vacancy), neighbours=len(found), neighbour_ids=[vid for vid, _ in found])
    if len(found) < MIN_NEIGHBOURS:
        return out
    salaries = sorted(s for _, s in found)
    out.p25, out.median, out.p75 = (percentile(salaries, p) for p in (0.25, 0.5, 0.75))
    return out


def _own_vector(session: Session, vacancy_id: str, model: str) -> list[float] | None:
    row = session.get(VacancyEmbeddingRow, vacancy_id)
    return row.embedding if row is not None and row.model == model else None


def is_fresh(
    stored: VacancySalaryEstimateRow, *, model: str, vacancy_updated: datetime, vector_at: datetime | None
) -> bool:
    """Оценка той же модели, после последнего изменения вакансии и её вектора и не старше ``REFRESH_DAYS``."""
    at = stored.created_at
    if stored.model != model or at < datetime.now(UTC) - timedelta(days=REFRESH_DAYS):
        return False
    return at >= vacancy_updated and (vector_at is None or at >= vector_at)


def save(session: Session, vacancy_id: str, est: Estimate, *, model: str) -> None:
    values = {
        "vacancy_id": vacancy_id,
        "model": model,
        "grade": est.grade,
        "neighbours": est.neighbours,
        "p25": est.p25,
        "median": est.median,
        "p75": est.p75,
        "neighbour_ids": est.neighbour_ids,
        "created_at": datetime.now(UTC),
    }
    stmt = pg_insert(VacancySalaryEstimateRow).values(values)
    session.execute(
        stmt.on_conflict_do_update(index_elements=["vacancy_id"], set_={k: stmt.excluded[k] for k in values})
    )


def current_estimate(session: Session, vacancy: VacancyRow, *, model: str) -> Estimate | None:
    """Сохранённая свежая оценка или посчитанная сейчас; None — у вакансии ещё нет вектора ``model``."""
    vec_row = session.get(VacancyEmbeddingRow, vacancy.id)
    if vec_row is None or vec_row.model != model:
        return None
    stored = session.get(VacancySalaryEstimateRow, vacancy.id)
    if stored is not None and is_fresh(
        stored, model=model, vacancy_updated=vacancy.updated_at, vector_at=vec_row.created_at
    ):
        return Estimate(
            grade=stored.grade,
            neighbours=stored.neighbours,
            p25=stored.p25,
            median=stored.median,
            p75=stored.p75,
            neighbour_ids=list(stored.neighbour_ids or []),
        )
    return estimate(session, vacancy, vec_row.embedding, model=model)


def _stale_candidates(session: Session, *, model: str, limit: int) -> list[str]:
    """Вакансии без зарплаты с вектором ``model``, у которых оценки нет или она устарела.

    Сначала активные, затем свежие — как индексация эмбеддингов.
    """
    e, v, s = VacancyEmbeddingRow, VacancyRow, VacancySalaryEstimateRow
    expired = datetime.now(UTC) - timedelta(days=REFRESH_DAYS)
    return list(
        session.scalars(
            select(v.id)
            .join(e, (e.vacancy_id == v.id) & (e.model == model))
            .outerjoin(s, s.vacancy_id == v.id)
            .where(
                v.is_duplicate.is_(False),
                v.detected_salary_avg_rub.is_(None),
                or_(
                    s.vacancy_id.is_(None),
                    s.model != model,
                    s.created_at < expired,
                    s.created_at < v.updated_at,
                    s.created_at < e.created_at,
                ),
            )
            .order_by(v.archived_at.is_not(None), v.post_date.desc().nulls_last(), v.id)
            .limit(limit)
        )
    )


def sweep_estimates(*, model: str, max_items: int = MAX_PER_PASS) -> int:
    """Пересчитать устаревшие оценки (до ``max_items``) и убрать ненужные; вернуть число пересчитанных."""
    with session_scope() as session:
        # Вакансия получила зарплату или стала дублем — оценка ей больше не нужна.
        session.execute(
            delete(VacancySalaryEstimateRow).where(
                VacancySalaryEstimateRow.vacancy_id.in_(
                    select(VacancyRow.id).where(
                        or_(VacancyRow.detected_salary_avg_rub.isnot(None), VacancyRow.is_duplicate.is_(True))
                    )
                )
            )
        )
        ids = _stale_candidates(session, model=model, limit=max_items)
    done = 0
    for start in range(0, len(ids), _COMMIT_EVERY):
        with session_scope() as session:
            for vid in ids[start : start + _COMMIT_EVERY]:
                vacancy = session.get(VacancyRow, vid)
                vector = _own_vector(session, vid, model)
                if vacancy is None or vector is None:
                    continue
                save(session, vid, estimate(session, vacancy, vector, model=model), model=model)
                done += 1
    if done:
        _LOG.info("Оценки зарплаты: пересчитано %s вакансий", done)
    return done


@dataclass
class EvalResult:
    """Проверка на вакансиях с известной зарплатой: оценка без самой вакансии против её зарплаты."""

    checked: int = 0
    estimated: int = 0
    median_ape: float | None = None
    within_iqr: float | None = None
    within_20pct: float | None = None
    by_grade: dict[str, dict[str, float | int]] = field(default_factory=dict)


def _summary(errors: list[float], inside: list[bool]) -> dict[str, float | int]:
    errs = sorted(errors)
    return {
        "n": len(errs),
        "median_ape": round(percentile(errs, 0.5), 3) if errs else 0.0,
        "within_iqr": round(sum(inside) / len(inside), 3) if inside else 0.0,
        "within_20pct": round(sum(e <= 0.2 for e in errs) / len(errs), 3) if errs else 0.0,
    }


def evaluate(session: Session, *, model: str, limit: int | None = None) -> EvalResult:
    """Оценить вакансии с зарплатой так, будто её нет, и сравнить с настоящей (только чтение)."""
    e, v = VacancyEmbeddingRow, VacancyRow
    stmt = (
        select(v, e.embedding)
        .join(e, (e.vacancy_id == v.id) & (e.model == model))
        .where(v.is_duplicate.is_(False), v.detected_salary_avg_rub.isnot(None))
        .order_by(v.post_date.desc().nulls_last(), v.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    result = EvalResult()
    errors: list[float] = []
    inside: list[bool] = []
    per_grade: dict[str, tuple[list[float], list[bool]]] = {}
    for vacancy, vector in session.execute(stmt).all():
        result.checked += 1
        est = estimate(session, vacancy, vector, model=model)
        if est.median is None:
            continue
        actual = float(vacancy.detected_salary_avg_rub)
        err = abs(est.median - actual) / actual
        hit = est.p25 <= actual <= est.p75
        errors.append(err)
        inside.append(hit)
        g = per_grade.setdefault(vacancy.detected_grade or "unknown", ([], []))
        g[0].append(err)
        g[1].append(hit)
    result.estimated = len(errors)
    if errors:
        overall = _summary(errors, inside)
        result.median_ape = overall["median_ape"]
        result.within_iqr = overall["within_iqr"]
        result.within_20pct = overall["within_20pct"]
    result.by_grade = {g: _summary(*vals) for g, vals in sorted(per_grade.items())}
    return result

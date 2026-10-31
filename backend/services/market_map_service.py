"""Карта рынка: вакансии на плоскости по смыслу, кластеры-ниши и место резюме на ней.

Эмбеддинги уникальных вакансий за год (самые свежие ``map_max_points``) сжимаются
PCA до 50 измерений и t-SNE (косинус) до 2D; на плоскости k-means делит их на ниши.
Подпись ниши — без LLM: самая частая роль и технологии, которых в ней заметно больше, чем
по рынку (lift). Рост ниши — доля её вакансий за последние 30 дней против предыдущих 30.
Резюме ставится в среднюю точку ближайших к нему по эмбеддингу вакансий карты.

Расчёт — от секунд до минут CPU на тысячи точек: его делает фоновое обслуживание
(``backend/maintenance.py``: лидер API или ``jobdesk-maintenance-worker``) раз в
``ai.embeddings.map_interval_hours`` или по запросу администратора. Хранится только
последняя карта; двух расчётов одновременно не бывает (advisory lock).
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.orm import Session

from backend.db.models import MarketMapPointRow, MarketMapRow, ResumeRow, VacancyEmbeddingRow, VacancyRow
from backend.db.session import session_scope
from backend.db.vector_type import query_vector
from backend.services import embedding_service

_LOG = logging.getLogger(__name__)

WINDOW_DAYS = 365
MIN_POINTS = 50
PCA_DIMS = 50
# Ниш столько, чтобы в средней было ~130 вакансий, но не меньше 4 и не больше 20.
POINTS_PER_CLUSTER = 130
MIN_CLUSTERS = 4
MAX_CLUSTERS = 20
LABEL_TECHS = 2
# Технология в подписи — у стольких вакансий ниши (доля) и во столько раз чаще, чем по рынку.
LABEL_MIN_SUPPORT = 0.15
LABEL_MIN_LIFT = 1.3
GROWTH_WINDOW_DAYS = 30
# Положение резюме — по стольким ближайшим вакансиям карты.
RESUME_NEIGHBOURS = 15
_LOCK_KEY = 0x6D61_7031  # "map1": один расчёт на все процессы


class MapBusy(RuntimeError):
    """Карту уже считает другой процесс."""


def _cluster_count(n: int) -> int:
    return max(MIN_CLUSTERS, min(MAX_CLUSTERS, n // POINTS_PER_CLUSTER))


def _layout(vectors: list[list[float]]) -> tuple[list[tuple[float, float]], list[int], dict[str, Any]]:
    """PCA → t-SNE → k-means; координаты нормированы в [0, 1] по большей стороне."""
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
    from sklearn.manifold import TSNE

    x = np.asarray(vectors, dtype=np.float32)
    n = len(x)
    dims = min(PCA_DIMS, n - 1, x.shape[1])
    reduced = PCA(n_components=dims, random_state=0).fit_transform(x)
    perplexity = float(min(30, max(5, (n - 1) // 3)))
    y = TSNE(n_components=2, metric="cosine", init="pca", perplexity=perplexity, random_state=0).fit_transform(reduced)
    k = _cluster_count(n)
    labels = KMeans(n_clusters=k, n_init=4, random_state=0).fit_predict(y)
    lo = y.min(axis=0)
    span = float((y.max(axis=0) - lo).max()) or 1.0
    coords = [(float(a), float(b)) for a, b in (y - lo) / span]
    return coords, [int(c) for c in labels], {"pca_dims": dims, "perplexity": perplexity, "clusters": k}


def _label_parts(
    roles: Counter, techs: Counter, size: int, market_tech: Counter, market_n: int
) -> tuple[str, list[str]]:
    """Самая частая роль и технологии ниши, которых в ней заметно больше, чем по рынку (lift)."""
    role = roles.most_common(1)[0][0] if roles else "Разное"
    distinctive = []
    for tech, n in techs.most_common():
        share = n / size
        if share < LABEL_MIN_SUPPORT:
            break
        if share / (market_tech[tech] / market_n) >= LABEL_MIN_LIFT:
            distinctive.append(tech)
    return role, distinctive


def unique_labels(parts: list[tuple[str, list[str]]]) -> list[str]:
    """Подписи ниш: роль и до двух характерных технологий; совпавшие уточняются ещё одной, иначе номером."""

    def label(role: str, techs: list[str], n: int) -> str:
        return f"{role} · {', '.join(techs[:n])}" if techs[:n] else role

    labels = [label(r, t, LABEL_TECHS) for r, t in parts]
    for i, (role, techs) in enumerate(parts):
        if labels.count(labels[i]) > 1 and len(techs) > LABEL_TECHS:
            labels[i] = label(role, techs, LABEL_TECHS + 1)
    repeated = {lab for lab, n in Counter(labels).items() if n > 1}
    seen: Counter = Counter()
    for i, lab in enumerate(labels):
        if lab in repeated:
            seen[lab] += 1
            labels[i] = f"{lab} ({seen[lab]})"
    return labels


def _clusters(rows: list, labels: list[int], coords: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """Подписи, состав, зарплата и рост ниш. ``rows`` — (id, роли, технологии, зарплата ₽, дата поста)."""
    n = len(rows)
    market_tech = Counter(t for r in rows for t in set(r[2] or []))
    anchor = max((r[4] for r in rows if r[4] is not None), default=datetime.now(UTC))
    recent_from = anchor - timedelta(days=GROWTH_WINDOW_DAYS)
    previous_from = recent_from - timedelta(days=GROWTH_WINDOW_DAYS)

    def window(r) -> str | None:
        if r[4] is None or r[4] < previous_from:
            return None
        return "recent" if r[4] > recent_from else "previous"

    totals = Counter(window(r) for r in rows)
    out = []
    for c in sorted(set(labels)):
        idx = [i for i, lab in enumerate(labels) if lab == c]
        members = [rows[i] for i in idx]
        roles = Counter(x for r in members for x in set(r[1] or []))
        techs = Counter(t for r in members for t in set(r[2] or []))
        salaries = [float(r[3]) for r in members if r[3] is not None]
        win = Counter(window(r) for r in members)
        recent_share = win["recent"] / totals["recent"] if totals["recent"] else None
        previous_share = win["previous"] / totals["previous"] if totals["previous"] else None
        size = len(members)
        out.append(
            {
                "id": c,
                "label": "",
                "_parts": _label_parts(roles, techs, size, market_tech, n),
                "size": size,
                "x": sum(coords[i][0] for i in idx) / size,
                "y": sum(coords[i][1] for i in idx) / size,
                "roles": [{"name": k, "share": round(v / size, 3)} for k, v in roles.most_common(3)],
                "technologies": [{"name": k, "share": round(v / size, 3)} for k, v in techs.most_common(6)],
                "salary_median": median(salaries) if len(salaries) >= 5 else None,
                "salary_count": len(salaries),
                "recent": win["recent"],
                "previous": win["previous"],
                "delta_pp": round((recent_share - previous_share) * 100, 1)
                if recent_share is not None and previous_share is not None
                else None,
            }
        )
    for c, lab in zip(out, unique_labels([c.pop("_parts") for c in out]), strict=True):
        c["label"] = lab
    return out


def _eligible(stmt, model: str):
    """Вакансии карты: уникальные, за ``WINDOW_DAYS``, с вектором ``model``."""
    since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)
    v, e = VacancyRow, VacancyEmbeddingRow
    return stmt.join(e, e.vacancy_id == v.id).where(e.model == model, v.is_duplicate.is_(False), v.post_date >= since)


def has_enough_points(session: Session) -> bool:
    """Хватит ли вакансий с эмбеддингами на карту (не меньше ``MIN_POINTS``)."""
    model = embedding_service.current_model()
    found = session.scalar(
        select(func.count()).select_from(_eligible(select(VacancyRow.id), model).limit(MIN_POINTS).subquery())
    )
    return (found or 0) >= MIN_POINTS


def build_market_map(*, max_points: int | None = None) -> MarketMapRow | None:
    """Пересчитать карту. None — мало вакансий с эмбеддингами; ``MapBusy`` — уже считается."""
    ai = embedding_service._ai()
    limit = max_points or ai.embeddings.map_max_points
    started = time.perf_counter()
    with session_scope() as session:
        if not session.scalar(select(func.pg_try_advisory_xact_lock(_LOCK_KEY))):
            raise MapBusy("Карта рынка уже пересчитывается")
        v, e = VacancyRow, VacancyEmbeddingRow
        rows = session.execute(
            _eligible(
                select(
                    v.id,
                    v.detected_specializations,
                    v.technologies,
                    v.detected_salary_avg_rub,
                    v.post_date,
                    e.embedding,
                ),
                ai.embeddings.model,
            )
            .order_by(v.post_date.desc(), v.id)
            .limit(limit)
        ).all()
        if len(rows) < MIN_POINTS:
            _LOG.info("Карта рынка: вакансий с эмбеддингами %s < %s — не считаю", len(rows), MIN_POINTS)
            return None
        coords, labels, params = _layout([r[5] for r in rows])
        meta = [r[:5] for r in rows]
        clusters = _clusters(meta, labels, coords)
        params.update(window_days=WINDOW_DAYS, seconds=round(time.perf_counter() - started, 1))
        row = MarketMapRow(model=ai.embeddings.model, points=len(rows), params=params, clusters=clusters)
        session.add(row)
        session.flush()
        session.execute(
            insert(MarketMapPointRow),
            [
                {"map_id": row.id, "vacancy_id": r[0], "x": xy[0], "y": xy[1], "cluster": lab}
                for r, xy, lab in zip(rows, coords, labels, strict=True)
            ],
        )
        session.execute(delete(MarketMapRow).where(MarketMapRow.id != row.id))
        _LOG.info("Карта рынка: %s вакансий, %s ниш за %s с", len(rows), len(clusters), params["seconds"])
        session.expunge(row)
        return row


def latest_map(session: Session) -> MarketMapRow | None:
    return session.scalar(select(MarketMapRow).order_by(MarketMapRow.id.desc()).limit(1))


def map_is_due(session: Session, interval_hours: int) -> bool:
    """Пора пересчитать: карты нет, она старше интервала (0 — только вручную) или посчитана
    прежней моделью эмбеддингов, а каталог уже переиндексирован целиком.
    """
    if interval_hours <= 0:
        return False
    m = latest_map(session)
    if m is None or datetime.now(UTC) - m.created_at >= timedelta(hours=interval_hours):
        return True
    if m.model != embedding_service.current_model():
        cov = embedding_service.coverage(session)
        return cov.embedded >= cov.vacancies
    return False


def map_points(session: Session, map_id: int) -> list:
    """Точки карты с полями для подсказки: (id, x, y, кластер, роль, грейд, зарплата ₽, компания, архив)."""
    p, v = MarketMapPointRow, VacancyRow
    return session.execute(
        select(
            p.vacancy_id,
            p.x,
            p.y,
            p.cluster,
            v.detected_specializations,
            v.detected_grade,
            v.detected_salary_avg_rub,
            v.company,
            v.archived_at,
        )
        .join(v, v.id == p.vacancy_id)
        .where(p.map_id == map_id)
        .order_by(p.vacancy_id)
    ).all()


def resume_position(session: Session, resume: ResumeRow, m: MarketMapRow) -> dict[str, Any] | None:
    """Резюме на карте: взвешенная средняя ближайших по эмбеддингу вакансий карты.

    ``EmbeddingsUnavailable`` — эмбеддинги выключены, недоступны или карта посчитана другой моделью.
    """
    if m.model != embedding_service.current_model():
        raise embedding_service.EmbeddingsUnavailable("карта посчитана прежней моделью эмбеддингов, ждёт пересчёта")
    vector = embedding_service.resume_embedding(session, resume)
    p, e = MarketMapPointRow, VacancyEmbeddingRow
    distance = e.embedding.cosine_distance(query_vector(vector))
    session.execute(text(f"SET LOCAL hnsw.ef_search = {RESUME_NEIGHBOURS * 10}"))
    rows = session.execute(
        select(p.vacancy_id, p.x, p.y, p.cluster, distance)
        .join(e, e.vacancy_id == p.vacancy_id)
        .where(p.map_id == m.id, e.model == m.model)
        .order_by(distance, p.vacancy_id)
        .limit(RESUME_NEIGHBOURS)
    ).all()
    if not rows:
        return None
    weights = [1.0 / (float(d) + 1e-3) for *_, d in rows]
    total = sum(weights)
    cluster = Counter(r[3] for r in rows).most_common(1)[0][0]
    return {
        "resume_id": resume.id,
        "x": sum(w * r[1] for w, r in zip(weights, rows, strict=True)) / total,
        "y": sum(w * r[2] for w, r in zip(weights, rows, strict=True)) / total,
        "cluster": cluster,
        "nearest": [r[0] for r in rows],
    }

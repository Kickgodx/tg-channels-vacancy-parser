"""Граф технологий: какие технологии просят вместе.

Узлы — самые частые технологии под фильтрами, рёбра — пары, которые встречаются в одной
вакансии. Сила связи — lift: во сколько раз пара встречается чаще, чем если бы технологии
появлялись независимо (``n_ab · N / (n_a · n_b)``). Частота пары сама по себе не годится:
Python и SQL встречаются вместе просто потому, что обе частые. Свой фильтр по технологии не
применяется, как у остальной аналитики технологий: под фильтром «Kafka» Kafka была бы у всех.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import Text, cast, func, lateral, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from backend.db.technology_analytics import _top_technologies, _without_own_filter
from backend.db.vacancy_filters import apply_vacancy_filters
from backend.db.vacancy_metrics import _spec_lateral, _tech_lateral, _vacancies_alias
from backend.schemas import TechnologyGraph, TechnologyGraphEdge, TechnologyGraphNode, VacancyQuery

DEFAULT_NODES = 40
MAX_NODES = 80
# Пара реже — не ребро: на двух вакансиях lift ничего не значит.
MIN_PAIR_COUNT = 3


def _pair_lateral(v, name: str):
    q = select(func.jsonb_array_elements_text(v.c.technologies).label("tech")).correlate(v)
    return lateral(q).alias(name)


def technology_graph(session: Session, query: VacancyQuery, *, nodes: int = DEFAULT_NODES) -> TechnologyGraph:
    query = _without_own_filter(query)
    v = _vacancies_alias()
    total = int(session.scalar(apply_vacancy_filters(select(func.count(v.c.id)), query, table=v)) or 0)
    top = _top_technologies(session, query, limit=nodes, salaried=False)
    if not top:
        return TechnologyGraph(total=total, min_pair_count=MIN_PAIR_COUNT)
    in_top = cast(top, ARRAY(Text))

    lat = _tech_lateral(v)
    counts = dict(
        session.execute(
            apply_vacancy_filters(
                select(lat.c.tech, func.count(v.c.id))
                .select_from(v.join(lat, true()))
                .where(lat.c.tech == func.any(in_top))
                .group_by(lat.c.tech),
                query,
                table=v,
            )
        ).all()
    )

    # Пары внутри вакансии: по технологиям самой вакансии, а не перебором всех пар на всех вакансиях.
    a, b = _pair_lateral(v, "tech_a"), _pair_lateral(v, "tech_b")
    pairs = session.execute(
        apply_vacancy_filters(
            select(a.c.tech, b.c.tech, func.count(func.distinct(v.c.id)))
            .select_from(v.join(a, true()).join(b, true()))
            .where(a.c.tech < b.c.tech, a.c.tech == func.any(in_top), b.c.tech == func.any(in_top))
            .group_by(a.c.tech, b.c.tech)
            .having(func.count(func.distinct(v.c.id)) >= MIN_PAIR_COUNT),
            query,
            table=v,
        )
    ).all()

    # Роль, с которой технология встречается чаще всего, — для цвета узла.
    t, s = _tech_lateral(v), _spec_lateral(v)
    roles: dict[str, Counter] = defaultdict(Counter)
    for tech, spec, n in session.execute(
        apply_vacancy_filters(
            select(t.c.tech, s.c.spec, func.count(v.c.id))
            .select_from(v.join(t, true()).join(s, true()))
            .where(t.c.tech == func.any(in_top))
            .group_by(t.c.tech, s.c.spec),
            query,
            table=v,
        )
    ).all():
        roles[tech][spec] = int(n)

    out_nodes = []
    for tech in top:
        n = int(counts.get(tech, 0))
        role, role_n = roles[tech].most_common(1)[0] if roles[tech] else (None, 0)
        out_nodes.append(
            TechnologyGraphNode(
                name=tech,
                count=n,
                share=n / total if total else 0.0,
                role=role,
                role_share=role_n / n if n else 0.0,
            )
        )
    edges = []
    for x, y, n_xy in pairs:
        n_x, n_y = counts.get(x, 0), counts.get(y, 0)
        if not (n_x and n_y):
            continue
        edges.append(TechnologyGraphEdge(source=x, target=y, count=int(n_xy), lift=n_xy * total / (n_x * n_y)))
    edges.sort(key=lambda e: (-e.lift, -e.count, e.source, e.target))
    return TechnologyGraph(total=total, nodes=out_nodes, edges=edges, min_pair_count=MIN_PAIR_COUNT)

"""«Что если выучить X»: как технология, которой нет в резюме, меняет его положение на рынке.

Сегмент рынка — ``PEERS`` вакансий, ближайших к резюме по эмбеддингу за год (как у «Резюме и
рынок»). Вакансия «подходит», если резюме покрывает не меньше ``FIT_THRESHOLD`` её технологий.
Для каждой недостающей технологии: сколько вакансий сегмента её требуют, сколько станут
подходящими, насколько вырастет среднее покрытие и какая зарплатная премия у неё на рынке роли
(медиана с технологией против медианы без неё внутри грейда — как во вкладке аналитики).
LLM не нужен. Сочетания технологий UI считает сам по ``peer_gaps``.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy.orm import Session

from backend.db.models import ResumeRow
from backend.db.technology_analytics import technology_premiums
from backend.db.vacancy_metrics import _prepare_fx
from backend.schemas import SkillWhatIfOption, SkillWhatIfOut, SkillWhatIfPeer, VacancyQuery
from backend.services import embedding_service
from backend.services.resume_market_service import WINDOW_DAYS, nearest_vacancies, resume_technology_check

PEERS = 200
# Вакансия подходит, если резюме покрывает не меньше такой доли её технологий.
FIT_THRESHOLD = 0.7
# Технология-кандидат — у стольких вакансий сегмента (и не реже MIN_DEMAND_SHARE).
MIN_DEMAND = 5
MIN_DEMAND_SHARE = 0.03
CANDIDATES_TOP = 30
OPTIONS_TOP = 12
# Медиана зарплаты вакансий сегмента с технологией — от стольких вакансий.
MIN_SALARY = 5


# «Написание автотестов», «Интеграционное тестирование» — практики, а не технологии, которые учат.
_PRACTICE_RE = re.compile(r"^[а-яё\-]+(?:\s+[а-яё\-]+)+$", re.I)


def is_learnable(tech: str) -> bool:
    return not _PRACTICE_RE.match(tech.strip())


def _fits(covered: int, total: int) -> bool:
    return total > 0 and covered / total >= FIT_THRESHOLD - 1e-9


def skill_what_if(session: Session, resume: ResumeRow) -> SkillWhatIfOut:
    """``EmbeddingsUnavailable`` — эмбеддинги выключены или недоступны."""
    vector = embedding_service.resume_embedding(session, resume)
    model = embedding_service._ai().embeddings.model
    now = datetime.now(UTC)
    rows = nearest_vacancies(session, vector, model=model, now=now, limit=PEERS)
    resume_techs, in_resume = resume_technology_check(session, resume)
    out = SkillWhatIfOut(
        resume_id=resume.id,
        window_days=WINDOW_DAYS,
        peers=len(rows),
        fit_threshold=FIT_THRESHOLD,
        resume_technologies=resume_techs,
        salary_display=_prepare_fx(session),
    )
    peers = []  # (всего технологий, покрыто, недостающие, зарплата)
    for _, _, techs, salary in rows:
        ts = sorted(set(techs or []))
        if not ts:
            continue
        missing = [t for t in ts if not in_resume(t)]
        peers.append((len(ts), len(ts) - len(missing), missing, salary))
    if not peers:
        return out

    roles = Counter(r for _, specs, *_ in rows for r in set(specs or []))
    grades = Counter(g for g, *_ in rows if g and g != "unknown")
    role = roles.most_common(1)[0][0] if roles else None
    grade = grades.most_common(1)[0][0] if grades else None

    base_fit = sum(_fits(c, n) for n, c, _, _ in peers)
    base_cov = sum(c / n for n, c, _, _ in peers) / len(peers)
    demand = Counter(t for _, _, missing, _ in peers for t in missing)
    min_demand = max(MIN_DEMAND, int(MIN_DEMAND_SHARE * len(peers) + 0.999))
    candidates = [t for t, k in demand.most_common() if k >= min_demand and is_learnable(t)][:CANDIDATES_TOP]

    premiums = {}
    if candidates:
        query = VacancyQuery(
            specializations=[role] if role else [],
            include_archived=True,
            date_from=(now - timedelta(days=WINDOW_DAYS)).date(),
        )
        premiums = {p.technology: p for p in technology_premiums(session, query, candidates)}

    options: list[SkillWhatIfOption] = []
    for tech in candidates:
        new_fit = sum(1 for n, c, missing, _ in peers if tech in missing and not _fits(c, n) and _fits(c + 1, n))
        gain = sum(1 / n for n, _, missing, _ in peers if tech in missing) / len(peers)
        salaries = [float(s) for _, _, missing, s in peers if s is not None and tech in missing]
        premium, scope = None, None
        p = premiums.get(tech)
        if p is not None:
            g = next((s for s in p.by_grade if s.grade == grade and s.compared), None)
            if g is not None and g.with_median and g.without_median:
                premium, scope = g.with_median / g.without_median - 1, "grade"
            elif p.premium is not None:
                premium, scope = p.premium, "role"
        options.append(
            SkillWhatIfOption(
                technology=tech,
                demand=demand[tech],
                demand_share=demand[tech] / len(peers),
                new_fit=new_fit,
                coverage_gain=gain,
                premium=premium,
                premium_scope=scope,
                median_with=median(salaries) if len(salaries) >= MIN_SALARY else None,
            )
        )
    options.sort(key=lambda o: (-o.new_fit, -o.demand, o.technology))
    options = options[:OPTIONS_TOP]
    keep = {o.technology for o in options}
    return out.model_copy(
        update={
            "with_technologies": len(peers),
            "role": role,
            "grade": grade,
            "baseline_fit": base_fit,
            "baseline_coverage": base_cov,
            "options": options,
            "peer_gaps": [
                SkillWhatIfPeer(total=n, covered=c, missing=[t for t in missing if t in keep])
                for n, c, missing, _ in peers
            ],
        }
    )

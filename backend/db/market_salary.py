"""Рыночная вилка для вакансии: квантили зарплаты похожих вакансий с лестницей критериев.

Похожие — вакансии с рублёвой зарплатой (включая архивные: это история рынка), опубликованные
за год до вакансии. Критерии ослабляются, пока не наберётся ``MIN_COMPARABLE``: грейд + роль +
технология → грейд + роль → грейд + технология → роль.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from backend.db.models import VacancyRow
from backend.db.vacancy_metrics import _prepare_fx
from backend.schemas import MarketSalaryOut

MIN_COMPARABLE = 8
WINDOW_DAYS = 365

# (имя ступени, нужен грейд, нужна роль, нужна технология) — от строгой к мягкой.
LEVELS: list[tuple[str, bool, bool, bool]] = [
    ("grade_role_tech", True, True, True),
    ("grade_role", True, True, False),
    ("grade_tech", True, False, True),
    ("role", False, True, False),
]


def _text_array(values: list[str]):
    return cast(values, ARRAY(Text))


def market_salary(session: Session, vacancy: VacancyRow) -> MarketSalaryOut:
    salary_display = _prepare_fx(session)
    grade = vacancy.detected_grade if vacancy.detected_grade and vacancy.detected_grade != "unknown" else None
    roles = list(vacancy.detected_specializations or [])
    techs = list(vacancy.technologies or [])
    anchor = vacancy.post_date or datetime.now(UTC)
    since = anchor - timedelta(days=WINDOW_DAYS)
    own = vacancy.detected_salary_avg_rub

    sal = VacancyRow.detected_salary_avg_rub
    base = [
        sal.isnot(None),
        VacancyRow.is_duplicate.is_(False),
        VacancyRow.id != vacancy.id,
        VacancyRow.post_date >= since,
        VacancyRow.post_date <= anchor + timedelta(days=1),
    ]
    out = MarketSalaryOut(
        vacancy_id=vacancy.id, window_days=WINDOW_DAYS, vacancy_salary=own, salary_display=salary_display
    )
    for level, need_grade, need_role, need_tech in LEVELS:
        if (need_grade and not grade) or (need_role and not roles) or (need_tech and not techs):
            continue
        cond = list(base)
        if need_grade:
            cond.append(VacancyRow.detected_grade == grade)
        if need_role:
            cond.append(VacancyRow.detected_specializations.has_any(_text_array(roles)))
        if need_tech:
            cond.append(VacancyRow.technologies.has_any(_text_array(techs)))
        q = [cast(func.percentile_cont(p).within_group(sal), Float) for p in (0.1, 0.25, 0.5, 0.75, 0.9)]
        below = func.count().filter(sal <= own) if own is not None else None
        cols = [func.count(), *q] + ([below] if below is not None else [])
        row = session.execute(select(*cols).where(*cond)).one()
        n = int(row[0])
        if n < MIN_COMPARABLE:
            continue
        p10, p25, med, p75, p90 = row[1:6]
        return out.model_copy(
            update={
                "level": level,
                "grade": grade if need_grade else None,
                "specializations": roles if need_role else [],
                "technologies": techs if need_tech else [],
                "count": n,
                "p10": p10,
                "p25": p25,
                "median": med,
                "p75": p75,
                "p90": p90,
                "percentile": (int(row[6]) / n) if below is not None else None,
            }
        )
    return out

"""Сводка недостающих навыков резюме: оценки из БД + словарь и спрос технологий каталога."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import VacancyMatchRow
from backend.db.vacancy_metrics import technology_counts
from backend.schemas import SkillGapItem, SkillGapsOut, VacancyQuery
from backend.settings import get_settings
from tg_jobs_parser.analytics.skill_gaps import MatchSkills, SkillGap, TechnologyMatcher, summarize_skill_gaps
from tg_jobs_parser.config.loader import load_config

# Технология каталога попадает в словарь, если она есть хотя бы у стольких активных вакансий:
# редкие имена из ответов LLM — шум («Moon», «LAVA»), они уходят в прочие навыки.
CATALOG_TECH_MIN_VACANCIES = 5
# Верхняя граница выборки технологий каталога (в базе их сотни).
_CATALOG_TECH_LIMIT = 10_000


def _item(gap: SkillGap, evaluated: int, market: dict[str, int] | None) -> SkillGapItem:
    return SkillGapItem(
        name=gap.name,
        vacancies=gap.vacancies,
        critical=gap.critical,
        important=gap.important,
        secondary=gap.secondary,
        share=gap.vacancies / evaluated if evaluated else 0.0,
        vacancy_ids=list(gap.vacancy_ids),
        market_vacancies=None if market is None else market.get(gap.name.lower(), 0),
    )


def _catalog_technologies(session: Session) -> dict[str, tuple[str, int]]:
    """Технологии каталога без учёта регистра: ключ lower → (самое частое написание, сумма)."""
    out: dict[str, tuple[str, int]] = {}
    for name, n in technology_counts(session, VacancyQuery(), limit=_CATALOG_TECH_LIMIT).items():
        key = name.lower()
        prev = out.get(key)
        if prev is None:
            out[key] = (name, n)
        else:
            out[key] = (prev[0] if prev[1] >= n else name, prev[1] + n)
    return out


def _role_names(cfg) -> set[str]:
    """Роли и их синонимы: LLM иногда кладёт их в технологии («Backend», «QA»), но это не навык."""
    out: set[str] = set()
    for name, synonyms in cfg.merged_specializations().items():
        out.add(name.lower())
        out.update(str(s).lower() for s in synonyms)
    return out


def skill_gaps(session: Session, *, resume_id: int) -> SkillGapsOut:
    rows = session.execute(
        select(VacancyMatchRow.vacancy_id, VacancyMatchRow.missing_skills)
        .where(VacancyMatchRow.resume_id == resume_id)
        .order_by(VacancyMatchRow.created_at.desc(), VacancyMatchRow.vacancy_id)
    ).all()
    if not rows:
        return SkillGapsOut(resume_id=resume_id, evaluated=0)

    catalog = _catalog_technologies(session)
    cfg = load_config(get_settings().config_path)
    roles = _role_names(cfg)
    names = cfg.merged_technologies() + [
        name for key, (name, n) in catalog.items() if n >= CATALOG_TECH_MIN_VACANCIES and key not in roles
    ]
    summary = summarize_skill_gaps(
        [MatchSkills(vacancy_id=vid, missing_skills=skills or []) for vid, skills in rows],
        TechnologyMatcher(names),
    )
    market = {key: n for key, (_, n) in catalog.items()}
    return SkillGapsOut(
        resume_id=resume_id,
        evaluated=summary.evaluated,
        technologies=[_item(g, summary.evaluated, market) for g in summary.technologies],
        other=[_item(g, summary.evaluated, None) for g in summary.other],
    )

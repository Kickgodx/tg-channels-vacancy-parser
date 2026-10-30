"""Разовая чистка сохранённых вакансий по правилам, которые появились позже записи.

- Посты соискателей и подборки вакансий (``is_not_vacancy_post``) удаляются: это не вакансии,
  а одна «вакансия» подборки засчитывалась в 5–12 ролей сразу. Удаление каскадом убирает и
  оценки, письма, эмбеддинги и точки карты рынка.
- Технологии проходят ``clean_technology_names``: практики, навыки и фразы из key_skills HH и
  ответа LLM убираются, склеенные списки делятся.

Новые вакансии те же правила получают при записи (детектор, HH-маппер, merge ответа LLM).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.db.models import VacancyRow
from jobdesk.config.schema import clean_technology_names
from jobdesk.extraction.vacancy_detector import is_not_vacancy_post

_BATCH = 1000


@dataclass
class CleanupPlan:
    delete_ids: list[str] = field(default_factory=list)
    delete_previews: list[str] = field(default_factory=list)
    technology_updates: dict[str, list[str]] = field(default_factory=dict)
    removed: Counter = field(default_factory=Counter)
    added: Counter = field(default_factory=Counter)


def plan_cleanup(session: Session) -> CleanupPlan:
    """Что удалить и какие технологии переписать (включая дубли и архив)."""
    plan = CleanupPlan()
    v = VacancyRow
    rows = session.execute(select(v.id, v.cleaned_text, v.raw_text, v.technologies).order_by(v.id)).yield_per(_BATCH)
    for vid, cleaned, raw, techs in rows:
        text = cleaned or raw or ""
        if is_not_vacancy_post(text):
            plan.delete_ids.append(vid)
            plan.delete_previews.append(" ".join(text.split())[:100])
            continue
        old = list(techs or [])
        new = clean_technology_names(old)
        if new != old:
            plan.technology_updates[vid] = new
            kept = {t.lower() for t in new}
            was = {t.lower() for t in old}
            plan.removed.update(t for t in old if t.lower() not in kept)
            plan.added.update(t for t in new if t.lower() not in was)
    return plan


def apply_cleanup(session: Session, plan: CleanupPlan) -> tuple[int, int]:
    """(удалено вакансий, переписано технологий)."""
    deleted = 0
    for i in range(0, len(plan.delete_ids), _BATCH):
        chunk = plan.delete_ids[i : i + _BATCH]
        deleted += session.execute(delete(VacancyRow).where(VacancyRow.id.in_(chunk))).rowcount
    items = list(plan.technology_updates.items())
    for i in range(0, len(items), _BATCH):
        session.execute(
            update(VacancyRow),
            [{"id": vid, "technologies": techs} for vid, techs in items[i : i + _BATCH]],
        )
    return deleted, len(items)

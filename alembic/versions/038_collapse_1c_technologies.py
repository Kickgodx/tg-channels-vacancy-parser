"""Варианты 1С в технологиях сводятся к одной технологии «1С».

LLM пишет технологии свободно: «1С:Предприятие 8.3», «1C:ERP», «1С КА», «язык запросов 1С».
В фильтре это были отдельные пункты. Любое имя с 1С/1C как отдельным токеном → «1С»,
«1С-Битрикс» / «Битрикс24» → «Bitrix» (отдельный продукт); повторы схлопываются, порядок
сохраняется. Правило — копия ``_technology_family`` из ``tg_jobs_parser/config/schema.py``
на момент миграции. Downgrade не разделяет обратно.

Revision ID: 038_collapse_1c_technologies
Revises: 037_separate_technologies
Create Date: 2026-10-20
"""

import json
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "038_collapse_1c_technologies"
down_revision: Union[str, None] = "037_separate_technologies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ONE_C_RE = re.compile(r"(?<![0-9a-zа-яё])1[сc](?![0-9a-zа-яё])", re.IGNORECASE)
_BITRIX_RE = re.compile(r"битрикс|bitrix", re.IGNORECASE)
# Отбор строк в SQL — то же правило в синтаксисе Postgres (без lookbehind).
_SQL_MATCH = "t ~* '(^|[^0-9a-zа-яё])1[сc]([^0-9a-zа-яё]|$)' OR t ~* 'битрикс|bitrix'"


def _collapse(techs: list) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in techs:
        name = " ".join(str(raw).split())
        if not name:
            continue
        if _BITRIX_RE.search(name):
            name = "Bitrix"
        elif _ONE_C_RE.search(name):
            name = "1С"
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, technologies FROM vacancies v WHERE EXISTS "
            f"(SELECT 1 FROM jsonb_array_elements_text(v.technologies) t WHERE {_SQL_MATCH})"
        )
    ).all()
    updates = []
    for vid, techs in rows:
        techs = json.loads(techs) if isinstance(techs, str) else list(techs or [])
        new = _collapse(techs)
        if new != techs:
            updates.append({"id": vid, "t": json.dumps(new, ensure_ascii=False)})
    if updates:
        bind.execute(sa.text("UPDATE vacancies SET technologies = CAST(:t AS jsonb) WHERE id = :id"), updates)


def downgrade() -> None:
    pass

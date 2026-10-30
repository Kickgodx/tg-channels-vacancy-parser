"""Нормализованный ключ компании в vacancies (company_key) для профиля компании.

«ООО Ромашка», «Ромашка», «РОМАШКА LLC» → «ромашка». Правило — копия
``jobdesk.utils.company.company_key`` на момент миграции; дальше ключ пишет
``vacancy_to_row`` при каждой записи вакансии.

Revision ID: 040_vacancies_company_key
Revises: 039_merge_middle_plus
Create Date: 2026-10-27
"""

import re
import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "040_vacancies_company_key"
down_revision: Union[str, None] = "039_merge_middle_plus"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGAL_RE = re.compile(r"(?<![0-9a-zа-я])(?:ооо|оао|зао|пао|нао|ао|ип|гк|llc|ltd|inc|gmbh|llp|plc|corp)(?![0-9a-zа-я])")
_NON_WORD_RE = re.compile(r"[^0-9a-zа-я]+")


def _company_key(name):
    if not name:
        return None
    t = unicodedata.normalize("NFKC", name).lower().replace("ё", "е")
    t = _NON_WORD_RE.sub(" ", t)
    t = _LEGAL_RE.sub(" ", t)
    t = " ".join(t.split())
    return t[:256] or None


def upgrade() -> None:
    op.add_column("vacancies", sa.Column("company_key", sa.String(256), nullable=True))
    op.create_index("ix_vacancies_company_key", "vacancies", ["company_key"])
    bind = op.get_bind()
    names = [r[0] for r in bind.execute(sa.text("SELECT DISTINCT company FROM vacancies WHERE company IS NOT NULL"))]
    updates = [{"c": n, "k": _company_key(n)} for n in names]
    updates = [u for u in updates if u["k"]]
    if updates:
        bind.execute(sa.text("UPDATE vacancies SET company_key = :k WHERE company = :c"), updates)


def downgrade() -> None:
    op.drop_index("ix_vacancies_company_key", table_name="vacancies")
    op.drop_column("vacancies", "company_key")

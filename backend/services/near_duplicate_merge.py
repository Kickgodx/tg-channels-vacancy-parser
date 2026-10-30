"""Разовое склеивание почти-дублей, накопленных до дедупа между прогонами (``db merge-duplicates``).

Вакансии Telegram проходят тот же дедуп по полному тексту, что и прогон, в порядке даты
поста: более поздняя копия становится дублем ранней. Строка копии остаётся (на неё могут
ссылаться оценки и письма), но помечается ``is_duplicate`` со ссылкой ``duplicate_of`` и
пропадает из списков и аналитики. Её появления переходят к исходной вакансии; исходная, как
при перепосте, получает более позднюю дату поста, возврат из архива и привязку к прогонам копии.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func, literal, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from backend.db.models import ParseRunVacancyRow, VacancyRow, VacancySightingRow
from backend.db.vacancy_mapper import row_to_vacancy
from backend.db.vacancy_repository import NON_TELEGRAM_CHANNELS, invalidate_vacancy_count_cache
from jobdesk.config.schema import AppConfig
from jobdesk.deduplication.changes import vacancy_changes
from jobdesk.deduplication.deduplicator import VacancyDeduplicator

# Появления с тем же текстом, что у вакансии: точный повтор в прогоне или текст, уже бывший в БД.
EXACT_REASONS = ("exact_raw_hash", "normalized_hash", "seen_before")


@dataclass(frozen=True)
class NearDuplicate:
    duplicate_id: str
    canonical_id: str
    reason: str


def find_near_duplicates(session: Session, config: AppConfig) -> list[NearDuplicate]:
    """Пары «поздняя копия → исходная» среди уникальных вакансий Telegram (только правило полного текста).

    Составной ключ прогона здесь не применяется: он сравнивает и зарплату, и рассчитан на один
    прогон, а по всей истории склеил бы больше, чем дедуп прогонов.
    """
    # Исходная — раньше всех опубликованная: по первому появлению, а не по post_date (её сдвигают перепосты).
    first_seen = (
        select(func.min(VacancySightingRow.post_date))
        .where(VacancySightingRow.vacancy_id == VacancyRow.id)
        .scalar_subquery()
    )
    rows = session.scalars(
        select(VacancyRow)
        .where(VacancyRow.is_duplicate.is_(False), VacancyRow.source_channel.not_in(NON_TELEGRAM_CHANNELS))
        .order_by(func.coalesce(first_seen, VacancyRow.post_date).asc().nulls_first(), VacancyRow.id)
    ).all()
    cfg = config.model_copy(
        update={"parser": config.parser.model_copy(update={"vacancy_fuzzy_duplicate_threshold": 101})}
    )
    dedup = VacancyDeduplicator(cfg)
    dedup.add(row_to_vacancy(r) for r in rows)
    return [
        NearDuplicate(duplicate_id=d.id, canonical_id=d.duplicate_of, reason=d.duplicate_reason or "duplicate")
        for d in dedup.duplicates
        if d.duplicate_of and d.duplicate_of != d.id
    ]


MAX_PASSES = 10


def merge_all(session: Session, config: AppConfig) -> tuple[int, int]:
    """Склеивать проходами, пока находятся пары: склеенная исходная получает дату последней копии,
    и в окно ``cross_run_dedup_days`` попадают её более поздние перепосты. Возвращает (копий, проходов)."""
    total = passes = 0
    while passes < MAX_PASSES:
        pairs = find_near_duplicates(session, config)
        if not pairs:
            break
        passes += 1
        total += apply_merges(session, pairs)
    flatten_hidden_copies(session)
    return total, passes


def flatten_hidden_copies(session: Session) -> int:
    """Скрытые копии ссылаются на корневую исходную, их изменения — от первой публикации.

    Склейка проходами даёт цепочки: копия → промежуточная копия → исходная. Идемпотентно.
    Возвращает число перевешенных ссылок.
    """
    hidden = {
        r.id: r
        for r in session.scalars(
            select(VacancyRow).where(VacancyRow.is_duplicate.is_(True), VacancyRow.duplicate_of.is_not(None))
        )
    }
    moved = 0
    for row in hidden.values():
        root_id, seen = row.duplicate_of, {row.id}
        while root_id in hidden and root_id not in seen:
            seen.add(root_id)
            root_id = hidden[root_id].duplicate_of
        root = session.get(VacancyRow, root_id)
        if root is None:
            continue
        if row.duplicate_of != root_id:
            row.duplicate_of = root_id
            moved += 1
        session.execute(
            update(VacancySightingRow)
            .where(VacancySightingRow.source_post_key == row.source_post_key)
            .values(vacancy_id=root_id, changes=vacancy_changes(root, row) or None)
        )
    session.flush()
    return moved


def apply_merges(session: Session, pairs: list[NearDuplicate]) -> int:
    """Пометить копии дублями и перенести их появления и прогоны на исходные. Возвращает число копий."""
    merged = 0
    v = VacancyRow.__table__
    for p in pairs:
        dup = session.get(VacancyRow, p.duplicate_id)
        if dup is None or dup.is_duplicate:
            continue
        canonical = session.get(VacancyRow, p.canonical_id)
        dup.is_duplicate = True
        dup.duplicate_reason = p.reason
        dup.duplicate_of = p.canonical_id
        if canonical is not None:
            # Посты с текстом копии (сам пост и его точные повторы) — перепосты исходной с правками.
            session.execute(
                update(VacancySightingRow)
                .where(
                    VacancySightingRow.vacancy_id == p.duplicate_id,
                    or_(
                        VacancySightingRow.source_post_key == dup.source_post_key,
                        VacancySightingRow.duplicate_reason.in_(EXACT_REASONS),
                    ),
                )
                .values(changes=vacancy_changes(canonical, dup) or None, duplicate_reason=p.reason[:64])
            )
        session.execute(
            update(VacancySightingRow)
            .where(VacancySightingRow.vacancy_id == p.duplicate_id)
            .values(
                vacancy_id=p.canonical_id,
                is_duplicate=True,
                duplicate_reason=func.coalesce(VacancySightingRow.duplicate_reason, p.reason[:64]),
            )
        )
        values = {"post_date": func.coalesce(func.greatest(v.c.post_date, dup.post_date), v.c.post_date)}
        if dup.post_date is not None and dup.archived_at is None:
            # Активная копия новее исходной — исходная снова активна, как при перепосте.
            values["archived_at"] = case((v.c.post_date < dup.post_date, null()), else_=v.c.archived_at)
        session.execute(update(v).where(v.c.id == p.canonical_id).values(**values))
        runs = aliased(ParseRunVacancyRow)
        session.execute(
            insert(ParseRunVacancyRow)
            .from_select(
                ["parse_run_id", "vacancy_id"],
                select(runs.parse_run_id, literal(p.canonical_id, VacancyRow.id.type)).where(
                    runs.vacancy_id == p.duplicate_id
                ),
            )
            .on_conflict_do_nothing()
        )
        merged += 1
    session.flush()
    if merged:
        invalidate_vacancy_count_cache()
    return merged

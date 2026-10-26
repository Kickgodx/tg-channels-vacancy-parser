"""Профиль компании и флаги вакансии — только правила по данным каталога, без LLM."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Float, cast, func, select, true, update
from sqlalchemy.orm import Session

from backend.db.models import VacancyRow, VacancySightingRow
from backend.db.vacancy_metrics import _prepare_fx, _spec_lateral, _tech_lateral, _vacancies_alias
from backend.schemas import (
    CompanyProfile,
    CompanyProfileVacancy,
    NamedCount,
    VacancyFlag,
    VacancyFlags,
)
from tg_jobs_parser.utils.company import company_key

# Флаги вакансии.
REPOST_MIN_DAYS = 3  # один текст публиковали в столько разных дней
LONG_OPEN_DAYS = 21  # между первой и последней публикацией текста
FREQUENT_ROLE_MIN = 5  # вакансий компании с той же ролью ...
FREQUENT_ROLE_WINDOW_DAYS = 90  # ... за столько дней до поста
# Профиль.
PROFILE_TOP = 10
PROFILE_RECENT = 20


def backfill_company_keys(session: Session) -> int:
    """Идемпотентно проставить ``company_key`` строкам без него (записанным до миграции 040
    или процессом со старым кодом)."""
    names = session.scalars(
        select(VacancyRow.company).where(VacancyRow.company.isnot(None), VacancyRow.company_key.is_(None)).distinct()
    ).all()
    updated = 0
    for name in names:
        key = company_key(name)
        if key is None:
            continue
        result = session.execute(
            update(VacancyRow)
            .where(VacancyRow.company == name, VacancyRow.company_key.is_(None))
            .values(company_key=key)
        )
        updated += int(result.rowcount or 0)
    return updated


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова для числа: 1 день, 2 дня, 5 дней (21 день, 11 дней)."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _unique(v):
    return v.c.is_duplicate.is_(False)


def _counts(session: Session, stmt) -> list[NamedCount]:
    return [NamedCount(name=str(n), count=int(c)) for n, c in session.execute(stmt).all() if n]


def company_profile(session: Session, name: str) -> CompanyProfile | None:
    key = company_key(name)
    if key is None:
        return None
    v = _vacancies_alias()
    where = [v.c.company_key == key, _unique(v)]
    sal = v.c.detected_salary_avg_rub
    q = [cast(func.percentile_cont(p).within_group(sal), Float) for p in (0.25, 0.5, 0.75)]
    row = session.execute(
        select(
            func.count(),
            func.count().filter(v.c.archived_at.is_(None)),
            func.min(v.c.post_date),
            func.max(v.c.post_date),
            func.count(sal),
            *q,
            func.mode().within_group(v.c.company),
        ).where(*where)
    ).one()
    total, active, first, last, with_salary, p25, med, p75, display = row
    if not total:
        return None

    variants = _counts(
        session,
        select(v.c.company, func.count()).where(*where).group_by(v.c.company).order_by(func.count().desc()),
    )
    spec = _spec_lateral(v)
    roles = _counts(
        session,
        select(spec.c.spec, func.count())
        .select_from(v.join(spec, true()))
        .where(*where)
        .group_by(spec.c.spec)
        .order_by(func.count().desc(), spec.c.spec)
        .limit(PROFILE_TOP),
    )
    tech = _tech_lateral(v)
    techs = _counts(
        session,
        select(tech.c.tech, func.count())
        .select_from(v.join(tech, true()))
        .where(*where)
        .group_by(tech.c.tech)
        .order_by(func.count().desc(), tech.c.tech)
        .limit(PROFILE_TOP),
    )
    grades = _counts(
        session,
        select(v.c.detected_grade, func.count())
        .where(*where)
        .group_by(v.c.detected_grade)
        .order_by(func.count().desc(), v.c.detected_grade),
    )
    channels = _counts(
        session,
        select(v.c.source_channel, func.count())
        .where(*where)
        .group_by(v.c.source_channel)
        .order_by(func.count().desc(), v.c.source_channel)
        .limit(PROFILE_TOP),
    )
    s = VacancySightingRow
    posts, repost_days = session.execute(
        select(func.count(), func.count(func.distinct(func.concat(s.vacancy_id, func.date(s.post_date)))))
        .join(VacancyRow, VacancyRow.id == s.vacancy_id)
        .where(VacancyRow.company_key == key, VacancyRow.is_duplicate.is_(False))
    ).one()
    recent_rows = session.execute(
        select(
            v.c.id,
            v.c.post_date,
            v.c.detected_specializations,
            v.c.detected_grade,
            v.c.detected_salary_avg_rub,
            v.c.archived_at,
            v.c.source_channel,
        )
        .where(*where)
        .order_by(v.c.post_date.desc().nulls_last(), v.c.id)
        .limit(PROFILE_RECENT)
    ).all()
    return CompanyProfile(
        key=key,
        name=str(display or name),
        variants=[c.name for c in variants],
        vacancies=int(total),
        active=int(active),
        first_post=first,
        last_post=last,
        with_salary=int(with_salary),
        salary_p25=p25,
        salary_median=med,
        salary_p75=p75,
        roles=roles,
        technologies=techs,
        grades=grades,
        channels=channels,
        telegram_posts=int(posts or 0),
        telegram_post_days=int(repost_days or 0),
        recent=[
            CompanyProfileVacancy(
                id=r.id,
                post_date=r.post_date,
                specializations=list(r.detected_specializations or []),
                grade=r.detected_grade,
                salary_rub=r.detected_salary_avg_rub,
                archived=r.archived_at is not None,
                source_channel=r.source_channel,
            )
            for r in recent_rows
        ],
        salary_display=_prepare_fx(session),
    )


def vacancy_flags(session: Session, vacancy: VacancyRow) -> VacancyFlags:
    flags: list[VacancyFlag] = []
    s = VacancySightingRow
    days, first, last = session.execute(
        select(func.count(func.distinct(func.date(s.post_date))), func.min(s.post_date), func.max(s.post_date)).where(
            s.vacancy_id == vacancy.id
        )
    ).one()
    if days and days >= REPOST_MIN_DAYS:
        flags.append(
            VacancyFlag(
                code="reposted",
                severity="warning",
                message=f"Тот же текст публиковали в {days} {_plural(days, 'разный день', 'разных дня', 'разных дней')}",
                value=int(days),
            )
        )
    if first is not None and last is not None:
        span = (last - first).days
        if span >= LONG_OPEN_DAYS:
            flags.append(
                VacancyFlag(
                    code="long_open",
                    severity="warning",
                    message=(
                        f"Вакансия висит давно: публикации в течение {span} {_plural(span, 'дня', 'дней', 'дней')} "
                        f"(с {first.date():%d.%m.%Y})"
                    ),
                    value=span,
                )
            )
    roles = list(vacancy.detected_specializations or [])
    if vacancy.company_key and roles and vacancy.post_date is not None:
        v = _vacancies_alias()
        spec = _spec_lateral(v)
        since = vacancy.post_date - timedelta(days=FREQUENT_ROLE_WINDOW_DAYS)
        rows = session.execute(
            select(spec.c.spec, func.count(func.distinct(v.c.id)))
            .select_from(v.join(spec, true()))
            .where(
                v.c.company_key == vacancy.company_key,
                v.c.is_duplicate.is_(False),
                v.c.post_date >= since,
                v.c.post_date <= vacancy.post_date,
                spec.c.spec.in_(roles),
            )
            .group_by(spec.c.spec)
        ).all()
        role, n = max(rows, key=lambda r: (r[1], r[0]), default=(None, 0))
        if role and n >= FREQUENT_ROLE_MIN:
            flags.append(
                VacancyFlag(
                    code="frequent_role",
                    severity="info",
                    message=(
                        f"Компания много нанимает на эту роль: {n} {_plural(n, 'вакансия', 'вакансии', 'вакансий')} "
                        f"{role} за "
                        f"{FREQUENT_ROLE_WINDOW_DAYS} дней — массовый найм или аутстафф"
                    ),
                    value=int(n),
                )
            )
    if vacancy.detected_salary_avg_rub is None:
        flags.append(VacancyFlag(code="no_salary", severity="info", message="Зарплата не указана"))
    return VacancyFlags(vacancy_id=vacancy.id, company_key=vacancy.company_key, flags=flags)

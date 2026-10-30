"""Еженедельный обзор рынка: факты недели по каталогу и короткий текст LLM по ним.

Неделя — понедельник–воскресенье по UTC, вакансия относится к неделе по дате поста.
Учитываются уникальные вакансии с архивом (это история рынка). Факты считаются в Python
по строкам за 8 недель — это сотни-тысячи строк, отдельные агрегаты SQL не нужны.
LLM получает только посчитанные факты и пишет по ним текст; без LLM обзор — одни факты.
Обзор формирует лидер API за прошедшую неделю (``ensure_latest_digest``) или админ.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import MarketDigestRow, VacancyRow
from backend.db.session import session_scope
from backend.schemas import (
    DigestCount,
    DigestFacts,
    DigestListItem,
    DigestOut,
    DigestSalary,
    DigestTechnology,
)
from backend.settings import get_settings
from jobdesk.ai.prompts import MARKET_DIGEST_PROMPT_VERSION
from jobdesk.config.loader import load_config
from jobdesk.config.schema import AppConfig

_LOG = logging.getLogger(__name__)

WEEK = timedelta(days=7)
SALARY_WINDOW = timedelta(days=28)
BASE_WEEKS = 4
SALARY_GRADES = ["junior", "middle", "senior", "lead"]
# Медиана группы зарплат — от стольких вакансий, иначе null: на 5 зарплатах lead медиана
# прыгала на десятки тысяч от недели к неделе.
MIN_SALARY_GROUP = 10
# Сдвиги технологий — только если в неделе и в базе (4 недели до неё) достаточно вакансий.
TECH_MIN_WEEK = 20
TECH_MIN_BASE = 40
TECH_CANDIDATES = 30
TECH_MOVERS = 5
TECH_MIN_DELTA_PP = 1.0
TOP_ROLES = 6
TOP_COMPANIES = 5
TOP_SOURCES = 6
SUMMARY_UNAVAILABLE = "Текст обзора не сформирован: модель недоступна. Факты ниже посчитаны."


class DigestWeekError(ValueError):
    """Неделя обзора задана неверно (не понедельник или ещё не закончилась)."""


def last_complete_week_start(today: date) -> date:
    """Понедельник прошедшей (полностью завершённой) недели."""
    return today - timedelta(days=today.weekday() + 7)


def check_week_start(week_start: date, *, today: date) -> None:
    if week_start.weekday() != 0:
        raise DigestWeekError("Неделя обзора начинается с понедельника")
    if week_start + WEEK > today:
        raise DigestWeekError("Неделя ещё не закончилась")


# --- факты --------------------------------------------------------------------------------


def _counts(rows, key) -> Counter:
    c: Counter = Counter()
    for r in rows:
        for value in key(r):
            if value:
                c[value] += 1
    return c


def _top(week: Counter, prev: Counter, limit: int) -> list[DigestCount]:
    items = sorted(week.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [DigestCount(name=name, count=n, previous=prev.get(name, 0)) for name, n in items]


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if len(values) >= MIN_SALARY_GROUP else None


def _salaries(rows, week_end: datetime) -> list[DigestSalary]:
    recent_from, prev_from = week_end - SALARY_WINDOW, week_end - 2 * SALARY_WINDOW
    recent: dict[str, list[float]] = defaultdict(list)
    prev: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.detected_salary_avg_rub is None:
            continue
        bucket = recent if r.post_date >= recent_from else prev if r.post_date >= prev_from else None
        if bucket is None:
            continue
        bucket["all"].append(float(r.detected_salary_avg_rub))
        if r.detected_grade in SALARY_GRADES:
            bucket[r.detected_grade].append(float(r.detected_salary_avg_rub))
    out = []
    for grade in ["all", *SALARY_GRADES]:
        a, b = recent.get(grade, []), prev.get(grade, [])
        if not a and not b:
            continue
        out.append(
            DigestSalary(
                grade=grade, median=_median(a), count=len(a), previous_median=_median(b), previous_count=len(b)
            )
        )
    return out


def _technologies(week, base) -> tuple[list[DigestTechnology], list[DigestTechnology]]:
    if len(week) < TECH_MIN_WEEK or len(base) < TECH_MIN_BASE:
        return [], []
    w = _counts(week, lambda r: set(r.technologies or []))
    b = _counts(base, lambda r: set(r.technologies or []))
    candidates = sorted((w + b).items(), key=lambda kv: (-kv[1], kv[0]))[:TECH_CANDIDATES]
    moves = []
    for name, _ in candidates:
        share, prev_share = w.get(name, 0) / len(week), b.get(name, 0) / len(base)
        moves.append(
            DigestTechnology(name=name, share=share, previous_share=prev_share, delta_pp=(share - prev_share) * 100)
        )
    up = sorted((m for m in moves if m.delta_pp >= TECH_MIN_DELTA_PP), key=lambda m: (-m.delta_pp, m.name))
    down = sorted((m for m in moves if m.delta_pp <= -TECH_MIN_DELTA_PP), key=lambda m: (m.delta_pp, m.name))
    return up[:TECH_MOVERS], down[:TECH_MOVERS]


def _companies(session: Session, week, prev, week_start: datetime) -> tuple[list[DigestCount], list[DigestCount]]:
    names: dict[str, Counter] = defaultdict(Counter)
    for r in week:
        if r.company_key and r.company:
            names[r.company_key][r.company.strip()] += 1
    if not names:
        return [], []

    def display(key: str) -> str:
        return sorted(names[key].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    w = _counts(week, lambda r: [r.company_key] if r.company_key in names else [])
    p = _counts(prev, lambda r: [r.company_key])
    top = [DigestCount(name=display(c.name), count=c.count, previous=c.previous) for c in _top(w, p, TOP_COMPANIES)]
    first_seen = dict(
        session.execute(
            select(VacancyRow.company_key, func.min(VacancyRow.post_date))
            .where(VacancyRow.company_key.in_(list(names)), VacancyRow.is_duplicate.is_(False))
            .group_by(VacancyRow.company_key)
        ).all()
    )
    fresh = Counter({k: n for k, n in w.items() if first_seen.get(k) and first_seen[k] >= week_start})
    new = [DigestCount(name=display(c.name), count=c.count) for c in _top(fresh, Counter(), TOP_COMPANIES)]
    return top, new


def compute_facts(session: Session, week_start: date) -> DigestFacts:
    ws = datetime.combine(week_start, time(), tzinfo=UTC)
    we = ws + WEEK
    v = VacancyRow
    rows = session.execute(
        select(
            v.post_date,
            v.detected_specializations,
            v.technologies,
            v.detected_grade,
            v.detected_salary_avg_rub,
            v.company_key,
            v.company,
            v.work_format,
            v.source_channel,
        ).where(v.is_duplicate.is_(False), v.post_date >= we - 2 * SALARY_WINDOW, v.post_date < we)
    ).all()
    week = [r for r in rows if r.post_date >= ws]
    prev = [r for r in rows if ws - WEEK <= r.post_date < ws]
    base = [r for r in rows if ws - BASE_WEEKS * WEEK <= r.post_date < ws]

    def roles(r):
        return set(r.detected_specializations or [])

    up, down = _technologies(week, base)
    top_companies, new_companies = _companies(session, week, prev, ws)
    return DigestFacts(
        period_start=week_start,
        period_end=week_start + timedelta(days=6),
        vacancies=len(week),
        previous_vacancies=len(prev),
        avg_4w_vacancies=len(base) / BASE_WEEKS,
        with_salary_share=(sum(1 for r in week if r.detected_salary_avg_rub is not None) / len(week)) if week else None,
        roles=_top(_counts(week, roles), _counts(prev, roles), TOP_ROLES),
        work_formats=_top(
            _counts(week, lambda r: [r.work_format]), _counts(prev, lambda r: [r.work_format]), TOP_ROLES
        ),
        salaries=_salaries(rows, we),
        salary_window_days=SALARY_WINDOW.days,
        technologies_up=up,
        technologies_down=down,
        top_companies=top_companies,
        new_companies=new_companies,
        sources=_top(
            _counts(week, lambda r: [r.source_channel]), _counts(prev, lambda r: [r.source_channel]), TOP_SOURCES
        ),
    )


# --- текст LLM ------------------------------------------------------------------------------


def _make_client(cfg: AppConfig):
    """Свой клиент на один вызов в неделю: вне суточного бюджета пользовательского LLM."""
    from jobdesk.ai.clients.openrouter import OpenRouterClient
    from jobdesk.ai.rate_limit import OpenRouterRateLimiter

    limiter = OpenRouterRateLimiter(
        requests_per_minute=cfg.ai.requests_per_minute,
        max_retries_on_429=cfg.ai.max_retries_on_429,
        retry_base_delay_seconds=cfg.ai.retry_base_delay_seconds,
        stop_llm_when_rate_limited=False,
    )
    return OpenRouterClient(cfg.ai, rate_limiter=limiter, catalog=cfg.llm_model_catalog)


def _summarize(cfg: AppConfig, facts: DigestFacts) -> tuple[str, str]:
    """(текст, ответившая модель); основная модель писем, затем fallback-модели."""
    client = _make_client(cfg)
    try:
        facts_json = facts.model_dump_json()
        last_err: Exception | None = None
        for model in [cfg.ai.resolved_cover_letter_model(), *cfg.ai.resolved_fallback_models()]:
            try:
                text, usage = client.market_digest(facts_json=facts_json, model=model)
                return text, usage.model or model
            except Exception as e:
                last_err = e
                _LOG.warning("Обзор рынка: модель %s не ответила: %s", model, e)
        raise RuntimeError(f"Обзор рынка: все модели недоступны: {last_err}") from last_err
    finally:
        client.close()


# --- хранение и выдача ----------------------------------------------------------------------


def _is_stale(row: MarketDigestRow, cfg: AppConfig) -> bool:
    if row.summary is None:
        return False
    return row.prompt_version != MARKET_DIGEST_PROMPT_VERSION or (
        row.requested_model is not None and row.requested_model != cfg.ai.resolved_cover_letter_model()
    )


def to_out(row: MarketDigestRow, cfg: AppConfig | None = None) -> DigestOut:
    cfg = cfg or load_config(get_settings().config_path)
    return DigestOut(
        period_start=row.period_start,
        period_end=row.period_end,
        facts=DigestFacts.model_validate(row.facts),
        summary=row.summary,
        summary_error=row.summary_error,
        model=row.model,
        created_at=row.created_at,
        stale=_is_stale(row, cfg),
    )


def get_digest(session: Session, period_start: date) -> MarketDigestRow | None:
    return session.scalar(select(MarketDigestRow).where(MarketDigestRow.period_start == period_start))


def list_digests(session: Session, *, limit: int = 52) -> list[DigestListItem]:
    rows = session.scalars(select(MarketDigestRow).order_by(MarketDigestRow.period_start.desc()).limit(limit)).all()
    return [
        DigestListItem(
            period_start=r.period_start,
            period_end=r.period_end,
            vacancies=int((r.facts or {}).get("vacancies", 0)),
            has_summary=r.summary is not None,
            created_at=r.created_at,
        )
        for r in rows
    ]


def generate_digest(week_start: date, *, force: bool = False) -> DigestOut:
    """Посчитать факты недели, получить текст LLM (без удержания сессии) и сохранить."""
    cfg = load_config(get_settings().config_path)
    with session_scope() as session:
        existing = get_digest(session, week_start)
        if existing is not None and not force:
            return to_out(existing, cfg)
        facts = compute_facts(session, week_start)

    summary = model = error = None
    requested = cfg.ai.resolved_cover_letter_model()
    if cfg.digest.llm_summary and facts.vacancies > 0:
        try:
            summary, model = _summarize(cfg, facts)
        except Exception as e:
            _LOG.warning("Обзор рынка за %s: текст LLM не получен: %s", week_start, e)
            error = SUMMARY_UNAVAILABLE

    values = {
        "period_start": week_start,
        "period_end": facts.period_end,
        "facts": facts.model_dump(mode="json"),
        "summary": summary,
        "summary_error": error,
        "model": model,
        "requested_model": requested if summary is not None else None,
        "prompt_version": MARKET_DIGEST_PROMPT_VERSION if summary is not None else None,
        "created_at": datetime.now(UTC),
    }
    stmt = pg_insert(MarketDigestRow).values(**values)
    with session_scope() as session:
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_market_digests_period_start",
                set_={k: stmt.excluded[k] for k in values if k != "period_start"},
            )
        )
        row = get_digest(session, week_start)
        _LOG.info(
            "Обзор рынка за %s: %s вакансий, текст LLM: %s", week_start, facts.vacancies, "да" if summary else "нет"
        )
        return to_out(row, cfg)


def ensure_latest_digest(*, today: date | None = None) -> bool:
    """Лидер API: обзор за прошедшую неделю, если его нет и в неделе есть вакансии. True — сформирован."""
    cfg = load_config(get_settings().config_path)
    if not cfg.digest.enabled:
        return False
    week_start = last_complete_week_start(today or datetime.now(UTC).date())
    ws = datetime.combine(week_start, time(), tzinfo=UTC)
    with session_scope() as session:
        if get_digest(session, week_start) is not None:
            return False
        n = session.scalar(
            select(func.count())
            .select_from(VacancyRow)
            .where(VacancyRow.is_duplicate.is_(False), VacancyRow.post_date >= ws, VacancyRow.post_date < ws + WEEK)
        )
    if not n:
        return False
    generate_digest(week_start)
    return True

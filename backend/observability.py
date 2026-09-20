"""Prometheus-метрики доменных событий backend.

Дополняет ``tg_jobs_parser.observability.llm_metrics`` (LLM-вызовы) и
Instrumentator (HTTP): прогоны парсинга, UPSERT вакансий, TTL-архивация,
ручной импорт, матчинг резюме↔вакансия, auth, загрузки курсов ЦБ.

Плюс ``_DBStatsCollector`` — COUNT-агрегаты по таблицам, собираемые на каждый
scrape ``/metrics``. При недоступной БД коллектор молча отдаёт пустой набор,
чтобы не ломать scrape.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily

_LOG = logging.getLogger(__name__)

# --- Прогоны парсинга -------------------------------------------------------

PARSE_RUNS = Counter(
    "parse_runs_total",
    "Завершённые прогоны парсинга",
    labelnames=("status",),  # completed | failed | cancelled
)

PARSE_RUNS_ACTIVE = Gauge(
    "parse_runs_active",
    "Прогоны парсинга, выполняющиеся прямо сейчас",
)

PARSE_RUN_DURATION = Histogram(
    "parse_run_duration_seconds",
    "Длительность прогона парсинга (wall time)",
    buckets=(60.0, 300.0, 600.0, 1200.0, 1800.0, 3600.0, 7200.0, 14400.0),
)

PARSE_POSTS = Counter(
    "parse_posts_fetched_total",
    "Постов Telegram загружено прогонами",
)


def record_parse_run_finished(
    *, status: str, posts_fetched: int, duration_seconds: float
) -> None:
    PARSE_RUNS.labels(status=status or "unknown").inc()
    PARSE_RUN_DURATION.observe(max(duration_seconds, 0.0))
    if posts_fetched > 0:
        PARSE_POSTS.inc(posts_fetched)


# --- Вакансии в БД ----------------------------------------------------------

VACANCY_UPSERTS = Counter(
    "vacancies_upserted_total",
    "Строк вакансий, записанных UPSERT'ом",
    labelnames=("result",),  # inserted | updated
)

VACANCY_ARCHIVED = Counter(
    "vacancies_archived_total",
    "Вакансий отправлено в архив по TTL",
)


def record_vacancy_upsert(*, inserted: int, updated_existing: int) -> None:
    if inserted > 0:
        VACANCY_UPSERTS.labels(result="inserted").inc(inserted)
    if updated_existing > 0:
        VACANCY_UPSERTS.labels(result="updated").inc(updated_existing)


def record_vacancies_archived(count: int) -> None:
    if count > 0:
        VACANCY_ARCHIVED.inc(count)


# --- Ручной импорт ----------------------------------------------------------

IMPORT_ITEMS = Counter(
    "vacancy_import_items_total",
    "Позиции ручного импорта вакансий",
    labelnames=("status",),  # created | exists | not_vacancy | error
)


def record_import_item(status: str) -> None:
    IMPORT_ITEMS.labels(status=status or "error").inc()


# --- Матчинг резюме ↔ вакансия ----------------------------------------------

MATCH_CALLS = Counter(
    "vacancy_match_calls_total",
    "Оценки соответствия резюме вакансии",
    labelnames=("outcome",),  # success | cached | error
)

MATCH_DURATION = Histogram(
    "vacancy_match_duration_seconds",
    "Длительность LLM-матчинга (вызов + разбор)",
    buckets=(1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0),
)

MATCH_SCORE = Histogram(
    "vacancy_match_score",
    "Распределение overall_score по успешным матчам",
    buckets=(5.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 95.0, 100.0),
)


def record_match(
    *, outcome: str, duration_seconds: float | None = None, score: float | None = None
) -> None:
    MATCH_CALLS.labels(outcome=outcome).inc()
    if duration_seconds is not None:
        MATCH_DURATION.observe(max(duration_seconds, 0.0))
    if score is not None:
        MATCH_SCORE.observe(score)


# --- Сопроводительные письма -------------------------------------------------

COVER_LETTER_CALLS = Counter(
    "cover_letter_calls_total",
    "Генерации сопроводительных писем резюме↔вакансия",
    labelnames=("outcome",),  # success | cached | error
)

COVER_LETTER_DURATION = Histogram(
    "cover_letter_duration_seconds",
    "Длительность LLM-генерации письма (вызов + разбор)",
    buckets=(1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0),
)


def record_cover_letter(*, outcome: str, duration_seconds: float | None = None) -> None:
    COVER_LETTER_CALLS.labels(outcome=outcome).inc()
    if duration_seconds is not None:
        COVER_LETTER_DURATION.observe(max(duration_seconds, 0.0))


# --- Auth -------------------------------------------------------------------

AUTH_EVENTS = Counter(
    "auth_events_total",
    "События аутентификации",
    labelnames=("event", "outcome"),  # register|login × success|closed|conflict|invalid|disabled
)


def record_auth_event(event: str, outcome: str) -> None:
    AUTH_EVENTS.labels(event=event, outcome=outcome).inc()


# --- Курсы валют (ЦБ) --------------------------------------------------------

FX_CBR_REQUESTS = Counter(
    "fx_cbr_requests_total",
    "HTTP-запросы к ЦБ за курсами валют",
    labelnames=("outcome",),  # fetched | failed
)


def record_fx_fetch(outcome: str) -> None:
    FX_CBR_REQUESTS.labels(outcome=outcome).inc()


# --- Агрегаты БД на scrape ---------------------------------------------------


class _DBStatsCollector:
    """COUNT-запросы по таблицам на каждый scrape /metrics.

    Выполняется в процессе backend; при недоступной БД возвращает пустой
    набор — scrape не должен падать из-за postgres. ``describe()`` нужен,
    чтобы ``REGISTRY.register`` не дёргал ``collect()`` (и БД) при импорте.
    """

    def describe(self):
        yield GaugeMetricFamily("db_vacancies", "Вакансии в БД", labels=["state"])
        yield GaugeMetricFamily("db_resumes", "Резюме в БД")
        yield GaugeMetricFamily("db_users", "Пользователи в БД")
        yield GaugeMetricFamily("db_vacancy_matches", "Оценки резюме↔вакансия")
        yield GaugeMetricFamily("db_cover_letters", "Сопроводительные письма в БД")
        yield GaugeMetricFamily("db_ai_cache_entries", "Записи LLM-кэша в БД")
        yield GaugeMetricFamily("db_parse_runs", "Прогоны в БД по статусу", labels=["status"])
        yield GaugeMetricFamily(
            "fx_latest_rate_timestamp_seconds", "Дата последнего курса ЦБ (unix time)"
        )

    def collect(self):
        try:
            counts, runs_by_status, fx_latest_ts = self._read_db()
        except Exception as e:
            _LOG.debug("DBStatsCollector: БД недоступна: %s", e)
            return

        fam = GaugeMetricFamily(
            "db_vacancies", "Вакансии в БД", labels=["state"]
        )
        fam.add_metric(["active"], counts["vacancies_active"])
        fam.add_metric(["archived"], counts["vacancies_archived"])
        yield fam

        yield GaugeMetricFamily("db_resumes", "Резюме в БД", value=counts["resumes"])
        yield GaugeMetricFamily("db_users", "Пользователи в БД", value=counts["users"])
        yield GaugeMetricFamily(
            "db_vacancy_matches", "Сохранённые оценки резюме↔вакансия", value=counts["matches"]
        )
        yield GaugeMetricFamily(
            "db_cover_letters", "Сохранённые сопроводительные письма", value=counts["cover_letters"]
        )
        yield GaugeMetricFamily(
            "db_ai_cache_entries", "Записи LLM-кэша в БД", value=counts["ai_cache"]
        )

        fam = GaugeMetricFamily(
            "db_parse_runs", "Прогоны в БД по статусу", labels=["status"]
        )
        for status, n in runs_by_status.items():
            fam.add_metric([status], n)
        yield fam

        if fx_latest_ts is not None:
            yield GaugeMetricFamily(
                "fx_latest_rate_timestamp_seconds",
                "Дата последнего загруженного курса ЦБ (unix time)",
                value=fx_latest_ts,
            )

    @staticmethod
    def _read_db() -> tuple[dict[str, int], dict[str, int], float | None]:
        from sqlalchemy import func, select

        from backend.db.models import (
            AIExtractionCacheRow,
            CoverLetterRow,
            ExchangeRateRow,
            ParseRunRow,
            ResumeRow,
            UserRow,
            VacancyMatchRow,
            VacancyRow,
        )
        from backend.db.session import session_scope

        counts: dict[str, int] = {}
        runs_by_status: dict[str, int] = {}
        fx_latest_ts: float | None = None

        with session_scope() as s:
            counts["vacancies_active"] = int(
                s.scalar(
                    select(func.count())
                    .select_from(VacancyRow)
                    .where(VacancyRow.archived_at.is_(None))
                )
                or 0
            )
            counts["vacancies_archived"] = int(
                s.scalar(
                    select(func.count())
                    .select_from(VacancyRow)
                    .where(VacancyRow.archived_at.is_not(None))
                )
                or 0
            )
            counts["resumes"] = int(
                s.scalar(select(func.count()).select_from(ResumeRow)) or 0
            )
            counts["users"] = int(
                s.scalar(select(func.count()).select_from(UserRow)) or 0
            )
            counts["matches"] = int(
                s.scalar(select(func.count()).select_from(VacancyMatchRow)) or 0
            )
            counts["cover_letters"] = int(
                s.scalar(select(func.count()).select_from(CoverLetterRow)) or 0
            )
            counts["ai_cache"] = int(
                s.scalar(select(func.count()).select_from(AIExtractionCacheRow)) or 0
            )

            for status, n in s.execute(
                select(ParseRunRow.status, func.count()).group_by(ParseRunRow.status)
            ):
                runs_by_status[str(status)] = int(n)

            latest = s.scalar(select(func.max(ExchangeRateRow.rate_date)))
            if latest is not None:
                fx_latest_ts = datetime(
                    latest.year, latest.month, latest.day, tzinfo=timezone.utc
                ).timestamp()

        return counts, runs_by_status, fx_latest_ts


REGISTRY.register(_DBStatsCollector())

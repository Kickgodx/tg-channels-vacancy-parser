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
import os
import time
from datetime import UTC, datetime

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

PARSE_RUN_STARTED_TIMESTAMP = Gauge(
    "parse_run_started_timestamp_seconds",
    "Unix-время старта активного прогона (0 если нет)",
)

PARSE_RUN_DURATION = Histogram(
    "parse_run_duration_seconds",
    "Длительность прогона парсинга (wall time)",
    buckets=(10.0, 30.0, 60.0, 300.0, 600.0, 1200.0, 1800.0, 3600.0, 7200.0, 14400.0),
)

PARSE_POSTS = Counter(
    "parse_posts_fetched_total",
    "Постов Telegram загружено прогонами",
)


def mark_parse_run_started() -> None:
    PARSE_RUN_STARTED_TIMESTAMP.set(time.time())


def mark_parse_run_stopped() -> None:
    PARSE_RUN_STARTED_TIMESTAMP.set(0.0)


def record_parse_run_finished(*, status: str, posts_fetched: int, duration_seconds: float) -> None:
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


def record_match(*, outcome: str, duration_seconds: float | None = None, score: float | None = None) -> None:
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


# --- Фоновые LLM-джобы (мэтч / письмо) --------------------------------------

LLM_JOB_CALLS = Counter(
    "llm_jobs_total",
    "Фоновые LLM-задачи мэтчинга и писем",
    labelnames=("outcome", "kind"),  # queued|done|error × match|cover_letter
)

LLM_JOB_DURATION = Histogram(
    "llm_job_duration_seconds",
    "Длительность фоновой LLM-задачи (очередь + выполнение)",
    labelnames=("kind",),
    buckets=(1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0),
)


def record_llm_job(*, outcome: str, kind: str, duration_seconds: float | None = None) -> None:
    LLM_JOB_CALLS.labels(outcome=outcome, kind=kind or "unknown").inc()
    if duration_seconds is not None:
        LLM_JOB_DURATION.labels(kind=kind or "unknown").observe(max(duration_seconds, 0.0))


LLM_JOB_ENQUEUE = Counter(
    "llm_job_enqueue_total",
    "Постановка LLM-джоб в очередь",
    labelnames=("backend", "kind", "outcome"),  # queued|reused|rejected_limit|error
)

LLM_RQ_QUEUE_JOBS = Gauge(
    "llm_rq_queue_jobs",
    "Глубина очереди RQ llm",
    labelnames=("queue",),
)

LLM_WORKER_JOBS = Counter(
    "llm_worker_jobs_total",
    "Исполнение LLM-джоб на worker",
    labelnames=("kind", "outcome"),  # started|done|error
)

LLM_WORKER_DURATION = Histogram(
    "llm_worker_job_duration_seconds",
    "Длительность run_llm_job на worker",
    labelnames=("kind",),
    buckets=(1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)

LLM_JOBS_RECOVER = Counter(
    "llm_jobs_recover_total",
    "Recover зависших LLM-джоб при старте worker",
    labelnames=("from_status",),
)

LLM_JOB_SSE_CONNECTIONS = Gauge(
    "llm_job_sse_connections",
    "Активные SSE-подписки на статус LLM-джоб",
)


def record_llm_job_enqueue(*, backend: str, kind: str, outcome: str) -> None:
    LLM_JOB_ENQUEUE.labels(
        backend=backend or "unknown",
        kind=kind or "unknown",
        outcome=outcome or "unknown",
    ).inc()


def record_llm_worker_job(*, kind: str, outcome: str, duration_seconds: float | None = None) -> None:
    LLM_WORKER_JOBS.labels(kind=kind or "unknown", outcome=outcome or "unknown").inc()
    if duration_seconds is not None:
        LLM_WORKER_DURATION.labels(kind=kind or "unknown").observe(max(duration_seconds, 0.0))


def record_llm_jobs_recover(*, from_status: str) -> None:
    LLM_JOBS_RECOVER.labels(from_status=from_status or "unknown").inc()


def set_llm_rq_queue_depth(count: int | None, *, queue: str = "llm") -> None:
    if count is None:
        return
    LLM_RQ_QUEUE_JOBS.labels(queue=queue).set(count)


# --- Auth -------------------------------------------------------------------

AUTH_EVENTS = Counter(
    "auth_events_total",
    "События аутентификации",
    labelnames=(
        "event",
        "outcome",
    ),  # register|login × success|closed|conflict|invalid|disabled
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


# --- HH.ru sync --------------------------------------------------------------

HH_SYNCS = Counter(
    "hh_syncs_total",
    "Завершённые синхронизации HH",
    labelnames=("outcome",),  # success | api_error | not_configured | error
)

HH_SYNC_DURATION = Histogram(
    "hh_sync_duration_seconds",
    "Длительность HH sync (wall time)",
    buckets=(5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1200.0, 3600.0),
)

HH_SEARCHED = Counter(
    "hh_vacancies_searched_total",
    "Вакансий получено из поиска HH",
)

HH_DETAILS = Counter(
    "hh_details_total",
    "Запросы detail к HH API",
    labelnames=("result",),  # fetched | skipped
)

HH_API_ERRORS = Counter(
    "hh_api_errors_total",
    "Ошибки HH API",
    labelnames=("status",),
)

HH_LISTINGS_PURGED = Counter(
    "hh_listings_purged_total",
    "Строк hh_listings удалено по TTL",
)


def record_hh_sync_finished(*, outcome: str, duration_seconds: float, searched: int) -> None:
    HH_SYNCS.labels(outcome=outcome or "unknown").inc()
    HH_SYNC_DURATION.observe(max(duration_seconds, 0.0))
    if searched > 0:
        HH_SEARCHED.inc(searched)


def record_hh_details(*, fetched: int, skipped: int) -> None:
    if fetched > 0:
        HH_DETAILS.labels(result="fetched").inc(fetched)
    if skipped > 0:
        HH_DETAILS.labels(result="skipped").inc(skipped)


def record_hh_api_error(*, status: str) -> None:
    HH_API_ERRORS.labels(status=status or "unknown").inc()


def record_hh_listings_purged(count: int) -> None:
    if count > 0:
        HH_LISTINGS_PURGED.inc(count)


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
            "db_parse_runs_finished_24h",
            "Прогоны с finished_at за последние 24 ч",
            labels=["status"],
        )
        yield GaugeMetricFamily(
            "db_parse_posts_fetched_24h",
            "Сумма posts_fetched у прогонов, завершённых за 24 ч",
        )
        yield GaugeMetricFamily("fx_latest_rate_timestamp_seconds", "Дата последнего курса ЦБ (unix time)")
        yield GaugeMetricFamily("db_hh_listings", "Кэш HH listings", labels=["state"])
        yield GaugeMetricFamily("llm_jobs_by_status", "LLM-джобы в БД по статусу", labels=["status"])

    def collect(self):
        try:
            (
                counts,
                runs_by_status,
                runs_finished_24h,
                posts_fetched_24h,
                fx_latest_ts,
                hh_listings,
                llm_jobs_by_status,
            ) = self._read_db()
        except Exception as e:
            _LOG.debug("DBStatsCollector: БД недоступна: %s", e)
            return
        try:
            from backend.queue.rq_conn import llm_queue_length

            set_llm_rq_queue_depth(llm_queue_length())
        except Exception:
            pass

        fam = GaugeMetricFamily("db_vacancies", "Вакансии в БД", labels=["state"])
        fam.add_metric(["active"], counts["vacancies_active"])
        fam.add_metric(["archived"], counts["vacancies_archived"])
        yield fam

        yield GaugeMetricFamily("db_resumes", "Резюме в БД", value=counts["resumes"])
        yield GaugeMetricFamily("db_users", "Пользователи в БД", value=counts["users"])
        yield GaugeMetricFamily(
            "db_vacancy_matches",
            "Сохранённые оценки резюме↔вакансия",
            value=counts["matches"],
        )
        yield GaugeMetricFamily(
            "db_cover_letters",
            "Сохранённые сопроводительные письма",
            value=counts["cover_letters"],
        )
        yield GaugeMetricFamily("db_ai_cache_entries", "Записи LLM-кэша в БД", value=counts["ai_cache"])

        fam = GaugeMetricFamily("db_parse_runs", "Прогоны в БД по статусу", labels=["status"])
        for status, n in runs_by_status.items():
            fam.add_metric([status], n)
        yield fam

        fam = GaugeMetricFamily(
            "db_parse_runs_finished_24h",
            "Прогоны с finished_at за последние 24 ч",
            labels=["status"],
        )
        for status, n in runs_finished_24h.items():
            fam.add_metric([status], n)
        yield fam

        yield GaugeMetricFamily(
            "db_parse_posts_fetched_24h",
            "Сумма posts_fetched у прогонов, завершённых за 24 ч",
            value=posts_fetched_24h,
        )

        if fx_latest_ts is not None:
            yield GaugeMetricFamily(
                "fx_latest_rate_timestamp_seconds",
                "Дата последнего загруженного курса ЦБ (unix time)",
                value=fx_latest_ts,
            )

        fam = GaugeMetricFamily("db_hh_listings", "Кэш HH listings", labels=["state"])
        fam.add_metric(["total"], hh_listings["total"])
        fam.add_metric(["with_detail"], hh_listings["with_detail"])
        fam.add_metric(["without_detail"], hh_listings["without_detail"])
        yield fam

        fam = GaugeMetricFamily("llm_jobs_by_status", "LLM-джобы в БД по статусу", labels=["status"])
        for status, n in llm_jobs_by_status.items():
            fam.add_metric([status], n)
        yield fam

    @staticmethod
    def _read_db() -> tuple[
        dict[str, int],
        dict[str, int],
        dict[str, int],
        int,
        float | None,
        dict[str, int],
        dict[str, int],
    ]:
        from datetime import timedelta

        from sqlalchemy import func, select

        from backend.db.models import (
            AIExtractionCacheRow,
            CoverLetterRow,
            ExchangeRateRow,
            HhListingRow,
            LlmJobRow,
            ParseRunRow,
            ResumeRow,
            UserRow,
            VacancyMatchRow,
            VacancyRow,
        )
        from backend.db.session import session_scope

        counts: dict[str, int] = {}
        runs_by_status: dict[str, int] = {}
        runs_finished_24h: dict[str, int] = {}
        posts_fetched_24h = 0
        fx_latest_ts: float | None = None
        hh_listings = {"total": 0, "with_detail": 0, "without_detail": 0}
        llm_jobs_by_status: dict[str, int] = {}
        since = datetime.now(UTC) - timedelta(hours=24)

        with session_scope() as s:
            counts["vacancies_active"] = int(
                s.scalar(select(func.count()).select_from(VacancyRow).where(VacancyRow.archived_at.is_(None))) or 0
            )
            counts["vacancies_archived"] = int(
                s.scalar(select(func.count()).select_from(VacancyRow).where(VacancyRow.archived_at.is_not(None))) or 0
            )
            counts["resumes"] = int(s.scalar(select(func.count()).select_from(ResumeRow)) or 0)
            counts["users"] = int(s.scalar(select(func.count()).select_from(UserRow)) or 0)
            counts["matches"] = int(s.scalar(select(func.count()).select_from(VacancyMatchRow)) or 0)
            counts["cover_letters"] = int(s.scalar(select(func.count()).select_from(CoverLetterRow)) or 0)
            counts["ai_cache"] = int(s.scalar(select(func.count()).select_from(AIExtractionCacheRow)) or 0)

            for status, n in s.execute(select(ParseRunRow.status, func.count()).group_by(ParseRunRow.status)):
                runs_by_status[str(status)] = int(n)

            for status, n in s.execute(
                select(ParseRunRow.status, func.count())
                .where(ParseRunRow.finished_at.is_not(None))
                .where(ParseRunRow.finished_at >= since)
                .group_by(ParseRunRow.status)
            ):
                runs_finished_24h[str(status)] = int(n)

            posts_fetched_24h = int(
                s.scalar(
                    select(func.coalesce(func.sum(ParseRunRow.posts_fetched), 0)).where(
                        ParseRunRow.finished_at.is_not(None),
                        ParseRunRow.finished_at >= since,
                    )
                )
                or 0
            )

            latest = s.scalar(select(func.max(ExchangeRateRow.rate_date)))
            if latest is not None:
                fx_latest_ts = datetime(latest.year, latest.month, latest.day, tzinfo=UTC).timestamp()

            hh_total = int(s.scalar(select(func.count()).select_from(HhListingRow)) or 0)
            hh_with = int(
                s.scalar(
                    select(func.count()).select_from(HhListingRow).where(HhListingRow.detail_fetched_at.is_not(None))
                )
                or 0
            )
            hh_listings = {
                "total": hh_total,
                "with_detail": hh_with,
                "without_detail": hh_total - hh_with,
            }

            for status, n in s.execute(select(LlmJobRow.status, func.count()).group_by(LlmJobRow.status)):
                llm_jobs_by_status[str(status)] = int(n)

        return (
            counts,
            runs_by_status,
            runs_finished_24h,
            posts_fetched_24h,
            fx_latest_ts,
            hh_listings,
            llm_jobs_by_status,
        )


# На llm-worker COUNT по всем таблицам не нужен: лишний RAM/нагрузка на Postgres
# при scale и дубли рядов в Prometheus. Счётчики LLM-джоб регистрируются выше всегда.
if os.environ.get("TG_JOBS_METRICS_ROLE", "backend").strip().lower() != "worker":
    REGISTRY.register(_DBStatsCollector())

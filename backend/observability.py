"""Prometheus-метрики доменных событий backend.

Дополняет ``tg_jobs_parser.observability.llm_metrics`` (LLM-вызовы) и
Instrumentator (HTTP): прогоны парсинга, UPSERT вакансий, TTL-архивация,
ручной импорт, матчинг резюме↔вакансия, auth, загрузки курсов ЦБ.

Плюс ``_DBStatsCollector`` — COUNT-агрегаты по таблицам для scrape ``/metrics``
с кэшем на ``METRICS_DB_STATS_CACHE_SECONDS``. При недоступной БД коллектор
молча отдаёт пустой набор, чтобы не ломать scrape.

``_DBPoolCollector`` — состояние SQLAlchemy QueuePool (занято / свободно /
overflow / лимит). Регистрируется и на backend, и на llm-worker.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily

_LOG = logging.getLogger(__name__)

# --- Прогоны парсинга -------------------------------------------------------

PARSE_RUNS = Counter(
    "parse_runs_total",
    "Завершённые прогоны парсинга",
    labelnames=("status",),  # completed | partial | failed | cancelled
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


# Нули для известных лейблов: иначе первый прирост серии не виден increase()/rate()
# (см. tg_jobs_parser/observability/parse_metrics.py).
for _status in ("completed", "partial", "failed", "cancelled"):
    PARSE_RUNS.labels(status=_status)
for _result in ("inserted", "updated"):
    VACANCY_UPSERTS.labels(result=_result)


def init_llm_model_series() -> None:
    """Нулевые LLM-серии для моделей ``config.yaml`` — на старте процесса, который ходит в LLM.

    Клиент создаётся непосредственно перед первым вызовом (в прогоне — после fetch), и
    заведённые им серии не успевают попасть в scrape до первого инкремента.
    """
    from backend.settings import get_settings
    from tg_jobs_parser.config.loader import load_config
    from tg_jobs_parser.observability.llm_metrics import init_model_series

    try:
        init_model_series(load_config(get_settings().config_path).ai.resolved_models())
    except Exception as e:
        _LOG.warning("LLM-метрики: модели из конфига не заведены: %s", e)


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
    labelnames=("outcome", "kind"),  # queued|done|error|cancelled × match|cover_letter|import
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

# Имя метрики историческое (была только очередь llm); лейбл queue — llm | parse.
LLM_RQ_QUEUE_JOBS = Gauge(
    "llm_rq_queue_jobs",
    "Глубина очередей RQ (llm, parse)",
    labelnames=("queue",),
)

LLM_WORKER_JOBS = Counter(
    "llm_worker_jobs_total",
    "Исполнение LLM-джоб на worker",
    labelnames=("kind", "outcome"),  # started|done|error|cancelled
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


PARSE_JOBS_RECOVER = Counter(
    "parse_jobs_recover_total",
    "Recover зависших parse-прогонов при старте worker",
    labelnames=("from_status",),
)


def record_parse_jobs_recover(*, from_status: str) -> None:
    PARSE_JOBS_RECOVER.labels(from_status=from_status or "unknown").inc()


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
    ),  # register|login|refresh|change_password|logout_all × success|closed|conflict|invalid|disabled|rate_limited
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
    labelnames=("outcome",),  # success | cancelled | api_error | not_configured | error
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


# --- Пул соединений SQLAlchemy -----------------------------------------------


class _DBPoolCollector:
    """Состояние QueuePool на scrape ``/metrics``.

    На пуле не QueuePool (NullPool и т.п.) метрики не отдаются.
    Ошибки чтения пула глотаются: scrape не должен падать.
    """

    def describe(self):
        yield GaugeMetricFamily("db_pool_checked_out", "Соединения, выданные из пула")
        yield GaugeMetricFamily("db_pool_checked_in", "Простаивающие соединения в пуле")
        yield GaugeMetricFamily("db_pool_size", "Настроенный размер постоянного пула")
        yield GaugeMetricFamily("db_pool_overflow", "Текущий overflow сверх pool_size (≥0)")
        yield GaugeMetricFamily("db_pool_max_overflow", "Максимальный overflow (настройка)")
        yield GaugeMetricFamily(
            "db_pool_max_connections",
            "Верхняя граница соединений процесса (pool_size + max_overflow)",
        )

    def collect(self):
        stats = self._read_pool()
        if stats is None:
            return
        yield GaugeMetricFamily(
            "db_pool_checked_out",
            "Соединения, выданные из пула",
            value=stats["checked_out"],
        )
        yield GaugeMetricFamily(
            "db_pool_checked_in",
            "Простаивающие соединения в пуле",
            value=stats["checked_in"],
        )
        yield GaugeMetricFamily(
            "db_pool_size",
            "Настроенный размер постоянного пула",
            value=stats["size"],
        )
        yield GaugeMetricFamily(
            "db_pool_overflow",
            "Текущий overflow сверх pool_size (≥0)",
            value=stats["overflow"],
        )
        yield GaugeMetricFamily(
            "db_pool_max_overflow",
            "Максимальный overflow (настройка)",
            value=stats["max_overflow"],
        )
        yield GaugeMetricFamily(
            "db_pool_max_connections",
            "Верхняя граница соединений процесса (pool_size + max_overflow)",
            value=stats["max_connections"],
        )

    @staticmethod
    def _read_pool() -> dict[str, float] | None:
        try:
            from sqlalchemy.pool import QueuePool

            from backend.db.session import get_engine

            pool = get_engine().pool
            if not isinstance(pool, QueuePool):
                return None
            size = float(pool.size())
            # QueuePool._max_overflow: -1 = без лимита (редко); иначе ≥0.
            raw_max_overflow = getattr(pool, "_max_overflow", 0)
            try:
                max_overflow = float(raw_max_overflow)
            except (TypeError, ValueError):
                max_overflow = 0.0
            if max_overflow < 0:
                # unlimited overflow — capacity неизвестна; отдаём только size как floor
                max_connections = size
                max_overflow_metric = -1.0
            else:
                max_overflow_metric = max_overflow
                max_connections = size + max_overflow
            return {
                "checked_out": float(pool.checkedout()),
                "checked_in": float(pool.checkedin()),
                "size": size,
                "overflow": float(max(pool.overflow(), 0)),
                "max_overflow": max_overflow_metric,
                "max_connections": max_connections,
            }
        except Exception as e:
            _LOG.debug("DBPoolCollector: пул недоступен: %s", e)
            return None


# --- Агрегаты БД на scrape ---------------------------------------------------


class _DBStatsCollector:
    """COUNT-запросы по таблицам для scrape /metrics, с кэшем.

    Выполняется в процессе backend. Результат живёт
    ``METRICS_DB_STATS_CACHE_SECONDS``: частые или параллельные scrape не
    повторяют ~12 COUNT'ов по всем таблицам (параллельные ждут первый под
    lock'ом). При недоступной БД возвращает пустой набор и ничего не кэширует —
    scrape не должен падать из-за postgres. ``describe()`` нужен, чтобы
    ``REGISTRY.register`` не дёргал ``collect()`` (и БД) при импорте.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached_at: float | None = None
        self._cached: tuple | None = None

    def _read_db_cached(self) -> tuple:
        from backend.settings import get_settings

        ttl = get_settings().metrics_db_stats_cache_seconds
        with self._lock:
            now = time.monotonic()
            if ttl > 0 and self._cached_at is not None and now - self._cached_at < ttl:
                return self._cached
            data = self._read_db()
            self._cached_at, self._cached = (now, data) if ttl > 0 else (None, None)
            return data

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
            ) = self._read_db_cached()
        except Exception as e:
            _LOG.debug("DBStatsCollector: БД недоступна: %s", e)
            return
        try:
            from backend.queue.rq_conn import llm_queue_length, parse_queue_depth

            set_llm_rq_queue_depth(llm_queue_length())
            # Прогон висит в Redis, а parse-worker его не берёт: recover в rq-режиме живёт в
            # самом воркере, и без этой серии упавший worker виден только по queued в БД.
            set_llm_rq_queue_depth(parse_queue_depth(), queue="parse")
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


# Пул — на каждом процессе (backend и воркеры): у каждого свой QueuePool.
REGISTRY.register(_DBPoolCollector())

_db_stats_lock = threading.Lock()
_db_stats_registered = False


def register_db_stats_collector() -> None:
    """COUNT-агрегаты таблиц в ``/metrics`` — только процесс API (``backend.main``).

    Явный вызов, а не проверка env при импорте: воркеры импортируют этот модуль
    транзитивно (recover, tasks) раньше, чем успевают выставить свою роль, и
    отдавали те же ``db_*`` — ``sum(db_users)`` на дашборде показывал ×3. Плюс
    лишние COUNT'ы по всем таблицам на каждой реплике.
    """
    global _db_stats_registered
    with _db_stats_lock:
        if not _db_stats_registered:
            REGISTRY.register(_DBStatsCollector())
            _db_stats_registered = True

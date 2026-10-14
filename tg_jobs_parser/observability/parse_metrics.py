"""Prometheus-метрики прогона парсинга, которые видны только ядру: Telegram fetch и обработка постов.

Итоги прогона (статус, длительность, UPSERT) пишет backend — ``backend/observability.py``.
Здесь — то, что происходит внутри ``run_parse`` и должно быть видно **во время** прогона:
прочитанные каналы и посты, FloodWait, поток обработанных постов, длительность фаз.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from prometheus_client import Counter, Histogram

TELEGRAM_CHANNELS = Counter(
    "telegram_channels_fetched_total",
    "Каналы Telegram, обход которых завершён",
    labelnames=("result",),  # ok | partial | error
)

TELEGRAM_POSTS = Counter(
    "telegram_posts_fetched_total",
    "Текстовые посты, прочитанные из Telegram (по мере обхода каналов)",
)

TELEGRAM_MESSAGES_SCANNED = Counter(
    "telegram_messages_scanned_total",
    "Сообщения, просмотренные через Telegram API (включая пустые и вне окна дат)",
)

TELEGRAM_CHANNEL_DURATION = Histogram(
    "telegram_channel_fetch_duration_seconds",
    "Длительность обхода одного канала (с ожиданием FloodWait)",
    buckets=(0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)

TELEGRAM_FLOOD_WAITS = Counter(
    "telegram_flood_waits_total",
    "FloodWait от Telegram",
    # telethon — Telethon проспал сам (< flood_sleep_threshold, по умолчанию 60 с);
    # resumed — наш обработчик выждал и продолжил; gave_up — канал вернулся частично.
    labelnames=("handled_by",),
)

TELEGRAM_FLOOD_WAIT_SECONDS = Counter(
    "telegram_flood_wait_seconds_total",
    "Секунды FloodWait, запрошенные Telegram (gave_up — не выжданные; параллельные ожидания складываются)",
    labelnames=("handled_by",),
)

PARSE_POSTS_PROCESSED = Counter(
    "parse_posts_processed_total",
    "Посты, прошедшие пост-обработку (rules + AI + фильтры)",
    labelnames=("result",),  # candidate | dropped
)

PARSE_PHASE_DURATION = Histogram(
    "parse_phase_duration_seconds",
    "Длительность фаз прогона парсинга",
    labelnames=("phase",),  # fetch | process | dedup
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1200.0, 3600.0, 7200.0),
)

# Серия с лейблами попадает в /metrics только после первого ``.labels()`` — сразу с ненулевым
# значением, и increase()/rate() этот первый прирост не видят: им не с чем сравнить. Для
# короткого прогона на свежем процессе это весь прогон. Заводим известные лейблы нулями.
for _result in ("ok", "partial", "error"):
    TELEGRAM_CHANNELS.labels(result=_result)
for _handled_by in ("telethon", "resumed", "gave_up"):
    TELEGRAM_FLOOD_WAITS.labels(handled_by=_handled_by)
    TELEGRAM_FLOOD_WAIT_SECONDS.labels(handled_by=_handled_by)
for _result in ("candidate", "dropped"):
    PARSE_POSTS_PROCESSED.labels(result=_result)
for _phase in ("fetch", "process", "dedup"):
    PARSE_PHASE_DURATION.labels(phase=_phase)


def record_channel_fetched(*, result: str, posts: int, scanned: int, duration_seconds: float) -> None:
    TELEGRAM_CHANNELS.labels(result=result).inc()
    if posts > 0:
        TELEGRAM_POSTS.inc(posts)
    if scanned > 0:
        TELEGRAM_MESSAGES_SCANNED.inc(scanned)
    TELEGRAM_CHANNEL_DURATION.observe(max(duration_seconds, 0.0))


def record_flood_wait(*, handled_by: str, seconds: float, request: str = "") -> None:
    """Счётчики + event на текущем span'е (``telegram.channel``, если обход идёт в нём)."""
    TELEGRAM_FLOOD_WAITS.labels(handled_by=handled_by).inc()
    if seconds > 0:
        TELEGRAM_FLOOD_WAIT_SECONDS.labels(handled_by=handled_by).inc(seconds)
    attrs: dict[str, str | float] = {"telegram.flood_wait.seconds": float(seconds), "handled_by": handled_by}
    if request:
        attrs["telegram.request"] = request
    trace.get_current_span().add_event("telegram.flood_wait", attributes=attrs)


def record_post_processed(*, candidate: bool) -> None:
    PARSE_POSTS_PROCESSED.labels(result="candidate" if candidate else "dropped").inc()


def record_phase_duration(phase: str, seconds: float) -> None:
    if seconds > 0:
        PARSE_PHASE_DURATION.labels(phase=phase).observe(seconds)


class TelethonFloodSleepFilter(logging.Filter):
    """Короткие FloodWait Telethon пережидает сам и сообщает о них только INFO-логом.

    Запись ``telethon.client.users``: ``'Sleeping%s for %ds (%s) on %s flood wait'`` с
    ``args = (' early' | '', seconds, timedelta, request_name)``. Фильтр превращает её в
    метрику и span-event, саму запись не трогает. Без него 25 с FloodWait в фазе fetch
    видно только как «дыру» в логах. Секунды и запрос ищутся по типу, а не по позиции —
    на случай смены порядка аргументов в Telethon. Фильтр логгера вызывается, только
    если для ``telethon.client.users`` включён INFO (так настраивает ``setup_logging``).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg if isinstance(record.msg, str) else ""
        if msg.startswith("Sleeping") and "flood wait" in msg:
            args = record.args if isinstance(record.args, tuple) else ()
            numbers = [a for a in args if isinstance(a, int | float) and not isinstance(a, bool)]
            request = args[-1] if args and isinstance(args[-1], str) else ""
            record_flood_wait(
                handled_by="telethon",
                seconds=float(numbers[0]) if numbers else 0.0,
                request=request.strip(),
            )
        return True


_FLOOD_FILTER = TelethonFloodSleepFilter()


def install_telethon_flood_filter() -> None:
    """Идемпотентно повесить фильтр на логгер Telethon, который пишет о flood-sleep."""
    logger = logging.getLogger("telethon.client.users")
    if _FLOOD_FILTER not in logger.filters:
        logger.addFilter(_FLOOD_FILTER)

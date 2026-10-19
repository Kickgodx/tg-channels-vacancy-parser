"""Короткий кэш агрегатов по вакансиям (метрики дашборда и аналитика).

``/vacancies/metrics`` — 8 агрегатных запросов, ``/analytics/salaries`` — 10 с
``percentile_cont``; каждый — проход по отфильтрованному корпусу. Дашборд
перезапрашивает их при каждом заходе и смене вкладки, хотя корпус меняется
редко (прогон, HH sync, пересчёт курсов). Кэш процесса на ``ANALYTICS_CACHE_SECONDS``
по ключу «эндпоинт + фильтры»; запись вакансий в этом процессе сбрасывает его
сразу, запись из воркера видна не позже чем через TTL.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from backend.schemas import VacancyQuery

_MAX_ENTRIES = 128

_lock = threading.Lock()
_entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()


def _ttl_seconds() -> float:
    from backend.settings import get_settings

    return max(0.0, get_settings().analytics_cache_seconds)


def cached_aggregate[T](name: str, query: VacancyQuery, compute: Callable[[], T], **extra: Any) -> T:
    """Вернуть агрегат из кэша или посчитать ``compute()`` и запомнить.

    Пагинация и сортировка в ключ не входят — на агрегаты они не влияют.
    """
    ttl = _ttl_seconds()
    if ttl <= 0:
        return compute()
    filters = query.model_dump(
        mode="json", exclude={"page", "page_size", "sort_by", "sort_order", "then_by", "then_order"}
    )
    key = f"{name}|{sorted(filters.items())!r}|{sorted(extra.items())!r}"
    now = time.monotonic()
    with _lock:
        hit = _entries.get(key)
        if hit is not None and now - hit[0] < ttl:
            _entries.move_to_end(key)
            return hit[1]
    # Считаем без лока: параллельный промах посчитает дважды, но не заблокирует остальных.
    value = compute()
    with _lock:
        _entries[key] = (time.monotonic(), value)
        _entries.move_to_end(key)
        while len(_entries) > _MAX_ENTRIES:
            _entries.popitem(last=False)
    return value


def invalidate_aggregate_cache() -> None:
    with _lock:
        _entries.clear()

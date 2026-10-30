"""Prometheus-метрики HTTP-вызовов к HH API."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

HH_API_REQUESTS = Counter(
    "hh_api_requests_total",
    "HTTP-запросы к api.hh.ru",
    labelnames=("method", "endpoint", "status"),
)

HH_API_DURATION = Histogram(
    "hh_api_request_duration_seconds",
    "Длительность HTTP-запросов к api.hh.ru",
    labelnames=("method", "endpoint"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)


def _endpoint_label(path: str) -> str:
    """Схлопывает /vacancies/{id} → /vacancies/:id, остальное — как есть."""
    raw = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if raw.startswith("/vacancies/") and raw != "/vacancies":
        return "/vacancies/:id"
    if raw == "/vacancies":
        return "/vacancies"
    return raw[:64] or "unknown"


def record_hh_api_request(
    *,
    method: str,
    path: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    endpoint = _endpoint_label(path)
    status = str(status_code) if status_code else "unknown"
    HH_API_REQUESTS.labels(method=(method or "GET").upper(), endpoint=endpoint, status=status).inc()
    HH_API_DURATION.labels(method=(method or "GET").upper(), endpoint=endpoint).observe(max(duration_seconds, 0.0))

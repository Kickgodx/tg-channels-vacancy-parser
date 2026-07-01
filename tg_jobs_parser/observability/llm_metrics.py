"""Prometheus-метрики вызовов OpenRouter и AI-enrichment."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

LLM_REQUESTS = Counter(
    "llm_requests_total",
    "OpenRouter chat completion requests",
    labelnames=("model", "outcome"),
)

LLM_REQUEST_DURATION = Histogram(
    "llm_request_duration_seconds",
    "OpenRouter request wall time",
    labelnames=("model",),
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

LLM_TOKENS = Counter(
    "llm_tokens_total",
    "LLM tokens consumed",
    labelnames=("model", "direction"),
)

LLM_ENRICH_EVENTS = Counter(
    "llm_enrich_events_total",
    "AI enrichment pipeline events (cache, skips, errors)",
    labelnames=("event",),
)


def _norm_model(model: str) -> str:
    m = (model or "").strip()
    return m if m else "unknown"


def record_request(*, model: str, outcome: str, duration_seconds: float) -> None:
    mid = _norm_model(model)
    LLM_REQUESTS.labels(model=mid, outcome=outcome).inc()
    LLM_REQUEST_DURATION.labels(model=mid).observe(max(duration_seconds, 0.0))


def record_tokens(*, model: str, prompt_tokens: int, completion_tokens: int) -> None:
    mid = _norm_model(model)
    if prompt_tokens > 0:
        LLM_TOKENS.labels(model=mid, direction="input").inc(prompt_tokens)
    if completion_tokens > 0:
        LLM_TOKENS.labels(model=mid, direction="output").inc(completion_tokens)


def record_enrich_event(event: str) -> None:
    LLM_ENRICH_EVENTS.labels(event=event).inc()

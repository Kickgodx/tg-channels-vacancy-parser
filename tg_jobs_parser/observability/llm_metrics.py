"""Prometheus-метрики вызовов OpenRouter и AI-enrichment."""

from __future__ import annotations

from collections.abc import Iterable

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

LLM_RATE_GATE_WAIT = Histogram(
    "llm_rate_gate_wait_seconds",
    "Ожидание слота RPM-лимита перед запросом (локальный темп + общий Redis-лимит)",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

LLM_RETRIES = Counter(
    "llm_retries_total",
    "Повторы запроса к OpenRouter",
    labelnames=("model", "reason"),  # rate_limit | transient
)

LLM_COST_USD = Counter(
    "llm_cost_usd_total",
    "Оценка стоимости LLM-вызовов по ценам llm_models.yaml, USD",
    labelnames=("model",),
)

LLM_ENRICH_EVENTS = Counter(
    "llm_enrich_events_total",
    "AI enrichment pipeline events (cache, skips, errors, merged)",
    labelnames=("event",),
)

# Нули для известных лейблов: иначе первый прирост серии не виден increase()/rate()
# (подробнее — в parse_metrics). Лейбл model заранее неизвестен — см. init_model_series.
_OUTCOMES = ("success", "error", "parse_error", "rate_limited")
_RETRY_REASONS = ("rate_limit", "transient")
_ENRICH_EVENTS = (
    "cache_hit",
    "merged",
    "skipped_score",
    "skipped_limit",
    "rate_limit_skipped",
    "dropped_not_job",
    "error",
    "circuit_open",
)
for _event in _ENRICH_EVENTS:
    LLM_ENRICH_EVENTS.labels(event=_event)

LLM_ENRICH_DISABLED = Counter(
    "llm_enrich_disabled_total",
    "AI-разбор (прогон, LLM-импорт) перестал использовать LLM — раз за разбор",
    # circuit_breaker — ошибки подряд; quota — квота / дневной лимит провайдера / суточный
    # бюджет; init_failed — AI включён, но клиент не создан.
    labelnames=("reason",),
)
LLM_DISABLED_REASONS = ("circuit_breaker", "quota", "init_failed")
for _reason in LLM_DISABLED_REASONS:
    LLM_ENRICH_DISABLED.labels(reason=_reason)


def _norm_model(model: str) -> str:
    m = (model or "").strip()
    return m if m else "unknown"


def init_model_series(models: Iterable[str]) -> None:
    """Завести нулевые серии для моделей — до первого вызова, а не в момент него.

    Вызывается на старте процесса (модели из ``config.yaml``) и при создании клиента
    (модели снимка прогона). Весь каталог не заводим: панели «по модели» заполнились бы
    нулевыми линиями неиспользуемых моделей.
    """
    for model in models:
        if not (model or "").strip():
            continue
        mid = _norm_model(model)
        for outcome in _OUTCOMES:
            LLM_REQUESTS.labels(model=mid, outcome=outcome)
        LLM_REQUEST_DURATION.labels(model=mid)
        for direction in ("input", "output"):
            LLM_TOKENS.labels(model=mid, direction=direction)
        for reason in _RETRY_REASONS:
            LLM_RETRIES.labels(model=mid, reason=reason)
        LLM_COST_USD.labels(model=mid)


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


def record_rate_gate_wait(seconds: float) -> None:
    LLM_RATE_GATE_WAIT.observe(max(seconds, 0.0))


def record_retry(*, model: str, reason: str) -> None:
    LLM_RETRIES.labels(model=_norm_model(model), reason=reason).inc()


def record_cost(*, model: str, usd: float) -> None:
    if usd > 0:
        LLM_COST_USD.labels(model=_norm_model(model)).inc(usd)


def record_enrich_event(event: str) -> None:
    LLM_ENRICH_EVENTS.labels(event=event).inc()


def record_llm_disabled(reason: str) -> None:
    LLM_ENRICH_DISABLED.labels(reason=reason).inc()

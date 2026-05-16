"""Ограничение частоты запросов к OpenRouter (free tier)."""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from tg_jobs_parser.utils.logger import get_logger

_LOG = get_logger(__name__)

_RESET_MS_RE = re.compile(r"X-RateLimit-Reset['\"]?\s*[:=]\s*['\"]?(\d+)", re.I)
_REMAINING_RE = re.compile(
    r"X-RateLimit-Remaining['\"]?\s*[:=]\s*['\"]?(\d+)", re.I
)


class LlmQuotaExhausted(RuntimeError):
    """Квота LLM исчерпана — до конца прогона только rule-based разбор."""


def extract_rate_limit_headers(error: Exception) -> dict[str, str]:
    """Достаёт заголовки X-RateLimit-* из тела/ответа OpenAI SDK или текста ошибки."""
    out: dict[str, str] = {}
    msg = str(error)

    for pattern, key in (
        (_REMAINING_RE, "X-RateLimit-Remaining"),
        (_RESET_MS_RE, "X-RateLimit-Reset"),
    ):
        m = pattern.search(msg)
        if m:
            out[key] = m.group(1)

    body = getattr(error, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            meta = err.get("metadata")
            if isinstance(meta, dict):
                headers = meta.get("headers")
                if isinstance(headers, dict):
                    for k, v in headers.items():
                        out[str(k)] = str(v)

    response = getattr(error, "response", None)
    if response is not None:
        hdrs = getattr(response, "headers", None)
        if hdrs is not None:
            try:
                items = hdrs.items() if hasattr(hdrs, "items") else hdrs
                for k, v in items:
                    lk = str(k).lower()
                    if "ratelimit" in lk:
                        out[str(k)] = str(v)
            except (TypeError, AttributeError):
                pass

    return out


class OpenRouterRateLimiter:
    """
    Сериализация запросов и отключение LLM при исчерпании квоты.

    OpenRouter free (из 429):
    - ``free-models-per-min`` — ~16 req/min;
    - ``free-models-per-day`` — ~50 req/day.
    """

    def __init__(
        self,
        *,
        requests_per_minute: float,
        max_retries_on_429: int,
        retry_base_delay_seconds: float,
        stop_llm_when_rate_limited: bool = True,
    ) -> None:
        self._rpm = max(0.1, requests_per_minute)
        self._min_interval = 60.0 / self._rpm
        self._max_retries = max(0, max_retries_on_429)
        self._base_delay = max(0.5, retry_base_delay_seconds)
        self.stop_llm_when_rate_limited = stop_llm_when_rate_limited
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self.llm_disabled = False
        self.llm_disabled_reason: str | None = None
        self.llm_disabled_until_epoch: float | None = None
        self.total_waits_seconds: float = 0.0
        self.rate_limit_retries: int = 0

    @property
    def daily_limit_exceeded(self) -> bool:
        return self.llm_disabled

    def disable_llm_for_run(
        self,
        reason: str,
        *,
        reset_epoch_ms: int | None = None,
    ) -> None:
        """Прекратить LLM-запросы до конца текущего прогона (rules продолжают работать)."""
        with self._lock:
            self.llm_disabled = True
            self.llm_disabled_reason = reason
            if reset_epoch_ms and reset_epoch_ms > 1_000_000_000_000:
                self.llm_disabled_until_epoch = reset_epoch_ms / 1000.0
            elif reset_epoch_ms and reset_epoch_ms > 1_000_000_000:
                self.llm_disabled_until_epoch = float(reset_epoch_ms)

        until_txt = ""
        if self.llm_disabled_until_epoch:
            until_txt = datetime.fromtimestamp(
                self.llm_disabled_until_epoch, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
        _LOG.error(
            "OpenRouter: LLM отключён до конца прогона (%s). %s "
            "Дальнейшие посты — только rule-based извлечение.",
            reason,
            f"Сброс лимита ориентировочно: {until_txt}. " if until_txt else "",
        )

    def apply_error(self, error: Exception) -> None:
        """
        Разбор 429: при Remaining=0 или дневном лимите — отключить LLM без повторов.
        """
        headers = extract_rate_limit_headers(error)
        msg = str(error).lower()
        remaining_raw = headers.get("X-RateLimit-Remaining") or headers.get(
            "x-ratelimit-remaining"
        )
        reset_raw = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        reset_ms = int(reset_raw) if reset_raw and str(reset_raw).isdigit() else None

        exhausted = (
            "per-day" in msg
            or "per day" in msg
            or (remaining_raw is not None and int(remaining_raw) <= 0)
        )

        if exhausted and self.stop_llm_when_rate_limited:
            label = "дневной лимит" if "per-day" in msg or "per day" in msg else "квота (Remaining=0)"
            self.disable_llm_for_run(
                f"{label}; X-RateLimit-Reset={reset_raw}",
                reset_epoch_ms=reset_ms,
            )
            raise LlmQuotaExhausted(self.llm_disabled_reason or "rate limit") from error

        if "per-day" in msg or "per day" in msg:
            self.disable_llm_for_run("дневной лимит free-моделей", reset_epoch_ms=reset_ms)
            raise LlmQuotaExhausted(self.llm_disabled_reason or "daily limit") from error

    def acquire(self) -> None:
        if self.llm_disabled:
            raise LlmQuotaExhausted(
                self.llm_disabled_reason or "LLM отключён (rate limit)"
            )
        with self._lock:
            now = time.monotonic()
            delay = self._min_interval - (now - self._last_request_at)
            if delay > 0:
                time.sleep(delay)
                self.total_waits_seconds += delay
            self._last_request_at = time.monotonic()

    def wait_after_429(self, error: Exception) -> None:
        """Краткая пауза перед повтором (только если LLM ещё не отключён)."""
        self.apply_error(error)

        reset_ms = None
        headers = extract_rate_limit_headers(error)
        reset_raw = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        if reset_raw and str(reset_raw).isdigit():
            reset_ms = int(reset_raw)
        if reset_ms and reset_ms > 1_000_000_000_000:
            wait = max(self._base_delay, reset_ms / 1000.0 - time.time() + 0.5)
        else:
            wait = self._base_delay
        wait = min(wait, 120.0)
        self.rate_limit_retries += 1
        _LOG.warning("OpenRouter 429: пауза %.1f с перед повтором.", wait)
        time.sleep(wait)
        self.total_waits_seconds += wait

    @property
    def max_retries(self) -> int:
        return self._max_retries

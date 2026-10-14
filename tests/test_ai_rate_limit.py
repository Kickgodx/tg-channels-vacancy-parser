"""Тесты rate limiter OpenRouter."""

import time

import pytest

from tg_jobs_parser.ai.rate_limit import (
    LlmQuotaExhausted,
    OpenRouterRateLimiter,
    extract_rate_limit_headers,
)


def test_acquire_serializes_requests():
    rl = OpenRouterRateLimiter(
        requests_per_minute=120.0,
        max_retries_on_429=0,
        retry_base_delay_seconds=1.0,
    )
    t0 = time.monotonic()
    rl.acquire()
    rl.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.4


def _remaining_zero_error(reset_ms: int) -> Exception:
    return Exception(
        "429 {'error': {'metadata': {'headers': {"
        "'X-RateLimit-Limit': '50', 'X-RateLimit-Remaining': '0', "
        f"'X-RateLimit-Reset': '{reset_ms}'}}}}}}"
    )


def test_disable_on_remaining_zero():
    rl = OpenRouterRateLimiter(
        requests_per_minute=60.0,
        max_retries_on_429=0,
        retry_base_delay_seconds=1.0,
    )
    with pytest.raises(LlmQuotaExhausted):
        rl.apply_error(_remaining_zero_error(int((time.time() + 3600) * 1000)))
    assert rl.llm_disabled
    with pytest.raises(LlmQuotaExhausted):
        rl.acquire()


def test_reenabled_after_reset_time_passes():
    """Долгоживущий лимитер (интерактивный клиент) не остаётся выключенным до рестарта."""
    rl = OpenRouterRateLimiter(requests_per_minute=600.0, max_retries_on_429=0, retry_base_delay_seconds=1.0)
    with pytest.raises(LlmQuotaExhausted):
        rl.apply_error(_remaining_zero_error(int((time.time() - 1) * 1000)))
    assert rl.llm_disabled
    rl.acquire()
    assert not rl.llm_disabled


def test_extract_headers_from_error():
    err = Exception("{'metadata': {'headers': {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1778976000000'}}}")
    h = extract_rate_limit_headers(err)
    assert h.get("X-RateLimit-Remaining") == "0"
    assert h.get("X-RateLimit-Reset") == "1778976000000"

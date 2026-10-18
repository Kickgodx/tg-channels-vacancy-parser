"""Ограничение частоты запросов к OpenRouter."""

from __future__ import annotations

import random
import re
import threading
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from typing import Protocol

from tg_jobs_parser.utils.logger import get_logger

_LOG = get_logger(__name__)

_TRANSIENT_BACKOFF_CAP_S = 30.0
# Один API-ключ OpenRouter на все процессы — один общий счётчик.
_GLOBAL_RATE_KEY = "openrouter"

_RESET_MS_RE = re.compile(r"X-RateLimit-Reset['\"]?\s*[:=]\s*['\"]?(\d+)", re.I)
_REMAINING_RE = re.compile(r"X-RateLimit-Remaining['\"]?\s*[:=]\s*['\"]?(\d+)", re.I)


class LlmQuotaExhausted(RuntimeError):
    """Квота LLM исчерпана — до конца прогона только rule-based разбор."""


class GlobalRateGate(Protocol):
    """Порт общего (между процессами) лимита запросов к провайдеру LLM.

    Локальный ``OpenRouterRateLimiter`` разносит запросы только внутри процесса:
    N воркеров дают N × RPM. Backend регистрирует реализацию на Redis
    (``register_global_rate_gate``); ядро о Redis не знает.
    """

    def wait_seconds(self, key: str, per_minute: float) -> float:
        """Занять слот под запрос. 0 — можно идти сейчас, иначе через сколько секунд повторить."""
        ...

    def take_daily(self, key: str, budget: int) -> bool:
        """Списать запрос из суточного (UTC) бюджета ``budget``. False — бюджет на сегодня исчерпан."""
        ...

    def daily_used(self, key: str) -> int:
        """Сколько запросов суточного бюджета уже списано сегодня (без списания)."""
        ...


class _LocalDailyBudget:
    """Суточный бюджет внутри процесса — когда общего хранилища нет или оно недоступно."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day: date | None = None
        self._used = 0

    def take(self, budget: int) -> bool:
        today = datetime.now(UTC).date()
        with self._lock:
            if self._day != today:
                self._day, self._used = today, 0
            if self._used >= budget:
                return False
            self._used += 1
            return True

    def used(self) -> int:
        with self._lock:
            return self._used if self._day == datetime.now(UTC).date() else 0


_local_daily_budget = _LocalDailyBudget()


def interactive_daily_budget_exhausted(budget: int) -> bool:
    """Исчерпан ли на сегодня суточный бюджет ``budget`` — без списания запроса.

    Проверка при постановке задачи: тот же счётчик, что у ``OpenRouterRateLimiter`` —
    общий через порт, а без него или при его сбое — счётчик процесса.
    """
    if budget <= 0:
        return False
    gate = _global_gate
    if gate is not None:
        try:
            return int(gate.daily_used(_GLOBAL_RATE_KEY)) >= budget
        except Exception as e:
            _LOG.debug("Общий суточный бюджет LLM недоступен для проверки: %s", e)
    return _local_daily_budget.used() >= budget


def _next_utc_midnight_ms() -> int:
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    return int(datetime.combine(tomorrow, dt_time.min, tzinfo=UTC).timestamp() * 1000)


_global_gate: GlobalRateGate | None = None
# Шаг ожидания общего слота: между шагами проверяется отключение LLM.
_GLOBAL_WAIT_STEP_S = 5.0


def register_global_rate_gate(gate: GlobalRateGate | None) -> None:
    """Включить общий лимит для всех ``OpenRouterRateLimiter`` процесса (``None`` — выключить)."""
    global _global_gate
    _global_gate = gate


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

    При 429 учитываются заголовки ``X-RateLimit-*`` и текст ошибки (в т.ч. дневная квота).
    """

    def __init__(
        self,
        *,
        requests_per_minute: float,
        max_retries_on_429: int,
        retry_base_delay_seconds: float,
        stop_llm_when_rate_limited: bool = True,
        daily_request_budget: int = 0,  # 0 — без суточного бюджета (парсинг)
    ) -> None:
        self._rpm = max(0.1, requests_per_minute)
        self._daily_budget = max(0, daily_request_budget)
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
        self._global_gate_failed = False

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
            until_txt = datetime.fromtimestamp(self.llm_disabled_until_epoch, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        _LOG.error(
            "OpenRouter: LLM отключён до конца прогона (%s). %s Дальнейшие посты — только rule-based извлечение.",
            reason,
            f"Сброс лимита ориентировочно: {until_txt}. " if until_txt else "",
        )

    def apply_error(self, error: Exception) -> None:
        """
        Разбор 429: при Remaining=0 или дневном лимите — отключить LLM без повторов.
        """
        headers = extract_rate_limit_headers(error)
        msg = str(error).lower()
        remaining_raw = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
        reset_raw = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        reset_ms = int(reset_raw) if reset_raw and str(reset_raw).isdigit() else None

        exhausted = "per-day" in msg or "per day" in msg or (remaining_raw is not None and int(remaining_raw) <= 0)

        if exhausted and self.stop_llm_when_rate_limited:
            label = "дневной лимит" if "per-day" in msg or "per day" in msg else "квота (Remaining=0)"
            self.disable_llm_for_run(
                f"{label}; X-RateLimit-Reset={reset_raw}",
                reset_epoch_ms=reset_ms,
            )
            raise LlmQuotaExhausted(self.llm_disabled_reason or "rate limit") from error

        if "per-day" in msg or "per day" in msg:
            self.disable_llm_for_run("дневной лимит API", reset_epoch_ms=reset_ms)
            raise LlmQuotaExhausted(self.llm_disabled_reason or "daily limit") from error

    def _reenable_if_reset_passed(self) -> None:
        """Лимит со временем сброса (дневная квота, суточный бюджет) после него снова пускает в LLM.

        Без этого долгоживущий лимитер (интерактивный клиент API) оставался бы выключенным
        до рестарта процесса.
        """
        until = self.llm_disabled_until_epoch
        if not self.llm_disabled or until is None or time.time() < until:
            return
        with self._lock:
            self.llm_disabled = False
            self.llm_disabled_until_epoch = None
        _LOG.info("OpenRouter: время сброса лимита прошло — LLM снова включён.")

    def _take_daily_budget(self) -> None:
        if self._daily_budget <= 0:
            return
        allowed: bool | None = None
        gate = _global_gate
        if gate is not None:
            try:
                allowed = gate.take_daily(_GLOBAL_RATE_KEY, self._daily_budget)
            except Exception as e:
                if not self._global_gate_failed:
                    self._global_gate_failed = True
                    _LOG.warning("Общий суточный бюджет LLM недоступен (%s) — считаем в процессе.", e)
        if allowed is None:
            allowed = _local_daily_budget.take(self._daily_budget)
        if allowed:
            return
        self.disable_llm_for_run(
            f"суточный бюджет запросов исчерпан ({self._daily_budget} в сутки)",
            reset_epoch_ms=_next_utc_midnight_ms(),
        )
        raise LlmQuotaExhausted(self.llm_disabled_reason or "daily budget")

    def acquire(self) -> None:
        self._reenable_if_reset_passed()
        if self.llm_disabled:
            raise LlmQuotaExhausted(self.llm_disabled_reason or "LLM отключён (rate limit)")
        with self._lock:
            now = time.monotonic()
            delay = self._min_interval - (now - self._last_request_at)
            if delay > 0:
                time.sleep(delay)
                self.total_waits_seconds += delay
            self._last_request_at = time.monotonic()
        self._acquire_global_slot()
        self._take_daily_budget()

    def _acquire_global_slot(self) -> None:
        """Слот общего лимита (если backend его зарегистрировал). Сбой хранилища — не блокирует запрос."""
        gate = _global_gate
        if gate is None:
            return
        while True:
            try:
                wait = gate.wait_seconds(_GLOBAL_RATE_KEY, self._rpm)
            except Exception as e:
                if not self._global_gate_failed:
                    self._global_gate_failed = True
                    _LOG.warning("Общий лимит LLM недоступен (%s) — только локальный лимит процесса.", e)
                return
            self._global_gate_failed = False
            if wait <= 0:
                return
            if self.llm_disabled:
                raise LlmQuotaExhausted(self.llm_disabled_reason or "LLM отключён (rate limit)")
            step = min(wait, _GLOBAL_WAIT_STEP_S)
            time.sleep(step)
            self.total_waits_seconds += step

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

    def wait_after_transient_error(self, attempt: int, error: Exception) -> None:
        """Backoff перед повтором после 5xx/таймаута: 2, 4, 8… с (не дольше 30) плюс jitter."""
        wait = min(2.0 * 2 ** max(0, attempt - 1), _TRANSIENT_BACKOFF_CAP_S)
        wait += random.uniform(0, wait * 0.25)
        _LOG.warning("OpenRouter: сбой (%s), повтор %s через %.1f с.", error, attempt, wait)
        time.sleep(wait)
        self.total_waits_seconds += wait

    @property
    def max_retries(self) -> int:
        return self._max_retries

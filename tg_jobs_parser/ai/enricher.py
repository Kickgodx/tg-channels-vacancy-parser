"""Вызов LLM и обогащение Vacancy."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from tg_jobs_parser.ai.cache import AIExtractionCache
from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient
from tg_jobs_parser.ai.rate_limit import LlmQuotaExhausted
from tg_jobs_parser.ai.merger import merge_rules_and_ai
from tg_jobs_parser.ai.search_context import SearchContext, build_search_context
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.logger import get_logger

_LOG = get_logger(__name__)


@dataclass
class RunAIStats:
    calls: int = 0
    cache_hits: int = 0
    errors: int = 0
    dropped_not_job: int = 0
    skipped_score: int = 0
    skipped_limit: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    rate_limit_skipped: int = 0
    llm_disabled_mid_run: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out

    def format_token_summary(self) -> str:
        """Строка для лога / UI: суммарные токены за текущий прогон."""
        return (
            f"Токены LLM за прогон: input={self.tokens_in}, "
            f"output={self.tokens_out}, всего={self.tokens_total}"
        )

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "errors": self.errors,
            "dropped_not_job": self.dropped_not_job,
            "skipped_score": self.skipped_score,
            "skipped_limit": self.skipped_limit,
            "rate_limit_skipped": self.rate_limit_skipped,
            "llm_disabled_mid_run": self.llm_disabled_mid_run,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_total,
        }


class AIEnrichmentService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._ai = config.ai
        self._context = build_search_context(config)
        self._client = OpenRouterClient(self._ai)
        self._cache = AIExtractionCache(
            Path(self._ai.cache_path),
            enabled=self._ai.use_cache,
        )
        self.stats = RunAIStats()
        self._progress_total: int = 0
        self._progress_done: int = 0
        self._daily_limit_hit = False

    def close(self) -> None:
        self._client.close()

    def begin_batch(self, total_posts: int) -> None:
        """Задать размер батча для логов вида ``AI LLM: 100/1500``."""
        self._progress_total = max(0, total_posts)
        self._progress_done = 0
        if self._progress_total:
            _LOG.info(
                "AI LLM: старт %s постов | модель %s | кэш %s | concurrency=%s | ~%.0f req/min.",
                self._progress_total,
                self._ai.model,
                "вкл" if self._ai.use_cache else "выкл",
                self._ai.concurrency,
                self._ai.requests_per_minute,
            )

    def _log_llm_progress(self, vacancy: Vacancy, *, phase: str) -> None:
        if self._progress_total <= 0:
            return
        with self.stats._lock:
            self._progress_done += 1
            current = self._progress_done
            total = self._progress_total
        _LOG.info(
            "AI LLM: %s/%s — @%s [%s]",
            current,
            total,
            vacancy.source_channel,
            phase,
        )

    def enrich(self, vacancy: Vacancy) -> Vacancy | None:
        """LLM-разбор; None если пост не вакансия или лимит/ошибка с drop."""
        if vacancy.vacancy_score < self._ai.skip_if_vacancy_score_below:
            with self.stats._lock:
                self.stats.skipped_score += 1
            return None

        with self.stats._lock:
            if self._ai.max_posts_per_run > 0 and self.stats.calls >= self._ai.max_posts_per_run:
                self.stats.skipped_limit += 1
                return vacancy

        text = (vacancy.cleaned_text or vacancy.raw_text or "")[: self._ai.max_input_chars]
        cached = self._cache.get(
            text=text,
            model=self._ai.model,
            prompt_version=self._ai.prompt_version,
            mode=self._ai.mode,
        )
        if cached is not None:
            with self.stats._lock:
                self.stats.cache_hits += 1
            extraction = cached
            self._log_llm_progress(
                vacancy,
                phase=f"кэш ({self._ai.model})",
            )
        else:
            if self._daily_limit_hit or self._client.rate_limiter.llm_disabled:
                self._daily_limit_hit = True
                with self.stats._lock:
                    self.stats.rate_limit_skipped += 1
                return vacancy if self._ai.mode == "assist" else None

            with self.stats._lock:
                self.stats.calls += 1
            self._log_llm_progress(vacancy, phase="запрос API")
            try:
                extraction, usage = self._client.extract_with_fallback(
                    text=text,
                    search_context=self._context,
                )
            except LlmQuotaExhausted:
                self._daily_limit_hit = True
                with self.stats._lock:
                    self.stats.rate_limit_skipped += 1
                    self.stats.llm_disabled_mid_run = True
                return vacancy if self._ai.mode == "assist" else None
            except Exception as e:
                with self.stats._lock:
                    self.stats.errors += 1
                _LOG.warning("AI ошибка для %s: %s", vacancy.id, e)
                if self._ai.mode == "primary":
                    return None
                return vacancy
            with self.stats._lock:
                self.stats.tokens_in += usage.prompt_tokens
                self.stats.tokens_out += usage.completion_tokens
            cache_model = (usage.model or self._ai.model).strip() or self._ai.model
            self._cache.put(
                text=text,
                model=cache_model,
                prompt_version=self._ai.prompt_version,
                mode=self._ai.mode,
                extraction=extraction,
            )
            _LOG.info(
                "AI LLM: ответ получен — @%s (токены in/out: %s/%s)",
                vacancy.source_channel,
                usage.prompt_tokens,
                usage.completion_tokens,
            )

        if not extraction.is_job_post:
            with self.stats._lock:
                self.stats.dropped_not_job += 1
            _LOG.info(
                "AI LLM: @%s — не вакансия%s",
                vacancy.source_channel,
                f" ({extraction.rejection_reason})" if extraction.rejection_reason else "",
            )
            return None

        merged = merge_rules_and_ai(vacancy, extraction, self._config)
        threshold = self._config.parser.vacancy_score_threshold
        if merged.vacancy_score < threshold:
            merged = merged.model_copy(update={"vacancy_score": threshold})
        return merged

    def log_batch_finished(self) -> None:
        if self._progress_total <= 0:
            return
        rl = self._client.rate_limiter
        _LOG.info(
            "AI LLM: завершено %s/%s | API %s | кэш %s | пропуск score<%s: %s | "
            "не вакансии %s | ошибок %s | пропуск (rate-limit) %s | паузы %.0f с | 429-retries %s.",
            self._progress_done,
            self._progress_total,
            self.stats.calls,
            self.stats.cache_hits,
            self._ai.skip_if_vacancy_score_below,
            self.stats.skipped_score,
            self.stats.dropped_not_job,
            self.stats.errors,
            self.stats.rate_limit_skipped,
            rl.total_waits_seconds,
            rl.rate_limit_retries,
        )
        _LOG.info(self.stats.format_token_summary())


def enrich_vacancy_with_ai(
    vacancy: Vacancy,
    service: AIEnrichmentService,
) -> Vacancy | None:
    return service.enrich(vacancy)

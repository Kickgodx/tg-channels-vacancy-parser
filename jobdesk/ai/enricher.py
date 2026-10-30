"""Вызов LLM и обогащение Vacancy."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from jobdesk.ai.cache import AIExtractionCache, AIExtractionCacheBackend
from jobdesk.ai.clients.openrouter import OpenRouterClient
from jobdesk.ai.merger import merge_rules_and_ai
from jobdesk.ai.models_catalog import LlmModelEntry
from jobdesk.ai.prompts import resolve_extract_prompt_version
from jobdesk.ai.rate_limit import LlmQuotaExhausted
from jobdesk.ai.search_context import build_search_context
from jobdesk.ai.token_cost import TokenCostEstimate, estimate_run_cost
from jobdesk.config.schema import AppConfig
from jobdesk.models.vacancy import Vacancy
from jobdesk.observability.llm_metrics import record_enrich_event, record_llm_disabled
from jobdesk.parse_cancel import ParseCancellationToken, ParseCancelled
from jobdesk.utils.logger import get_logger

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
    _tokens_by_model: dict[str, list[int]] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out

    def record_api_tokens(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Учёт токенов одного API-вызова (не кэш), с разбивкой по модели."""
        mid = (model or "").strip() or "unknown"
        with self._lock:
            self.tokens_in += prompt_tokens
            self.tokens_out += completion_tokens
            bucket = self._tokens_by_model.setdefault(mid, [0, 0])
            bucket[0] += prompt_tokens
            bucket[1] += completion_tokens

    def tokens_by_model(self) -> dict[str, tuple[int, int]]:
        with self._lock:
            return {m: (v[0], v[1]) for m, v in self._tokens_by_model.items()}

    def estimate_cost(
        self,
        catalog: list[LlmModelEntry],
        primary_model: str,
    ) -> TokenCostEstimate | None:
        return estimate_run_cost(
            tokens_by_model=self.tokens_by_model(),
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            catalog=catalog,
            primary_model=primary_model,
        )

    def format_token_summary(self) -> str:
        """Строка для лога / UI: суммарные токены за текущий прогон."""
        return f"Токены LLM за прогон: input={self.tokens_in}, output={self.tokens_out}, всего={self.tokens_total}"

    def format_cost_summary(
        self,
        catalog: list[LlmModelEntry],
        primary_model: str,
    ) -> str | None:
        est = self.estimate_cost(catalog, primary_model)
        return est.format_line() if est else None

    def to_dict(
        self,
        *,
        catalog: list[LlmModelEntry] | None = None,
        primary_model: str = "",
    ) -> dict:
        data = {
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
        if catalog:
            est = self.estimate_cost(catalog, primary_model)
            if est:
                data.update(est.to_dict())
        return data


class LlmUnavailableError(RuntimeError):
    """LLM не удалось запустить там, где без него нельзя (``ai.mode: primary``, LLM-импорт).

    Причина детерминированная (ключ, модель, кэш), поэтому задача завершается ошибкой,
    а не откатывается на правила и не повторяется.
    """


class AIEnrichmentService:
    def __init__(
        self,
        config: AppConfig,
        *,
        cache: AIExtractionCacheBackend | None = None,
    ) -> None:
        self._config = config
        self._ai = config.ai
        self._context = build_search_context(config)
        self._client = OpenRouterClient(self._ai, catalog=config.llm_model_catalog)
        self._cache = AIExtractionCache(self._ai, backend=cache)
        self.stats = RunAIStats()
        self._progress_total: int = 0
        self._progress_done: int = 0
        self._daily_limit_hit = False
        self._consecutive_errors = 0

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

    def add_to_batch(self, posts: int) -> None:
        """Потоковый прогон: размер батча для логов растёт по мере вычитки каналов."""
        with self.stats._lock:
            self._progress_total += max(0, posts)

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

    def enrich(
        self,
        vacancy: Vacancy,
        *,
        cancel: ParseCancellationToken | None = None,
    ) -> Vacancy | None:
        """LLM-разбор; None если пост не вакансия или лимит/ошибка с drop."""
        if cancel and cancel.is_cancelled():
            raise ParseCancelled()
        if vacancy.vacancy_score < self._ai.skip_if_vacancy_score_below:
            with self.stats._lock:
                self.stats.skipped_score += 1
            record_enrich_event("skipped_score")
            return None

        # Потолок ai.max_posts_per_run проверяется после кэша: он ограничивает вызовы,
        # а уже оплаченный ответ из кэша используется всегда.
        text = (vacancy.cleaned_text or vacancy.raw_text or "")[: self._ai.max_input_chars]
        prompt_version = resolve_extract_prompt_version(self._ai.prompt_version)
        cached = self._cache.get(
            text=text,
            model=self._ai.model,
            prompt_version=prompt_version,
            mode=self._ai.mode,
        )
        if cached is not None:
            with self.stats._lock:
                self.stats.cache_hits += 1
            record_enrich_event("cache_hit")
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
                record_enrich_event("rate_limit_skipped")
                return vacancy if self._ai.mode == "assist" else None

            if cancel and cancel.is_cancelled():
                raise ParseCancelled()
            # Проверка потолка и резерв вызова — одной секцией: иначе параллельные
            # потоки (ai.concurrency > 1) проскакивали бы потолок.
            with self.stats._lock:
                limit = self._ai.max_posts_per_run
                limit_hit = limit > 0 and self.stats.calls >= limit
                if limit_hit:
                    self.stats.skipped_limit += 1
                else:
                    self.stats.calls += 1
            if limit_hit:
                record_enrich_event("skipped_limit")
                # primary без ответа LLM пост не сохраняет — он потерян, прогон неполный.
                return vacancy if self._ai.mode == "assist" else None
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
                self._mark_llm_disabled("quota")
                record_enrich_event("rate_limit_skipped")
                return vacancy if self._ai.mode == "assist" else None
            except Exception as e:
                with self.stats._lock:
                    self.stats.errors += 1
                    self._consecutive_errors += 1
                    consecutive = self._consecutive_errors
                record_enrich_event("error")
                _LOG.warning("AI ошибка для %s: %s", vacancy.id, e)
                self._trip_breaker_if_needed(consecutive, e)
                if self._ai.mode == "primary":
                    return None
                return vacancy
            with self.stats._lock:
                self._consecutive_errors = 0
            cache_model = (usage.model or self._ai.model).strip() or self._ai.model
            self.stats.record_api_tokens(
                cache_model,
                usage.prompt_tokens,
                usage.completion_tokens,
            )
            # Ответ fallback-модели — под её ключом: иначе он на весь TTL выдавался бы
            # за ответ основной, а следующий прогон не спросил бы основную модель заново.
            self._cache.put(
                text=text,
                model=cache_model,
                prompt_version=prompt_version,
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
            record_enrich_event("dropped_not_job")
            _LOG.info(
                "AI LLM: @%s — не вакансия%s",
                vacancy.source_channel,
                f" ({extraction.rejection_reason})" if extraction.rejection_reason else "",
            )
            return None

        merged = merge_rules_and_ai(vacancy, extraction, self._config)
        # Вердикт LLM «вакансия» главнее эвристик правил: оценки поднимаются до порогов
        # пост-фильтра, иначе оплаченный ответ отбросил бы порог правил.
        parser = self._config.parser
        raised = {}
        if merged.vacancy_score < parser.vacancy_score_threshold:
            raised["vacancy_score"] = parser.vacancy_score_threshold
        if merged.vacancy_substantive_score < parser.vacancy_min_substantive_score:
            raised["vacancy_substantive_score"] = parser.vacancy_min_substantive_score
        if raised:
            merged = merged.model_copy(update=raised)
        record_enrich_event("merged")
        return merged

    def _trip_breaker_if_needed(self, consecutive: int, error: Exception) -> None:
        """Circuit breaker: при полном отказе провайдера каждый пост ждал бы повторов и таймаутов.

        После ``ai.circuit_breaker_errors`` ошибок подряд LLM отключается до конца прогона —
        дальше тот же путь, что при исчерпанной квоте: посты идут без LLM (assist) или
        пропускаются, а прогон помечается неполным (primary).
        """
        threshold = self._ai.circuit_breaker_errors
        limiter = self._client.rate_limiter
        if threshold <= 0 or consecutive < threshold or limiter.llm_disabled:
            return
        detail = str(error)
        if len(detail) > 200:
            detail = detail[:199] + "…"
        limiter.disable_llm_for_run(f"circuit breaker: {consecutive} ошибок LLM подряд, последняя: {detail}")
        self._mark_llm_disabled("circuit_breaker")
        record_enrich_event("circuit_open")

    def _mark_llm_disabled(self, reason: str) -> None:
        """Первое отключение LLM за разбор — в метрику (алерт ``LlmDisabled``); повторы — нет."""
        with self.stats._lock:
            if self.stats.llm_disabled_mid_run:
                return
            self.stats.llm_disabled_mid_run = True
        record_llm_disabled(reason)

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
        cost_line = self.stats.format_cost_summary(
            self._config.llm_model_catalog,
            self._ai.model,
        )
        if cost_line:
            _LOG.info(cost_line)

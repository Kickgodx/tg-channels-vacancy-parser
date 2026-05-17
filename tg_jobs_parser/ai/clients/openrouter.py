"""Клиент OpenRouter через официальный OpenAI SDK (OpenAI-compatible API)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from tg_jobs_parser.ai.prompts import build_messages
from tg_jobs_parser.ai.rate_limit import (
    LlmQuotaExhausted,
    OpenRouterRateLimiter,
    extract_rate_limit_headers,
)
from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.ai.search_context import SearchContext
from tg_jobs_parser.config.schema import AiConfig
from tg_jobs_parser.utils.logger import get_logger

_LOG = get_logger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.I)


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


def _extract_json_text(content: str) -> str:
    content = content.strip()
    m = _JSON_FENCE_RE.search(content)
    if m:
        return m.group(1).strip()
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        return content[start : end + 1]
    return content


def _parse_extraction(raw: str) -> VacancyAIExtraction:
    text = _extract_json_text(raw)
    data = json.loads(text)
    return VacancyAIExtraction.model_validate(data)


def _message_content(message: Any) -> str:
    content = getattr(message, "content", None) or ""
    if content:
        return str(content)
    reasoning = getattr(message, "reasoning", None)
    if reasoning:
        return str(reasoning)
    return ""


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg


class OpenRouterClient:
    """OpenRouter (OpenAI-compatible). Клиент — ``openai`` SDK; ``poetry install --extras ai``."""

    def __init__(self, ai: AiConfig, rate_limiter: OpenRouterRateLimiter | None = None) -> None:
        self._ai = ai
        self._api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not self._api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY не задан. Добавьте ключ в .env (см. .env.example)."
            )
        self._rate_limiter = rate_limiter or OpenRouterRateLimiter(
            requests_per_minute=ai.requests_per_minute,
            max_retries_on_429=ai.max_retries_on_429,
            retry_base_delay_seconds=ai.retry_base_delay_seconds,
            stop_llm_when_rate_limited=ai.stop_llm_when_rate_limited,
        )
        self._sdk: Any | None = None

    @property
    def rate_limiter(self) -> OpenRouterRateLimiter:
        return self._rate_limiter

    def _get_sdk(self) -> Any:
        if self._sdk is None:
            self._sdk = self._build_sdk_client()
        return self._sdk

    def _build_sdk_client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "Для AI нужен пакет openai: poetry install --extras ai"
            ) from e

        default_headers: dict[str, str] = {}
        referer = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
        if referer:
            default_headers["HTTP-Referer"] = referer
        title = os.getenv("OPENROUTER_APP_TITLE", "tg-jobs-parser").strip()
        if title:
            default_headers["X-Title"] = title

        return OpenAI(
            base_url=self._ai.base_url.rstrip("/"),
            api_key=self._api_key,
            timeout=self._ai.request_timeout_seconds,
            max_retries=0,
            default_headers=default_headers or None,
        )

    def _create_completion(self, kwargs: dict[str, Any]) -> Any:
        if self._rate_limiter.llm_disabled:
            raise LlmQuotaExhausted(
                self._rate_limiter.llm_disabled_reason or "LLM отключён"
            )

        attempts = self._rate_limiter.max_retries + 1
        last_err: Exception | None = None
        for attempt in range(attempts):
            try:
                self._rate_limiter.acquire()
                return self._get_sdk().chat.completions.create(**kwargs)
            except LlmQuotaExhausted:
                raise
            except Exception as e:
                last_err = e
                if not _is_rate_limit_error(e):
                    raise RuntimeError(f"OpenRouter API: {e}") from e

                headers = extract_rate_limit_headers(e)
                rem = headers.get("X-RateLimit-Remaining") or headers.get(
                    "x-ratelimit-remaining"
                )
                if self._ai.stop_llm_when_rate_limited and (
                    rem is not None and int(rem) <= 0
                    or "per-day" in str(e).lower()
                ):
                    try:
                        self._rate_limiter.apply_error(e)
                    except LlmQuotaExhausted:
                        raise

                if attempt < attempts - 1 and not self._rate_limiter.llm_disabled:
                    try:
                        self._rate_limiter.wait_after_429(e)
                    except LlmQuotaExhausted:
                        raise
                    continue

                if self._ai.stop_llm_when_rate_limited:
                    try:
                        self._rate_limiter.apply_error(e)
                    except LlmQuotaExhausted:
                        raise

                raise RuntimeError(f"OpenRouter API: {e}") from e
        raise RuntimeError(f"OpenRouter API: {last_err}")

    def extract(
        self,
        *,
        text: str,
        search_context: SearchContext,
        model: str | None = None,
    ) -> tuple[VacancyAIExtraction, LLMUsage]:
        use_model = model or self._ai.model
        messages = build_messages(
            text=text,
            search_context=search_context,
            use_few_shot=self._ai.use_few_shot,
        )

        kwargs: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": self._ai.temperature,
            "max_tokens": self._ai.max_completion_tokens,
        }
        extra_body: dict[str, Any] | None = None
        if self._ai.reasoning_enabled:
            extra_body = {"reasoning": {"enabled": True}}
        else:
            kwargs["response_format"] = {"type": "json_object"}

        if extra_body:
            kwargs["extra_body"] = extra_body

        response = self._create_completion(kwargs)

        if not response.choices:
            raise RuntimeError("OpenRouter: пустой choices в ответе")

        message = response.choices[0].message
        content = _message_content(message)
        if not content:
            raise RuntimeError("OpenRouter: пустой ответ модели")

        usage_raw = response.usage
        usage = LLMUsage(
            prompt_tokens=int(getattr(usage_raw, "prompt_tokens", None) or 0),
            completion_tokens=int(getattr(usage_raw, "completion_tokens", None) or 0),
            model=use_model,
        )
        try:
            extraction = _parse_extraction(content)
        except (json.JSONDecodeError, ValueError) as e:
            _LOG.warning("Не удалось разобрать JSON от %s: %s", use_model, e)
            raise
        return extraction, usage

    def extract_with_fallback(
        self,
        *,
        text: str,
        search_context: SearchContext,
    ) -> tuple[VacancyAIExtraction, LLMUsage]:
        if self._rate_limiter.llm_disabled:
            raise LlmQuotaExhausted(
                self._rate_limiter.llm_disabled_reason or "LLM отключён"
            )
        try:
            return self.extract(text=text, search_context=search_context)
        except LlmQuotaExhausted:
            raise
        except Exception as first_err:
            if _is_rate_limit_error(first_err):
                raise
            fallbacks = self._ai.resolved_fallback_models()
            if not fallbacks:
                raise
            last_err: Exception = first_err
            for fb in fallbacks:
                try:
                    _LOG.warning(
                        "Основная модель %s недоступна (%s), пробуем fallback: %s",
                        self._ai.model,
                        first_err,
                        fb,
                    )
                    return self.extract(text=text, search_context=search_context, model=fb)
                except LlmQuotaExhausted:
                    raise
                except Exception as e:
                    last_err = e
                    _LOG.warning("Fallback %s не удался: %s", fb, e)
            raise RuntimeError(
                f"OpenRouter: все модели недоступны (основная + {len(fallbacks)} fallback): {last_err}"
            ) from last_err

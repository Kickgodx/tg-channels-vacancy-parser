"""Клиент OpenRouter через официальный OpenAI SDK (OpenAI-compatible API)."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from tg_jobs_parser.ai.prompts import (
    build_cover_letter_messages,
    build_match_messages,
    build_messages,
)
from tg_jobs_parser.ai.rate_limit import (
    LlmQuotaExhausted,
    OpenRouterRateLimiter,
    extract_rate_limit_headers,
)
from tg_jobs_parser.ai.schema import VacancyAIExtraction, VacancyMatchExtraction
from tg_jobs_parser.ai.search_context import SearchContext
from tg_jobs_parser.config.schema import AiConfig
from tg_jobs_parser.observability.llm_metrics import record_request, record_tokens
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


def _strict_json_schema(model: type) -> dict[str, Any]:
    """Pydantic-схема → OpenAI-совместимый strict JSON Schema.

    Strict-режим требует ``additionalProperties: false`` и все свойства
    в ``required`` — проставляем рекурсивно, включая ``$defs``.
    """

    def _fix(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
            props = node.get("properties")
            if isinstance(props, dict):
                node["required"] = list(props.keys())
                for sub in props.values():
                    _fix(sub)
        for sub in node.values():
            if isinstance(sub, dict):
                _fix(sub)

    schema = model.model_json_schema()
    schema.pop("title", None)
    _fix(schema)
    return schema


def _parse_match(raw: str) -> VacancyMatchExtraction:
    text = _extract_json_text(raw)
    data = json.loads(text)
    return VacancyMatchExtraction.model_validate(data)


def _parse_cover_letter(raw: str) -> str:
    """Письмо — обычный текст; снимаем markdown-ограждение, если модель его добавила."""
    text = raw.strip()
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    if not text:
        raise ValueError("OpenRouter: пустое письмо")
    return text


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

    def close(self) -> None:
        """Закрыть HTTP-клиент OpenAI/httpx (меньше зависаний при выходе из UI)."""
        if self._sdk is None:
            return
        try:
            close_fn = getattr(self._sdk, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            pass
        self._sdk = None

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

    def _call_llm(
        self,
        *,
        messages: list[dict[str, str]],
        parse,
        model: str | None = None,
        reasoning: bool | None = None,
        json_schema: dict[str, Any] | None = None,
        plain_text: bool = False,
    ) -> tuple[Any, LLMUsage]:
        """Общий вызов chat.completions: kwargs, usage, метрики, разбор через ``parse``."""
        use_model = model or self._ai.model
        use_reasoning = self._ai.reasoning_enabled if reasoning is None else reasoning
        kwargs: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": self._ai.temperature,
            "max_tokens": self._ai.max_completion_tokens,
        }
        extra_body: dict[str, Any] | None = None
        if use_reasoning:
            extra_body = {"reasoning": {"enabled": True}}
        elif json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.get("title", "response"),
                    "strict": True,
                    "schema": json_schema,
                },
            }
        elif not plain_text:
            kwargs["response_format"] = {"type": "json_object"}

        if extra_body:
            kwargs["extra_body"] = extra_body

        start = time.perf_counter()
        outcome = "error"
        usage: LLMUsage | None = None
        try:
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
                result = parse(content)
            except (json.JSONDecodeError, ValueError) as e:
                outcome = "parse_error"
                _LOG.warning("Не удалось разобрать JSON от %s: %s", use_model, e)
                raise
            outcome = "success"
            return result, usage
        except LlmQuotaExhausted:
            outcome = "rate_limited"
            raise
        finally:
            record_request(
                model=use_model,
                outcome=outcome,
                duration_seconds=time.perf_counter() - start,
            )
            if usage is not None:
                record_tokens(
                    model=use_model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                )

    def extract(
        self,
        *,
        text: str,
        search_context: SearchContext,
        model: str | None = None,
    ) -> tuple[VacancyAIExtraction, LLMUsage]:
        messages = build_messages(
            text=text,
            search_context=search_context,
            use_few_shot=self._ai.use_few_shot,
        )
        return self._call_llm(messages=messages, parse=_parse_extraction, model=model)

    def match(
        self,
        *,
        resume_text: str,
        vacancy_text: str,
        model: str | None = None,
    ) -> tuple[VacancyMatchExtraction, LLMUsage]:
        """Матчинг идёт своей моделью (``ai.match_model``) — обычно reasoning.

        При невалидном/неразбираемом JSON — один controlled retry той же модели.
        """
        messages = build_match_messages(resume_text=resume_text, vacancy_text=vacancy_text)
        call = lambda: self._call_llm(  # noqa: E731
            messages=messages,
            parse=_parse_match,
            model=model or self._ai.resolved_match_model(),
            reasoning=self._ai.match_reasoning_enabled,
            json_schema=_strict_json_schema(VacancyMatchExtraction),
        )
        try:
            return call()
        except LlmQuotaExhausted:
            raise
        except (json.JSONDecodeError, ValueError) as e:
            _LOG.warning("Матчинг: невалидный JSON от LLM (%s), повторный запрос", e)
            return call()

    def cover_letter(
        self,
        *,
        resume_text: str,
        vacancy_text: str,
        tone: str = "neutral",
        extra_notes: str | None = None,
        match_summary: dict | None = None,
        model: str | None = None,
    ) -> tuple[str, LLMUsage]:
        """Сопроводительное письмо — обычный текст, не JSON.

        Модель — ``ai.cover_letter_model`` (пусто → ``match_model`` → ``model``),
        reasoning-флаг общий с матчингом. Пустой ответ — один controlled retry.
        """
        messages = build_cover_letter_messages(
            resume_text=resume_text,
            vacancy_text=vacancy_text,
            tone=tone,
            extra_notes=extra_notes,
            match_summary=match_summary,
        )
        call = lambda: self._call_llm(  # noqa: E731
            messages=messages,
            parse=_parse_cover_letter,
            model=model or self._ai.resolved_cover_letter_model(),
            reasoning=self._ai.match_reasoning_enabled,
            plain_text=True,
        )
        try:
            return call()
        except LlmQuotaExhausted:
            raise
        except (json.JSONDecodeError, ValueError) as e:
            _LOG.warning("Письмо: пустой/битый ответ LLM (%s), повторный запрос", e)
            return call()

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

    def cover_letter_with_fallback(
        self,
        *,
        resume_text: str,
        vacancy_text: str,
        tone: str = "neutral",
        extra_notes: str | None = None,
        match_summary: dict | None = None,
    ) -> tuple[str, LLMUsage]:
        if self._rate_limiter.llm_disabled:
            raise LlmQuotaExhausted(
                self._rate_limiter.llm_disabled_reason or "LLM отключён"
            )
        try:
            return self.cover_letter(
                resume_text=resume_text,
                vacancy_text=vacancy_text,
                tone=tone,
                extra_notes=extra_notes,
                match_summary=match_summary,
            )
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
                        "Модель писем %s недоступна (%s), пробуем fallback: %s",
                        self._ai.resolved_cover_letter_model(),
                        first_err,
                        fb,
                    )
                    return self.cover_letter(
                        resume_text=resume_text,
                        vacancy_text=vacancy_text,
                        tone=tone,
                        extra_notes=extra_notes,
                        match_summary=match_summary,
                        model=fb,
                    )
                except LlmQuotaExhausted:
                    raise
                except Exception as e:
                    last_err = e
                    _LOG.warning("Fallback %s не удался (cover letter): %s", fb, e)
            raise RuntimeError(
                f"OpenRouter: все модели недоступны (основная + {len(fallbacks)} fallback): {last_err}"
            ) from last_err

    def match_with_fallback(
        self,
        *,
        resume_text: str,
        vacancy_text: str,
    ) -> tuple[VacancyMatchExtraction, LLMUsage]:
        if self._rate_limiter.llm_disabled:
            raise LlmQuotaExhausted(
                self._rate_limiter.llm_disabled_reason or "LLM отключён"
            )
        try:
            return self.match(resume_text=resume_text, vacancy_text=vacancy_text)
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
                        "Модель матчинга %s недоступна (%s), пробуем fallback: %s",
                        self._ai.resolved_match_model(),
                        first_err,
                        fb,
                    )
                    return self.match(
                        resume_text=resume_text, vacancy_text=vacancy_text, model=fb
                    )
                except LlmQuotaExhausted:
                    raise
                except Exception as e:
                    last_err = e
                    _LOG.warning("Fallback %s не удался (match): %s", fb, e)
            raise RuntimeError(
                f"OpenRouter: все модели недоступны (основная + {len(fallbacks)} fallback): {last_err}"
            ) from last_err

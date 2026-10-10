"""HTTP-клиент HH API с готовым Bearer-токеном (без client_credentials)."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from tg_jobs_parser.hh.metrics import record_hh_api_request
from tg_jobs_parser.hh.models import (
    VacancyDetail,
    VacancySearchParams,
    VacancySearchResponse,
    VacancyShort,
)

_RESPONSE_BODY_LOG_LIMIT = 2000

_LOG = logging.getLogger(__name__)

# Жёсткий лимит HH: page * per_page < 2000.
HH_VACANCIES_DEPTH_LIMIT = 2000


class HHApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class HHClientConfig:
    api_token: str
    contact_email: str
    api_base: str = "https://api.hh.ru"
    host: str = "hh.ru"
    app_name: str = "PersonalJobSearch"
    app_version: str = "1.0"
    max_rps: float = 3.0
    timeout: float = 30.0
    max_retries: int = 5

    @property
    def user_agent(self) -> str:
        return f"{self.app_name}/{self.app_version} ({self.contact_email})"


class HHClient:
    """Клиент публичного чтения вакансий с заранее заданным Bearer-токеном."""

    def __init__(self, config: HHClientConfig):
        if not config.api_token.strip():
            raise ValueError("HH API token is empty")
        if not config.contact_email.strip():
            raise ValueError("HH contact email is empty")
        self.config = config
        self._token = config.api_token.strip()
        self._min_interval = (1.0 / config.max_rps) if config.max_rps > 0 else 0.0
        self._max_retries = max(0, config.max_retries)
        self._last_request_at = 0.0
        self._http = httpx.Client(
            base_url=config.api_base.rstrip("/"),
            timeout=config.timeout,
            headers={
                "User-Agent": config.user_agent,
                "HH-User-Agent": config.user_agent,
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> HHClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._token}"
        params = dict(kwargs.pop("params", {}) or {})
        params.setdefault("host", self.config.host)

        started = time.monotonic()
        _LOG.info(
            "HH REQUEST %s %s params=%s",
            method,
            path,
            self._safe_params(params),
        )
        response = self._http_request(method, path, headers=headers, params=params, **kwargs)
        elapsed = time.monotonic() - started
        record_hh_api_request(
            method=method,
            path=path,
            status_code=response.status_code,
            duration_seconds=elapsed,
        )
        body_preview = self._response_preview(response)
        _LOG.info(
            "HH RESPONSE %s %s → %s in %.0fms body=%s",
            method,
            path,
            response.status_code,
            elapsed * 1000,
            body_preview,
        )
        return self._parse(response, context=f"{method} {path}")

    def _http_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            self._throttle()
            response = self._http.request(method, url, **kwargs)
            last_response = response
            if response.status_code != 429:
                return response
            if attempt >= self._max_retries:
                _LOG.error(
                    "HH HTTP 429 after %s attempts: %s %s",
                    attempt + 1,
                    method,
                    url,
                )
                break
            delay = self._retry_delay(response, attempt)
            _LOG.warning(
                "HH HTTP 429, retry %s/%s in %.1fs: %s %s",
                attempt + 1,
                self._max_retries,
                delay,
                method,
                url,
            )
            time.sleep(delay)
        assert last_response is not None
        return last_response

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            self._last_request_at = time.monotonic()
            return
        now = time.monotonic()
        wait = self._min_interval - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return (2**attempt) + random.uniform(0.0, 0.5)

    def search_vacancies(
        self,
        params: VacancySearchParams | None = None,
        /,
        **overrides: Any,
    ) -> VacancySearchResponse:
        search = self._resolve_search_params(params, overrides)
        payload = self.request("GET", "/vacancies", params=search.to_query())
        return VacancySearchResponse.model_validate(payload)

    def iter_vacancy_pages(
        self,
        params: VacancySearchParams | None = None,
        /,
        **overrides: Any,
    ) -> Iterator[VacancySearchResponse]:
        search = self._resolve_search_params(params, overrides)
        page = search.page
        per_page = search.per_page
        fetched = page * per_page

        while fetched < HH_VACANCIES_DEPTH_LIMIT:
            page_params = search.model_copy(update={"page": page, "per_page": per_page})
            result = self.search_vacancies(page_params)
            yield result
            if not result.items:
                break
            fetched += len(result.items)
            if fetched >= result.found:
                break
            if (page + 1) * per_page >= HH_VACANCIES_DEPTH_LIMIT:
                break
            page += 1

    def iter_vacancies(
        self,
        params: VacancySearchParams | None = None,
        /,
        *,
        max_items: int | None = None,
        **overrides: Any,
    ) -> Iterator[VacancyShort]:
        limit = HH_VACANCIES_DEPTH_LIMIT if max_items is None else min(max_items, HH_VACANCIES_DEPTH_LIMIT)
        if limit <= 0:
            return
        yielded = 0
        for index, page_result in enumerate(self.iter_vacancy_pages(params, **overrides)):
            if index == 0:
                _LOG.info(
                    "HH search found=%s, fetching up to %s",
                    page_result.found,
                    min(limit, page_result.found),
                )
            for item in page_result.items:
                yield item
                yielded += 1
                if yielded >= limit:
                    return

    def get_vacancy(self, vacancy_id: str) -> VacancyDetail:
        payload = self.request("GET", f"/vacancies/{vacancy_id}")
        return VacancyDetail.model_validate(payload)

    @staticmethod
    def _resolve_search_params(
        params: VacancySearchParams | None,
        overrides: dict[str, Any],
    ) -> VacancySearchParams:
        if params is None:
            return VacancySearchParams.model_validate(overrides)
        if overrides:
            return params.model_copy(update=overrides)
        return params

    @staticmethod
    def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
        """Копия query без секретов; длинный text обрезаем."""
        out: dict[str, Any] = {}
        for key, value in params.items():
            kl = str(key).lower()
            if kl in {"token", "access_token", "authorization"}:
                out[key] = "***"
                continue
            if key == "text" and isinstance(value, str) and len(value) > 120:
                out[key] = value[:117] + "..."
                continue
            out[key] = value
        return out

    @staticmethod
    def _response_preview(response: httpx.Response) -> str:
        """Краткое тело ответа для лога (без полного HTML/огромных JSON)."""
        try:
            payload: Any = response.json()
        except ValueError:
            text = (response.text or "").replace("\n", " ")
            if len(text) > _RESPONSE_BODY_LOG_LIMIT:
                return text[:_RESPONSE_BODY_LOG_LIMIT] + "…"
            return text or "<empty>"

        if isinstance(payload, dict):
            if "items" in payload:
                items = payload.get("items") or []
                return (
                    f"{{found={payload.get('found')}, pages={payload.get('pages')}, "
                    f"page={payload.get('page')}, per_page={payload.get('per_page')}, "
                    f"items={len(items) if isinstance(items, list) else '?'}}}"
                )
            if "id" in payload:
                employer = payload.get("employer") or {}
                employer_name = employer.get("name") if isinstance(employer, dict) else None
                area = payload.get("area") or {}
                area_name = area.get("name") if isinstance(area, dict) else None
                return (
                    f"{{id={payload.get('id')!r}, name={payload.get('name')!r}, "
                    f"employer={employer_name!r}, area={area_name!r}}}"
                )
            keys = list(payload.keys())[:12]
            return f"{{keys={keys}}}"
        text = str(payload)
        if len(text) > _RESPONSE_BODY_LOG_LIMIT:
            return text[:_RESPONSE_BODY_LOG_LIMIT] + "…"
        return text

    @staticmethod
    def _parse(response: httpx.Response, *, context: str) -> Any:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        if response.is_error:
            raise HHApiError(
                f"{context} → HTTP {response.status_code}: {body}",
                status_code=response.status_code,
                body=body,
            )
        return body

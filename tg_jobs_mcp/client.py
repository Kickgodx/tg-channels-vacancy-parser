"""Асинхронный клиент HTTP API с персональным токеном доступа."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from tg_jobs_mcp import __version__

DEFAULT_API_URL = "http://127.0.0.1:9000/api/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
# Сколько инструмент LLM ждёт готовности задачи, прежде чем вернуть job_id.
DEFAULT_LLM_WAIT_SECONDS = 60.0


class ApiError(Exception):
    """Ошибка API: ``detail = {code, message, retryable}`` или сетевой сбой."""

    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class Settings:
    api_url: str
    token: str
    api_key: str | None = None
    llm_wait_seconds: float = DEFAULT_LLM_WAIT_SECONDS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        token = (env.get("TGJOBS_TOKEN") or "").strip()
        if not token:
            raise ValueError("TGJOBS_TOKEN не задан: создайте токен на странице «Токены» и передайте его в env")
        try:
            wait = float(env.get("TGJOBS_LLM_WAIT_SECONDS") or DEFAULT_LLM_WAIT_SECONDS)
        except ValueError as e:
            raise ValueError("TGJOBS_LLM_WAIT_SECONDS — число секунд") from e
        return cls(
            api_url=(env.get("TGJOBS_API_URL") or DEFAULT_API_URL).rstrip("/"),
            token=token,
            api_key=(env.get("TGJOBS_API_KEY") or "").strip() or None,
            llm_wait_seconds=max(0.0, wait),
        )


def _error_from_response(resp: httpx.Response) -> ApiError:
    try:
        body = resp.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and "message" in detail:
        return ApiError(
            resp.status_code,
            str(detail.get("code") or "ERROR"),
            str(detail["message"]),
            retryable=bool(detail.get("retryable")),
        )
    if isinstance(detail, list):  # 422 FastAPI: список ошибок полей
        parts = [f"{'.'.join(str(x) for x in e.get('loc', [])[1:])}: {e.get('msg')}" for e in detail[:5]]
        return ApiError(resp.status_code, "VALIDATION", "; ".join(parts) or "Некорректные параметры")
    if isinstance(detail, str):
        return ApiError(resp.status_code, "ERROR", detail)
    return ApiError(resp.status_code, "ERROR", f"HTTP {resp.status_code}", retryable=resp.status_code >= 500)


class ApiClient:
    """Тонкая обёртка над ``httpx.AsyncClient``: заголовки авторизации и разбор ошибок API.

    Токен уходит только в заголовке ``Authorization`` и не попадает в текст ошибок.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.settings = settings
        headers = {
            "Authorization": f"Bearer {settings.token}",
            "User-Agent": f"tg-jobs-mcp/{__version__}",
            "Accept": "application/json",
        }
        if settings.api_key:
            headers["X-API-Key"] = settings.api_key
        self._http = httpx.AsyncClient(
            base_url=settings.api_url + "/", headers=headers, timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> tuple[int, Any]:
        """(статус, тело JSON) для 2xx; иначе ``ApiError``."""
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != []}
        try:
            resp = await self._http.request(method, path.lstrip("/"), params=clean, json=json)
        except httpx.TimeoutException as e:
            raise ApiError(0, "TIMEOUT", "API не ответил вовремя", retryable=True) from e
        except httpx.HTTPError as e:
            raise ApiError(
                0,
                "UNREACHABLE",
                f"API недоступен по адресу {self.settings.api_url}: {type(e).__name__}",
                retryable=True,
            ) from e
        if resp.status_code >= 400:
            raise _error_from_response(resp)
        if resp.status_code == 204 or not resp.content:
            return resp.status_code, None
        return resp.status_code, resp.json()

    async def get(self, path: str, **params: Any) -> Any:
        return (await self.request("GET", path, params=params))[1]

    async def post(self, path: str, *, json: Any = None, **params: Any) -> tuple[int, Any]:
        return await self.request("POST", path, params=params, json=json)

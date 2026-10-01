"""HTTP-лог запросов/ответов: метод, путь, статус, заголовки и читаемые тела.

Чистый ASGI-middleware (не ``BaseHTTPMiddleware``). Каждому запросу —
``request_id`` (UUID; валидный входящий ``X-Request-ID`` переиспользуется),
он же уходит в заголовок ответа ``X-Request-ID`` и в contextvar
``request_id_var`` — все логи внутри запроса получают префикс
``request_id=… |``. Две записи на запрос:

- ``REQUEST METHOD path {json}`` — до обработки: заголовки и тело (текстовое
  тело вычитывается заранее и отдаётся приложению повторно);
- ``RESPONSE METHOD path status Nms {json}`` — после ответа: заголовки и тело.

Тела ответа копятся только для лога — SSE и выгрузки файлов стримятся без
буферизации. Тела пишутся только для текстовых типов (JSON, form, text/*,
XML), кроме ``text/event-stream`` и ``Content-Disposition: attachment``.
Секреты маскируются: заголовки авторизации и cookies, query-параметры и
JSON/form-ключи вида password/token/secret/api_key.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import deque
from typing import Any
from urllib.parse import parse_qsl, urlencode

from opentelemetry import trace

from tg_jobs_parser.utils.logger import request_id_var

_LOG = logging.getLogger("backend.http")

MASK = "***"
REQUEST_ID_HEADER = "x-request-id"

DEFAULT_EXCLUDE_PATHS = frozenset({"/metrics"})
_SENSITIVE_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}
)
_SENSITIVE_KEYS_EXACT = frozenset(
    {"token", "accesstoken", "refreshtoken", "idtoken", "authorization", "cookie", "sessionstring"}
)
_SENSITIVE_KEYS_SUBSTR = ("password", "secret", "apikey", "apihash")
_TEXT_TYPES = frozenset(
    {
        "application/json",
        "application/x-ndjson",
        "application/x-www-form-urlencoded",
        "application/xml",
        "application/javascript",
    }
)
_CAPTURE_LIMIT = 1 << 20
_JSON_STR_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"(\s*:\s*)"(?:[^"\\]|\\.)*"')


def is_sensitive_key(key: str) -> bool:
    k = key.lower().replace("_", "").replace("-", "")
    return k in _SENSITIVE_KEYS_EXACT or any(s in k for s in _SENSITIVE_KEYS_SUBSTR)


def redact(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: MASK if isinstance(k, str) and is_sensitive_key(k) else redact(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def is_loggable_type(content_type: str | None) -> bool:
    mt = _media_type(content_type)
    if mt == "text/event-stream":
        return False
    return (
        mt in _TEXT_TYPES
        or mt.startswith("text/")
        or mt.endswith("+json")
        or mt.endswith("+xml")
    )


def redact_query(query: str) -> str:
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    return urlencode([(k, MASK if is_sensitive_key(k) else v) for k, v in pairs], safe="*")


def _redact_header(name: str, value: str) -> str:
    if name not in _SENSITIVE_HEADERS:
        return value
    if name in ("authorization", "proxy-authorization"):
        scheme, sep, _ = value.partition(" ")
        return f"{scheme} {MASK}" if sep else MASK
    return MASK


def render_headers(raw: list[tuple[bytes, bytes]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name_b, value_b in raw:
        name = name_b.decode("latin-1").lower()
        value = _redact_header(name, value_b.decode("latin-1"))
        out[name] = f"{out[name]}, {value}" if name in out else value
    return out


def _truncate(text: str, limit: int, total_bytes: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[truncated, {total_bytes} bytes]"


def render_body(
    raw: bytes, content_type: str | None, *, total_bytes: int, limit: int
) -> Any:
    """Тело для лога: dict/list для целого JSON в пределах лимита, иначе строка."""
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    mt = _media_type(content_type)
    complete = total_bytes <= len(raw)
    if complete and (mt == "application/json" or mt.endswith("+json")):
        try:
            data = redact(json.loads(text))
        except ValueError:
            pass
        else:
            dumped = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            return data if len(dumped) <= limit else _truncate(dumped, limit, total_bytes)
    if mt == "application/x-www-form-urlencoded" and complete:
        text = redact_query(text)
    else:
        text = _JSON_STR_PAIR.sub(
            lambda m: f'"{m.group(1)}"{m.group(2)}"{MASK}"'
            if is_sensitive_key(m.group(1))
            else m.group(0),
            text,
        )
    return _truncate(text, limit, total_bytes)


class _BodyCapture:
    __slots__ = ("enabled", "chunks", "size", "total")

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.chunks: list[bytes] = []
        self.size = 0
        self.total = 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if self.enabled and chunk and self.size < _CAPTURE_LIMIT:
            part = chunk[: _CAPTURE_LIMIT - self.size]
            self.chunks.append(part)
            self.size += len(part)

    @property
    def data(self) -> bytes:
        return b"".join(self.chunks)


class HttpLoggingMiddleware:
    def __init__(
        self,
        app,
        *,
        log_bodies: bool = True,
        body_limit: int = 4096,
        exclude_paths: frozenset[str] = DEFAULT_EXCLUDE_PATHS,
    ) -> None:
        self.app = app
        self.log_bodies = log_bodies
        self.body_limit = body_limit
        self.exclude_paths = exclude_paths

    def _is_noise(self, scope) -> bool:
        """Probes/scrape/документация и CORS preflight — без HTTP-лога."""
        if scope["path"] in self.exclude_paths:
            return True
        return scope["method"] == "OPTIONS" and any(
            name.lower() == b"access-control-request-method" for name, _ in scope.get("headers") or []
        )

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self._is_noise(scope):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        request_id = resolve_request_id(scope.get("headers") or [])
        token = request_id_var.set(request_id)
        trace.get_current_span().set_attribute("request_id", request_id)
        method = scope["method"]
        query = redact_query(scope.get("query_string", b"").decode("latin-1"))
        path = scope["path"] + (f"?{query}" if query else "")
        req_headers = render_headers(scope.get("headers") or [])

        pending: deque = deque()
        if self.log_bodies and is_loggable_type(req_headers.get("content-type")):
            pending, req_body, complete = await _read_request_body(receive)
            content_length = req_headers.get("content-length", "")
            total = req_body.total if complete else (
                int(content_length) if content_length.isdigit() else req_body.total + 1
            )
        else:
            req_body, total = _BodyCapture(False), 0

        client = scope.get("client")
        request: dict[str, Any] = {
            "event": "request",
            "request_id": request_id,
            "method": method,
            "path": path,
            "client": f"{client[0]}:{client[1]}" if client else None,
            "headers": req_headers,
        }
        body = render_body(
            req_body.data, req_headers.get("content-type"), total_bytes=total, limit=self.body_limit
        )
        if body is not None:
            request["body"] = body
        _LOG.info("REQUEST %s %s %s", method, path, _dumps(request))

        resp_body = _BodyCapture(False)
        resp: dict[str, Any] = {"status": None, "headers": {}}

        async def receive_wrapper():
            return pending.popleft() if pending else await receive()

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                raw_headers = [
                    *message.get("headers", []),
                    (REQUEST_ID_HEADER.encode(), request_id.encode()),
                ]
                message = {**message, "headers": raw_headers}
                headers = render_headers(raw_headers)
                resp["status"] = message["status"]
                resp["headers"] = headers
                resp_body.enabled = (
                    self.log_bodies
                    and is_loggable_type(headers.get("content-type"))
                    and "attachment" not in headers.get("content-disposition", "").lower()
                )
            elif message["type"] == "http.response.body":
                resp_body.add(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            self._log_response(request_id, method, path, started, resp, resp_body)
            request_id_var.reset(token)

    def _log_response(self, request_id, method, path, started, resp, resp_body) -> None:
        status = resp["status"] or 500
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response: dict[str, Any] = {
            "event": "response",
            "request_id": request_id,
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": duration_ms,
            "headers": resp["headers"],
        }
        body = render_body(
            resp_body.data,
            resp["headers"].get("content-type"),
            total_bytes=resp_body.total,
            limit=self.body_limit,
        )
        if body is not None:
            response["body"] = body
        level = (
            logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
        )
        _LOG.log(
            level, "RESPONSE %s %s %s %sms %s", method, path, status, duration_ms, _dumps(response)
        )


def resolve_request_id(raw_headers: list[tuple[bytes, bytes]]) -> str:
    """Входящий ``X-Request-ID``, если это UUID, иначе новый uuid4."""
    for name, value in raw_headers:
        if name.lower() == REQUEST_ID_HEADER.encode():
            try:
                return str(uuid.UUID(value.decode("latin-1")))
            except ValueError:
                break
    return str(uuid.uuid4())


async def _read_request_body(receive) -> tuple[deque, _BodyCapture, bool]:
    """Вычитать тело (до ``_CAPTURE_LIMIT``) для лога; сообщения потом отдаются приложению."""
    capture = _BodyCapture(True)
    pending: deque = deque()
    more = True
    while more and capture.size < _CAPTURE_LIMIT:
        message = await receive()
        pending.append(message)
        if message["type"] != "http.request":
            break
        capture.add(message.get("body", b""))
        more = message.get("more_body", False)
    return pending, capture, not more


def _dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)

"""HTTP-лог запросов/ответов: метод, путь, статус, заголовки и читаемые тела.

Чистый ASGI-middleware (не ``BaseHTTPMiddleware``): приложение получает и
отдаёт чанки как есть, копия копится только для лога — SSE и выгрузки файлов
стримятся без буферизации. Тела пишутся только для текстовых типов (JSON,
form, text/*, XML), кроме ``text/event-stream`` и ``Content-Disposition:
attachment``. Секреты маскируются: заголовки авторизации и cookies,
query-параметры и JSON/form-ключи вида password/token/secret/api_key.

Одна строка на запрос: ``METHOD path status Nms {json}`` — JSON-хвост
разбирается в Grafana через ``| regexp `(?P<http>\\{.*\\})$` | line_format
"{{.http}}" | json``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode

_LOG = logging.getLogger("backend.http")

MASK = "***"

_SKIP_PATHS = frozenset({"/metrics"})
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
    def __init__(self, app, *, log_bodies: bool = True, body_limit: int = 4096) -> None:
        self.app = app
        self.log_bodies = log_bodies
        self.body_limit = body_limit

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        req_headers = render_headers(scope.get("headers") or [])
        req_ct = req_headers.get("content-type")
        req_body = _BodyCapture(self.log_bodies and is_loggable_type(req_ct))
        resp_body = _BodyCapture(False)
        resp: dict[str, Any] = {"status": None, "headers": {}}

        async def receive_wrapper():
            message = await receive()
            if message["type"] == "http.request":
                req_body.add(message.get("body", b""))
            return message

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                headers = render_headers(message.get("headers") or [])
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
            self._log(scope, started, req_headers, req_body, resp, resp_body)

    def _log(self, scope, started, req_headers, req_body, resp, resp_body) -> None:
        status = resp["status"] or 500
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        query = redact_query(scope.get("query_string", b"").decode("latin-1"))
        path = scope["path"] + (f"?{query}" if query else "")
        client = scope.get("client")

        request: dict[str, Any] = {"headers": req_headers}
        response: dict[str, Any] = {"headers": resp["headers"]}
        for target, capture, ct in (
            (request, req_body, req_headers.get("content-type")),
            (response, resp_body, resp["headers"].get("content-type")),
        ):
            body = render_body(
                capture.data, ct, total_bytes=capture.total, limit=self.body_limit
            )
            if body is not None:
                target["body"] = body

        details = {
            "method": scope["method"],
            "path": path,
            "status": status,
            "duration_ms": duration_ms,
            "client": f"{client[0]}:{client[1]}" if client else None,
            "request": request,
            "response": response,
        }
        level = (
            logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
        )
        _LOG.log(
            level,
            "%s %s %s %sms %s",
            scope["method"],
            path,
            status,
            duration_ms,
            json.dumps(details, ensure_ascii=False, default=str),
        )

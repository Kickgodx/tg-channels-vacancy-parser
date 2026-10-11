"""Idempotency-Key для мутаций (parse / HH sync / import).

Резерв (pending) коммитится в отдельной ``session_scope`` до side-effect,
чтобы параллельный запрос видел ключ и не дублировал работу.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError

from backend.db import idempotency_repository as idem_repo
from backend.db.idempotency_repository import PENDING_STATUS
from backend.db.session import session_scope
from backend.errors import CODE_INTERNAL, AppError

SCOPE_PARSE_RUN = "parse_run"
SCOPE_HH_SYNC = "hh_sync"
SCOPE_IMPORT = "import"

ACTOR_API = "api"
MAX_KEY_LEN = 128


@dataclass(frozen=True)
class CachedIdempotentResponse:
    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True)
class BeginResult:
    """cached | in_progress | reserved=True (можно выполнять side-effect)."""

    cached: CachedIdempotentResponse | None = None
    reserved: bool = False
    in_progress: bool = False


def actor_for_user(user_id: int) -> str:
    return f"user:{user_id}"


def normalize_key(key: str | None) -> str | None:
    if not key or not key.strip():
        return None
    raw = key.strip()
    if len(raw) > MAX_KEY_LEN:
        raise AppError(
            "VALIDATION",
            f"Idempotency-Key длиннее {MAX_KEY_LEN} символов",
            status_code=400,
            retryable=False,
        )
    return raw


def request_fingerprint(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _public_body(body: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (body or {}).items() if not str(k).startswith("_")}


def _hash_mismatch(stored: dict[str, Any] | None, request_hash: str | None) -> bool:
    if not request_hash:
        return False
    stored_hash = (stored or {}).get("_request_hash")
    return bool(stored_hash and stored_hash != request_hash)


def _from_existing(row, request_hash: str | None) -> BeginResult:
    if _hash_mismatch(row.body, request_hash):
        raise AppError(
            "VALIDATION",
            "Idempotency-Key уже используется с другим телом запроса",
            status_code=409,
            retryable=False,
        )
    if row.status_code == PENDING_STATUS:
        return BeginResult(in_progress=True)
    return BeginResult(
        cached=CachedIdempotentResponse(
            status_code=row.status_code,
            body=_public_body(row.body),
        )
    )


def begin(
    *,
    key: str | None,
    scope: str,
    actor: str,
    request_hash: str | None = None,
) -> BeginResult:
    """Зарезервировать ключ до side-effect (отдельный commit). Без ключа — reserved."""
    nk = normalize_key(key)
    if nk is None:
        return BeginResult(reserved=True)

    with session_scope() as session:
        existing = idem_repo.get_idempotency(session, key=nk, scope=scope, actor=actor)
        if existing is not None:
            return _from_existing(existing, request_hash)
        try:
            with session.begin_nested():
                idem_repo.insert_pending_idempotency(
                    session,
                    key=nk,
                    scope=scope,
                    actor=actor,
                    request_hash=request_hash,
                )
        except IntegrityError:
            existing = idem_repo.get_idempotency(session, key=nk, scope=scope, actor=actor)
            if existing is None:
                raise AppError(
                    CODE_INTERNAL,
                    "Не удалось зарезервировать Idempotency-Key",
                    status_code=500,
                    retryable=True,
                ) from None
            return _from_existing(existing, request_hash)
    return BeginResult(reserved=True)


def complete(
    *,
    key: str | None,
    scope: str,
    actor: str,
    status_code: int,
    body: dict[str, Any],
    request_hash: str | None = None,
) -> None:
    nk = normalize_key(key)
    if nk is None:
        return
    out = dict(body)
    if request_hash:
        out["_request_hash"] = request_hash
    with session_scope() as session:
        idem_repo.put_idempotency(
            session,
            key=nk,
            scope=scope,
            actor=actor,
            status_code=status_code,
            body=out,
        )


def abandon(
    *,
    key: str | None,
    scope: str,
    actor: str,
) -> None:
    """Снять pending-резерв при ошибке до успешного ответа (retry с тем же ключом)."""
    nk = normalize_key(key)
    if nk is None:
        return
    with session_scope() as session:
        row = idem_repo.get_idempotency(session, key=nk, scope=scope, actor=actor)
        if row is not None and row.status_code == PENDING_STATUS:
            idem_repo.delete_idempotency(session, key=nk, scope=scope, actor=actor)


def lookup(
    *,
    key: str | None,
    scope: str,
    actor: str,
) -> CachedIdempotentResponse | None:
    """Устаревший API: только готовый кэш (не pending). Предпочтительно ``begin``."""
    nk = normalize_key(key)
    if nk is None:
        return None
    with session_scope() as session:
        row = idem_repo.get_idempotency(session, key=nk, scope=scope, actor=actor)
        if row is None or row.status_code == PENDING_STATUS:
            return None
        return CachedIdempotentResponse(status_code=row.status_code, body=_public_body(row.body))


def store(
    *,
    key: str | None,
    scope: str,
    actor: str,
    status_code: int,
    body: dict[str, Any],
) -> None:
    complete(key=key, scope=scope, actor=actor, status_code=status_code, body=body)

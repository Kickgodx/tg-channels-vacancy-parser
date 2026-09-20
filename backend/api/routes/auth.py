from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db import repository as repo
from backend.db.models import UserRow
from backend.observability import record_auth_event
from backend.schemas import AuthCredentials, TokenResponse, UserOut
from backend.services import auth_service
from backend.settings import get_settings

_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_out(row: UserRow) -> UserOut:
    return UserOut(
        id=row.id,
        email=row.email,
        is_admin=row.is_admin,
        created_at=row.created_at,
    )


def _token_response(user: UserRow) -> TokenResponse:
    return TokenResponse(
        access_token=auth_service.create_access_token(user.id),
        expires_in=get_settings().jwt_ttl_minutes * 60,
        user=_to_out(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: AuthCredentials, db: Session = Depends(get_db)) -> TokenResponse:
    """Регистрация. Первый пользователь → admin и наследует legacy-строки (user_id IS NULL)."""
    if not get_settings().auth_registration_open and repo.count_users(db) > 0:
        record_auth_event("register", "closed")
        _LOG.warning("Регистрация отклонена (закрыта): %s", body.email.strip().lower())
        raise HTTPException(status_code=403, detail="Registration is closed")
    email = body.email.strip().lower()
    if repo.get_user_by_email(db, email) is not None:
        record_auth_event("register", "conflict")
        _LOG.warning("Регистрация отклонена (email занят): %s", email)
        raise HTTPException(status_code=409, detail="Email already registered")
    is_first = repo.count_users(db) == 0
    user = repo.create_user(
        db,
        email=email,
        password_hash=auth_service.hash_password(body.password),
        is_admin=is_first,
    )
    if is_first:
        repo.claim_legacy_rows(db, user.id)
    record_auth_event("register", "success")
    _LOG.info("Зарегистрирован пользователь %s (id=%s, admin=%s)", email, user.id, is_first)
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(body: AuthCredentials, db: Session = Depends(get_db)) -> TokenResponse:
    user = repo.get_user_by_email(db, body.email.strip())
    if user is None or not auth_service.verify_password(
        body.password, user.password_hash
    ):
        record_auth_event("login", "invalid")
        _LOG.warning("Неудачный вход: %s", body.email.strip().lower())
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        record_auth_event("login", "disabled")
        _LOG.warning("Вход отклонён (пользователь отключён): %s", user.email)
        raise HTTPException(status_code=403, detail="User is disabled")
    record_auth_event("login", "success")
    _LOG.info("Вход выполнен: %s (id=%s)", user.email, user.id)
    return _token_response(user)


@router.get("/me", response_model=UserOut)
def me(user: UserRow = Depends(get_current_user)) -> UserOut:
    return _to_out(user)

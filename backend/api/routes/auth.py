from __future__ import annotations

import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.deps import CurrentUser, get_current_user, get_db, verify_api_key
from backend.db import user_content_repository as users_repo
from backend.db.models import ApiTokenRow, UserRow
from backend.errors import (
    CODE_EMAIL_TAKEN,
    CODE_FORBIDDEN,
    CODE_NOT_AUTHENTICATED,
    CODE_NOT_FOUND,
    CODE_VALIDATION,
    http_error,
)
from backend.observability import record_auth_event
from backend.schemas import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenOut,
    AuthCredentials,
    ChangePasswordRequest,
    RegisterRequest,
    SseTicketResponse,
    TokenResponse,
    UserOut,
)
from backend.services import api_tokens, auth_rate_limit, auth_service
from backend.settings import get_settings

_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_out(row: UserRow | CurrentUser) -> UserOut:
    return UserOut(
        id=row.id,
        email=row.email,
        is_admin=row.is_admin,
        created_at=row.created_at,
    )


def _token_response(user: UserRow) -> TokenResponse:
    return TokenResponse(
        access_token=auth_service.create_access_token(user.id, user.token_version),
        expires_in=get_settings().jwt_ttl_minutes * 60,
        user=_to_out(user),
    )


def _set_refresh_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        auth_service.REFRESH_COOKIE,
        raw,
        max_age=settings.refresh_ttl_days * 86400,
        path=auth_service.REFRESH_COOKIE_PATH,
        httponly=True,
        secure=bool(settings.refresh_cookie_secure),
        # Strict: cookie не уходит с чужих сайтов — CSRF на /auth/refresh закрыт.
        # Поэтому UI и API должны быть same-site (localhost:5173 и localhost:9000 — да).
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    # Те же атрибуты, что при установке: браузер стирает cookie только с совпадающими.
    response.delete_cookie(
        auth_service.REFRESH_COOKIE,
        path=auth_service.REFRESH_COOKIE_PATH,
        httponly=True,
        secure=bool(get_settings().refresh_cookie_secure),
        samesite="strict",
    )


def _start_session(user: UserRow, response: Response, db: Session) -> TokenResponse:
    """Вход: access-токен в ответе + новая refresh-сессия в httpOnly cookie."""
    _set_refresh_cookie(response, auth_service.issue_refresh_token(db, user))
    return _token_response(user)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_limits(event: str, request: Request, email: str | None = None) -> None:
    """Лимит попыток с IP и, для входа, неудачных входов на email — до argon2."""
    try:
        auth_rate_limit.check_ip(_client_ip(request))
        if email is not None:
            auth_rate_limit.check_login_allowed(email)
    except HTTPException:
        record_auth_event(event, "rate_limited")
        _LOG.warning("%s отклонён лимитом попыток: ip=%s email=%s", event, _client_ip(request), email or "-")
        raise


def _email_taken(email: str) -> HTTPException:
    record_auth_event("register", "conflict")
    _LOG.warning("Регистрация отклонена (email занят): %s", email)
    return http_error(409, CODE_EMAIL_TAKEN, "Email already registered")


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(
    body: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> TokenResponse:
    """Регистрация. Первый пользователь → admin и наследует legacy-строки (user_id IS NULL)."""
    _enforce_limits("register", request)
    email = body.email.strip().lower()
    # До commit: проверка «первый ли» и вставка атомарны относительно других регистраций.
    users_repo.lock_user_registration(db)
    is_first = users_repo.count_users(db) == 0
    if not get_settings().auth_registration_open and not is_first:
        record_auth_event("register", "closed")
        _LOG.warning("Регистрация отклонена (закрыта): %s", email)
        raise HTTPException(status_code=403, detail="Registration is closed")
    if users_repo.get_user_by_email(db, email) is not None:
        raise _email_taken(email)
    try:
        user = users_repo.create_user(
            db,
            email=email,
            password_hash=auth_service.hash_password(body.password),
            is_admin=is_first,
        )
    except IntegrityError:
        # Параллельная регистрация того же email мимо lock'а (например, из CLI).
        db.rollback()
        raise _email_taken(email) from None
    if is_first:
        users_repo.claim_legacy_rows(db, user.id)
    record_auth_event("register", "success")
    _LOG.info("Зарегистрирован пользователь %s (id=%s, admin=%s)", email, user.id, is_first)
    return _start_session(user, response, db)


def _authenticate(email: str, password: str, request: Request, response: Response, db: Session) -> TokenResponse:
    email = email.strip().lower()
    _enforce_limits("login", request, email)
    user = users_repo.get_user_by_email(db, email)
    if user is None or not auth_service.verify_password(password, user.password_hash):
        auth_rate_limit.record_login_failure(email)
        record_auth_event("login", "invalid")
        _LOG.warning("Неудачный вход: %s", email)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        record_auth_event("login", "disabled")
        _LOG.warning("Вход отклонён (пользователь отключён): %s", user.email)
        raise HTTPException(status_code=403, detail="User is disabled")
    auth_rate_limit.reset_login_failures(email)
    record_auth_event("login", "success")
    _LOG.info("Вход выполнен: %s (id=%s)", user.email, user.id)
    return _start_session(user, response, db)


@router.post("/login", response_model=TokenResponse)
def login(body: AuthCredentials, request: Request, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    return _authenticate(body.email, body.password, request, response, db)


async def _password_form(request: Request) -> tuple[str, str]:
    """``username``/``password`` из urlencoded-тела OAuth2 password flow.

    Вручную, а не ``OAuth2PasswordRequestForm``: тому нужен python-multipart,
    лишняя зависимость ради одного эндпоинта.
    """
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    username = form.get("username", [""])[0]
    password = form.get("password", [""])[0]
    if not username or not password:
        raise http_error(422, CODE_VALIDATION, "username and password are required")
    return username, password


@router.post("/token", response_model=TokenResponse, include_in_schema=False)
def token(
    request: Request,
    response: Response,
    form: tuple[str, str] = Depends(_password_form),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """OAuth2 password flow для кнопки «Authorize» в Swagger UI: ``username`` — email.

    Те же лимиты и проверки, что у ``/login``; клиентам — JSON ``/login``.
    """
    return _authenticate(form[0], form[1], request, response, db)


_REFRESH_COOKIE = Cookie(default=None, alias=auth_service.REFRESH_COOKIE, include_in_schema=False)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    refresh_token: str | None = _REFRESH_COOKIE,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Новый access-токен по refresh-cookie; cookie ротируется. Нет живой сессии — 401."""
    outcome = auth_service.rotate_refresh_token(db, refresh_token) if refresh_token else None
    if outcome is None:
        record_auth_event("refresh", "invalid")
        raise http_error(401, CODE_NOT_AUTHENTICATED, "Session expired")
    if outcome.refresh_token is not None:
        _set_refresh_cookie(response, outcome.refresh_token)
    record_auth_event("refresh", "success")
    return _token_response(outcome.user)


@router.post("/logout", status_code=204)
def logout(refresh_token: str | None = _REFRESH_COOKIE, db: Session = Depends(get_db)) -> Response:
    """Выход из этой сессии: refresh отзывается на сервере, cookie стирается.

    Без access-токена — выйти можно и с истёкшим. Уже выданный access доживает свой TTL.
    """
    if refresh_token:
        auth_service.revoke_refresh_session(db, refresh_token)
    response = Response(status_code=204)
    _clear_refresh_cookie(response)
    return response


@router.post("/sse-ticket", response_model=SseTicketResponse, dependencies=[Depends(verify_api_key)])
def sse_ticket(user: CurrentUser = Depends(get_current_user)) -> SseTicketResponse:
    """Одноразовый короткоживущий тикет для ``?ticket=`` SSE-эндпоинта.

    EventSource не шлёт заголовки; JWT в query оседал бы в логах прокси, тикет
    из лога бесполезен — он истекает через секунды и принимается один раз.
    """
    return SseTicketResponse(
        ticket=auth_service.create_sse_ticket(user.id, user.token_version),
        expires_in=int(auth_service.SSE_TICKET_TTL.total_seconds()),
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user)) -> UserOut:
    return _to_out(user)


@router.post("/change-password", response_model=TokenResponse)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Смена пароля отзывает все выданные токены и сессии; эта получает новые."""
    _enforce_limits("change_password", request, current.email)
    user = db.get(UserRow, current.id)
    if not auth_service.verify_password(body.current_password, user.password_hash):
        # Неверный текущий пароль — тот же перебор, что и на /login.
        auth_rate_limit.record_login_failure(user.email)
        record_auth_event("change_password", "invalid")
        _LOG.warning("Смена пароля отклонена (неверный текущий пароль): %s", user.email)
        raise http_error(400, CODE_VALIDATION, "Current password is incorrect")
    user.password_hash = auth_service.hash_password(body.new_password)
    user.token_version += 1
    db.flush()
    auth_rate_limit.reset_login_failures(user.email)
    record_auth_event("change_password", "success")
    _LOG.info("Пароль изменён, выданные токены отозваны: %s (id=%s)", user.email, user.id)
    return _start_session(user, response, db)


@router.post("/logout-all", status_code=204)
def logout_all(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    """Отозвать все выданные токены и refresh-сессии пользователя, включая текущие."""
    user = db.get(UserRow, current.id)
    user.token_version += 1
    db.flush()
    record_auth_event("logout_all", "success")
    _LOG.info("Все сессии отозваны: %s (id=%s)", user.email, user.id)
    response = Response(status_code=204)
    _clear_refresh_cookie(response)
    return response


def _token_out(row: ApiTokenRow) -> ApiTokenOut:
    return ApiTokenOut(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        scopes=list(row.scopes or []),
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
    )


def _require_session(user: CurrentUser) -> None:
    """Управлять токенами можно только из сессии UI: утёкший PAT не выпускает новые."""
    if user.auth_kind != "jwt":
        raise http_error(403, CODE_FORBIDDEN, "Токенами управляют только из сессии входа")


@router.get("/tokens", response_model=list[ApiTokenOut], dependencies=[Depends(verify_api_key)])
def list_api_tokens(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ApiTokenOut]:
    """Персональные токены доступа пользователя (без значений; отозванные не показываются)."""
    return [_token_out(row) for row in api_tokens.list_tokens(db, user.id)]


@router.post("/tokens", response_model=ApiTokenCreated, status_code=201, dependencies=[Depends(verify_api_key)])
def create_api_token(
    body: ApiTokenCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiTokenCreated:
    """Новый токен для скриптов и MCP-сервера; значение — только в этом ответе."""
    _require_session(user)
    try:
        row, raw = api_tokens.create_token(
            db,
            user_id=user.id,
            token_version=user.token_version,
            name=body.name,
            scopes=list(body.scopes),
            expires_days=body.expires_days,
        )
    except api_tokens.TooManyTokens as e:
        raise http_error(
            409, CODE_VALIDATION, f"Не больше {api_tokens.MAX_ACTIVE_TOKENS} действующих токенов — отзовите лишние"
        ) from e
    record_auth_event("token_create", "success")
    _LOG.info("Выпущен токен доступа id=%s пользователю id=%s (scopes=%s)", row.id, user.id, row.scopes)
    return ApiTokenCreated(**_token_out(row).model_dump(), token=raw)


@router.delete("/tokens/{token_id}", status_code=204, dependencies=[Depends(verify_api_key)])
def revoke_api_token(
    token_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Отозвать токен: действует сразу."""
    _require_session(user)
    if not api_tokens.revoke_token(db, user.id, token_id):
        raise http_error(404, CODE_NOT_FOUND, "Токен не найден")
    record_auth_event("token_revoke", "success")
    return Response(status_code=204)

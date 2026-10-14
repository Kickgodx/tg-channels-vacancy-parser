"""Настройки backend API (env + defaults)."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import quote

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_ENV_ALIASES = {"": "dev", "development": "dev", "local": "dev", "production": "prod"}
_MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Ошибка валидации печатается при старте — без этого в лог попадут значения env (секреты).
        hide_input_in_errors=True,
    )

    # dev — умолчания, удобные для локальной разработки; prod — закрытые
    # (см. _apply_app_env). Явно заданная переменная побеждает в обоих режимах.
    app_env: Literal["dev", "prod"] = Field(default="dev", alias="APP_ENV")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    # Адрес, на котором API доступен снаружи процесса. В compose процесс слушает 0.0.0.0
    # внутри контейнера, а наружу порт публикуется на BIND_ADDR — его и передаём сюда.
    # Пусто → API_HOST. dev на не-loopback адресе backend не запускает (см. check_exposure).
    api_public_addr: str | None = Field(default=None, alias="API_PUBLIC_ADDR")
    # Осознанно запустить dev на не-loopback адресе (доверенная сеть): вместо отказа — warning.
    allow_public_dev: bool = Field(default=False, alias="ALLOW_PUBLIC_DEV")
    # /metrics backend'а — на отдельном порту (как у воркеров), а не на порту API:
    # в compose он не публикуется на хост, Prometheus ходит по Docker-сети. 0 — не поднимать.
    api_metrics_port: int = Field(default=9100, ge=0, alias="API_METRICS_PORT")
    # Кэш COUNT-агрегатов db_* для /metrics (сек): частые scrape не нагружают Postgres; 0 — без кэша.
    metrics_db_stats_cache_seconds: float = Field(default=30.0, ge=0, alias="METRICS_DB_STATS_CACHE_SECONDS")
    # Пусто → Postgres из compose с хоста, пароль — POSTGRES_PASSWORD из того же .env
    # (см. _apply_app_env): backend, CLI и alembic с хоста работают без своего DATABASE_URL.
    database_url: str = Field(default="", alias="DATABASE_URL")
    postgres_password: str | None = Field(default=None, alias="POSTGRES_PASSWORD")
    config_path: Path = Field(default=Path("config.yaml"), alias="CONFIG_PATH")
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )
    api_key: str | None = Field(default=None, alias="API_KEY")
    jwt_secret: str | None = Field(default=None, alias="JWT_SECRET")
    jwt_ttl_minutes: int = Field(default=60, alias="JWT_TTL_MINUTES")
    # Лимиты /auth (0 — выключить): попыток входа/регистрации с одного IP в минуту и
    # неудачных входов на один email за окно — дальше 429 до конца окна, без argon2.
    auth_rate_limit_per_minute: int = Field(default=20, ge=0, alias="AUTH_RATE_LIMIT_PER_MINUTE")
    auth_max_failed_logins: int = Field(default=5, ge=0, alias="AUTH_MAX_FAILED_LOGINS")
    auth_failed_login_window_seconds: int = Field(default=900, ge=1, alias="AUTH_FAILED_LOGIN_WINDOW_SECONDS")
    # None → по APP_ENV: dev — true, prod — false.
    auth_registration_open: bool | None = Field(default=None, alias="AUTH_REGISTRATION_OPEN")
    http_log_bodies: bool | None = Field(default=None, alias="HTTP_LOG_BODIES")
    http_log_body_limit: int = Field(default=4096, alias="HTTP_LOG_BODY_LIMIT")
    otel_enabled: bool = Field(default=False, alias="OTEL_ENABLED")
    otel_traces_sample_ratio: float = Field(default=1.0, alias="OTEL_TRACES_SAMPLE_RATIO")
    http_log_exclude_paths: str = Field(
        # Агрегаты (vacancies/metrics, analytics/*) — крупные JSON; под polling/ab
        # заливают Loki (default ingestion 4MB/s → 429) и раздувают docker-логи.
        default=(
            "/api/v1/health,/api/v1/vacancies/metrics,"
            "/api/v1/analytics/salaries,/api/v1/analytics/sources,/api/v1/analytics/companies,"
            "/docs,/docs/oauth2-redirect,/redoc,/openapi.json,/favicon.ico"
        ),
        alias="HTTP_LOG_EXCLUDE_PATHS",
    )
    # HH.ru: готовый Bearer-токен (без client_credentials). Email — для User-Agent.
    hh_api_token: str | None = Field(default=None, alias="HH_API_TOKEN")
    hh_contact_email: str | None = Field(default=None, alias="HH_CONTACT_EMAIL")

    # LLM jobs: inprocess (ThreadPool в API) или rq (Redis Queue + llm-worker).
    llm_job_backend: str = Field(default="inprocess", alias="LLM_JOB_BACKEND")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    llm_worker_metrics_port: int = Field(default=9101, alias="LLM_WORKER_METRICS_PORT")
    llm_job_stuck_minutes: int = Field(default=15, alias="LLM_JOB_STUCK_MINUTES")
    llm_rq_job_timeout_seconds: int = Field(default=600, alias="LLM_RQ_JOB_TIMEOUT_SECONDS")

    # Parse jobs: inprocess (ThreadPool в API) или rq (Redis Queue + parse-worker).
    parse_job_backend: str = Field(default="inprocess", alias="PARSE_JOB_BACKEND")
    parse_worker_metrics_port: int = Field(default=9102, alias="PARSE_WORKER_METRICS_PORT")
    parse_job_stuck_minutes: int = Field(default=30, alias="PARSE_JOB_STUCK_MINUTES")
    parse_rq_job_timeout_seconds: int = Field(default=43200, alias="PARSE_RQ_JOB_TIMEOUT_SECONDS")
    parse_job_lease_seconds: int = Field(default=120, alias="PARSE_JOB_LEASE_SECONDS")
    llm_job_lease_seconds: int = Field(default=120, alias="LLM_JOB_LEASE_SECONDS")
    job_recover_interval_seconds: int = Field(default=60, alias="JOB_RECOVER_INTERVAL_SECONDS")
    # Суточная (UTC) квота вызовов LLM на не-админа: LLM-джобы и ручной импорт; 0 — без квоты.
    llm_user_daily_calls: int = Field(default=100, ge=0, alias="LLM_USER_DAILY_CALLS")
    # Кэш агрегатов дашборда и аналитики в памяти API (сек); 0 — без кэша.
    analytics_cache_seconds: float = Field(default=30.0, ge=0, alias="ANALYTICS_CACHE_SECONDS")
    # Сколько раз джобу берут в работу, прежде чем recover завершит её ошибкой
    # вместо очередного requeue (воркер стабильно падает на ней, напр. OOM).
    parse_job_max_attempts: int = Field(default=3, ge=1, alias="PARSE_JOB_MAX_ATTEMPTS")
    llm_job_max_attempts: int = Field(default=2, ge=1, alias="LLM_JOB_MAX_ATTEMPTS")

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalize_app_env(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        return _APP_ENV_ALIASES.get(normalized, normalized)

    @model_validator(mode="after")
    def _apply_app_env(self) -> Self:
        if not self.database_url.strip():
            # 127.0.0.1, а не localhost: compose публикует порт только на IPv4-loopback, а
            # localhost сначала резолвится в ::1 — на Windows psycopg ждёт там весь connect timeout.
            password = quote(self.postgres_password or "tgjobs", safe="")
            self.database_url = f"postgresql+psycopg://tgjobs:{password}@127.0.0.1:5432/tgjobs"
        if self.auth_registration_open is None:
            self.auth_registration_open = not self.is_production
        if self.http_log_bodies is None:
            self.http_log_bodies = not self.is_production
        if self.is_production and len((self.jwt_secret or "").strip()) < _MIN_JWT_SECRET_LENGTH:
            # Эфемерный секрет допустим только в dev: в prod он сбрасывает сессии при
            # каждом рестарте и расходится между процессами.
            raise ValueError(f"APP_ENV=prod: задайте JWT_SECRET не короче {_MIN_JWT_SECRET_LENGTH} символов")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "prod"

    @property
    def api_exposed_addr(self) -> str:
        return (self.api_public_addr or "").strip() or self.api_host.strip()

    @property
    def api_is_publicly_exposed(self) -> bool:
        return not _is_loopback(self.api_exposed_addr)

    @property
    def llm_job_backend_normalized(self) -> str:
        v = (self.llm_job_backend or "inprocess").strip().lower()
        return v if v in ("inprocess", "rq") else "inprocess"

    @property
    def parse_job_backend_normalized(self) -> str:
        v = (self.parse_job_backend or "inprocess").strip().lower()
        return v if v in ("inprocess", "rq") else "inprocess"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def http_log_exclude_path_set(self) -> frozenset[str]:
        return frozenset(p.strip() for p in self.http_log_exclude_paths.split(",") if p.strip())


def _is_loopback(addr: str) -> bool:
    """127.0.0.0/8, ::1 и localhost. Имя хоста, 0.0.0.0 и :: — не loopback."""
    host = addr.strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_exposure(settings: Settings) -> str | None:
    """Отказ стартовать API в dev, если он доступен не только с этой машины.

    dev-умолчания (открытая регистрация, тела запросов с резюме в логах,
    эфемерный JWT-секрет) безопасны только на loopback. Возвращает текст
    предупреждения, если это осознанно разрешено ``ALLOW_PUBLIC_DEV``.
    """
    if settings.is_production or not settings.api_is_publicly_exposed:
        return None
    message = (
        f"APP_ENV=dev, а API доступен на {settings.api_exposed_addr}: регистрация открыта, "
        "тела запросов (резюме) пишутся в лог. Задайте APP_ENV=prod (и JWT_SECRET) "
        "или публикуйте порт на 127.0.0.1"
    )
    if settings.allow_public_dev:
        return f"{message} (разрешено ALLOW_PUBLIC_DEV=true)"
    raise RuntimeError(f"{message}; осознанно разрешить — ALLOW_PUBLIC_DEV=true")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]

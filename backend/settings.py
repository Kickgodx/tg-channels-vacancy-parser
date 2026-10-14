"""Настройки backend API (env + defaults)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

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
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    # 127.0.0.1, а не localhost: compose публикует порт только на IPv4-loopback, а
    # localhost сначала резолвится в ::1 — на Windows psycopg ждёт там весь connect timeout.
    database_url: str = Field(
        default="postgresql+psycopg://tgjobs:tgjobs@127.0.0.1:5432/tgjobs",
        alias="DATABASE_URL",
    )
    config_path: Path = Field(default=Path("config.yaml"), alias="CONFIG_PATH")
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )
    api_key: str | None = Field(default=None, alias="API_KEY")
    jwt_secret: str | None = Field(default=None, alias="JWT_SECRET")
    jwt_ttl_minutes: int = Field(default=60, alias="JWT_TTL_MINUTES")
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
            "/metrics,/api/v1/health,/api/v1/vacancies/metrics,"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]

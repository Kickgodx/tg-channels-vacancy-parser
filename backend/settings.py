"""Настройки backend API (env + defaults)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    database_url: str = Field(
        default="postgresql+psycopg://tgjobs:tgjobs@localhost:5432/tgjobs",
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
    auth_registration_open: bool = Field(default=True, alias="AUTH_REGISTRATION_OPEN")
    http_log_bodies: bool = Field(default=True, alias="HTTP_LOG_BODIES")
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

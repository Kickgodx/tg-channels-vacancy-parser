"""Настройки backend API (env + defaults)."""

from __future__ import annotations

import os
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]

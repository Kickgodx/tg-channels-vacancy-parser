"""SQLAlchemy-модели PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ParseRunRow(Base):
    __tablename__ = "parse_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    run_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    posts_fetched: Mapped[int] = mapped_column(Integer, default=0)
    parse_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class VacancyRow(Base):
    """Уникальная вакансия по тексту (``id`` = sha256 normalize(cleaned_text))."""

    __tablename__ = "vacancies"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_post_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_channel: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    post_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    cleaned_text: Mapped[str] = mapped_column(Text, nullable=False)

    detected_specializations: Mapped[list[str]] = mapped_column(JSON, default=list)
    detected_grade: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    detected_salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_salary_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)

    work_format: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String(256), nullable=True)
    city: Mapped[str | None] = mapped_column(String(256), nullable=True)
    company: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact: Mapped[str | None] = mapped_column(String(512), nullable=True)

    technologies: Mapped[list[str]] = mapped_column(JSON, default=list)
    required_experience: Mapped[str | None] = mapped_column(Text, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    application_urls: Mapped[list[str]] = mapped_column(JSON, default=list)

    extraction_source: Mapped[str] = mapped_column(String(16), default="rules")
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    vacancy_score: Mapped[int] = mapped_column(Integer, default=0)
    vacancy_substantive_score: Mapped[int] = mapped_column(Integer, default=0)

    is_duplicate: Mapped[bool] = mapped_column(default=False)
    duplicate_group_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duplicate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_parse_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )


class AIExtractionCacheRow(Base):
    """Кэш ответов LLM (ключ = sha256 текста + model + prompt_version + mode)."""

    __tablename__ = "ai_extraction_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

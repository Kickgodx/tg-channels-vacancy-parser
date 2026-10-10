"""SQLAlchemy-модели PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
    rq_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    progress: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ExchangeRateRow(Base):
    """Курс валюты к рублю на дату (источник — ЦБ РФ)."""

    __tablename__ = "exchange_rates"

    rate_date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String(8), primary_key=True)
    rate_to_rub: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="cbr")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class VacancyRow(Base):
    """Уникальная вакансия по тексту (``id`` = sha256 normalize(cleaned_text))."""

    __tablename__ = "vacancies"
    __table_args__ = (
        # Список по умолчанию: WHERE NOT duplicate AND archived IS NULL ORDER BY post_date DESC.
        # На Postgres миграция 013 создаёт (post_date DESC NULLS LAST, id); здесь — portable вариант для SQLite/create_all.
        Index(
            "ix_vacancies_active_post_date",
            "post_date",
            "id",
            postgresql_where=text("is_duplicate IS false AND archived_at IS NULL"),
            sqlite_where=text("is_duplicate = 0 AND archived_at IS NULL"),
        ),
    )

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
    detected_salary_avg_rub: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    ai_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    vacancy_score: Mapped[int] = mapped_column(Integer, default=0)
    vacancy_substantive_score: Mapped[int] = mapped_column(Integer, default=0)

    is_duplicate: Mapped[bool] = mapped_column(default=False)
    duplicate_group_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duplicate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    last_parse_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class UserRow(Base):
    """Пользователь: владелец резюме, пресетов фильтров и (косвенно) матчей."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())


class SavedFilterRow(Base):
    """Именованный набор фильтров списка вакансий (JSON — поля VacancyQuery)."""

    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())


class ResumeRow(Base):
    """Резюме кандидата для матчинга с вакансиями."""

    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    skills_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())


class VacancyMatchRow(Base):
    """Оценка соответствия резюме вакансии. PK — и кэш матчинга (resume+vacancy+model)."""

    __tablename__ = "vacancy_matches"

    resume_id: Mapped[int] = mapped_column(Integer, ForeignKey("resumes.id", ondelete="CASCADE"), primary_key=True)
    vacancy_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("vacancies.id", ondelete="CASCADE"), primary_key=True
    )
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    matching_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    missing_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    strengths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())


class CoverLetterRow(Base):
    """Сгенерированное сопроводительное письмо. PK — и кэш генерации (resume+vacancy)."""

    __tablename__ = "cover_letters"

    resume_id: Mapped[int] = mapped_column(Integer, ForeignKey("resumes.id", ondelete="CASCADE"), primary_key=True)
    vacancy_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("vacancies.id", ondelete="CASCADE"), primary_key=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())


class HhListingRow(Base):
    """Кэш короткой выдачи HH: last_seen + флаг detail_fetched для anti-DDOS."""

    __tablename__ = "hh_listings"

    hh_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    employer: Mapped[str | None] = mapped_column(String(256), nullable=True)
    area: Mapped[str | None] = mapped_column(String(256), nullable=True)
    alternate_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    salary_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    snippet_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )


class LlmJobRow(Base):
    """Фоновая LLM-задача (мэтчинг / сопроводительное письмо)."""

    __tablename__ = "llm_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # match | cover_letter
    resume_id: Mapped[int] = mapped_column(Integer, ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False)
    vacancy_id: Mapped[str] = mapped_column(String(128), ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    force: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rq_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIExtractionCacheRow(Base):
    """Кэш ответов LLM (ключ = sha256 текста + model + prompt_version + mode)."""

    __tablename__ = "ai_extraction_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

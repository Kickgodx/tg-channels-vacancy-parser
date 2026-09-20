"""Ручной импорт вакансий: текст → rules-извлечение → LLM → запись в БД.

Тот же пайплайн, что у прогона по каналам (`raw_post_to_vacancy_rules` +
`AIEnrichmentService.enrich` + `upsert_vacancies`), но источник — вставленный
пользователем текст (например, скопированная вакансия с hh.ru), а не посты
Telegram. Пост-фильтры прогона (exclude_keywords, форматы, локации) здесь не
применяются: вакансия добавляется по явному действию пользователя.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_runner import raw_post_to_vacancy_rules
from tg_jobs_parser.parsers.base import RawPost

from backend.db import repository as repo
from backend.db.models import VacancyRow
from backend.db.vacancy_mapper import row_to_vacancy
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config

_LOG = logging.getLogger(__name__)

MANUAL_CHANNEL = "manual"


@dataclass
class ImportItemResult:
    index: int
    status: str  # created | exists | not_vacancy | error
    message: str | None = None
    vacancy: Vacancy | None = None


@dataclass
class ImportResult:
    items: list[ImportItemResult] = field(default_factory=list)
    ai_enabled: bool = False
    warning: str | None = None


def _text_to_vacancy(text: str, config: AppConfig, *, source_url: str | None) -> Vacancy:
    """Rules-извлечение из произвольного текста; канал-плейсхолдер ``manual``."""
    post = RawPost(
        channel_username=MANUAL_CHANNEL,
        post_id=uuid.uuid4().hex,
        text=text,
        post_date=datetime.now(timezone.utc),
        post_url=source_url,
    )
    v = raw_post_to_vacancy_rules(post, config)
    # source_url по умолчанию — t.me/s/<channel>; для ручного ввода это мусор.
    return v.model_copy(update={"source_url": source_url or ""})


def _enrich_one(
    ai_service, vacancy: Vacancy, *, skip_below: int
) -> tuple[Vacancy | None, str | None, str | None]:
    """
    Один LLM-разбор. Возвращает (vacancy|None, reject_status|None, save_warning|None):
    reject_status — 'not_vacancy'/'error', если сохранять нечего.
    """
    stats = ai_service.stats
    before = (
        stats.skipped_score,
        stats.dropped_not_job,
        stats.errors,
        stats.rate_limit_skipped,
    )
    enriched = ai_service.enrich(vacancy)
    after = (
        stats.skipped_score,
        stats.dropped_not_job,
        stats.errors,
        stats.rate_limit_skipped,
    )
    d_score, d_notjob, d_err, d_rl = (a - b for a, b in zip(after, before))

    if enriched is None:
        if d_notjob:
            return None, "not_vacancy", "LLM: текст не похож на вакансию"
        if d_score:
            return None, "not_vacancy", f"Низкий vacancy_score (< {skip_below})"
        if d_rl:
            return None, "error", "LLM недоступен (rate limit / дневная квота)"
        return None, "error", "Ошибка LLM-разбора"

    warnings = []
    if d_err:
        warnings.append("ошибка LLM — сохранён rules-разбор")
    if d_rl:
        warnings.append("LLM rate-limit — сохранён rules-разбор")
    return enriched, None, "; ".join(warnings) or None


def import_vacancy_texts(
    session: Session,
    texts: list[str],
    *,
    source_url: str | None = None,
) -> ImportResult:
    """Разобрать и сохранить тексты вакансий. Дедупликация — по хэшу текста (id)."""
    config = load_config(get_settings().config_path)
    result = ImportResult()

    ai_service = None
    if config.ai.enabled:
        from tg_jobs_parser.ai.enricher import AIEnrichmentService

        try:
            ai_service = AIEnrichmentService(config)
            ai_service.begin_batch(len(texts))
            result.ai_enabled = True
        except Exception as e:
            result.warning = f"AI не запущен ({e}); вакансии разобраны без LLM."
            _LOG.warning("Импорт: AI не инициализирован: %s", e)

    threshold = config.parser.vacancy_score_threshold
    try:
        for i, raw in enumerate(texts):
            text = raw.strip()
            if not text:
                result.items.append(
                    ImportItemResult(index=i, status="error", message="Пустой текст")
                )
                continue

            v = _text_to_vacancy(text, config, source_url=source_url)

            # Дедуп по content-hash: уже есть в БД — не тратим вызов LLM.
            existing = session.get(VacancyRow, v.id)
            if existing is not None:
                result.items.append(
                    ImportItemResult(
                        index=i,
                        status="exists",
                        message="Такая вакансия уже есть в базе",
                        vacancy=row_to_vacancy(existing),
                    )
                )
                continue

            message = None
            if ai_service is not None:
                enriched, reject_status, message = _enrich_one(
                    ai_service, v, skip_below=config.ai.skip_if_vacancy_score_below
                )
                if reject_status is not None:
                    result.items.append(
                        ImportItemResult(index=i, status=reject_status, message=message)
                    )
                    continue
                if enriched is not None:
                    v = enriched
            elif v.vacancy_score < threshold:
                result.items.append(
                    ImportItemResult(
                        index=i,
                        status="not_vacancy",
                        message=f"Не похоже на вакансию (score {v.vacancy_score} < {threshold})",
                    )
                )
                continue

            repo.upsert_vacancies(session, [v], parse_run_id=None)
            result.items.append(
                ImportItemResult(index=i, status="created", message=message, vacancy=v)
            )
            _LOG.info("Импорт: вакансия %s сохранена (AI=%s)", v.id, result.ai_enabled)
    finally:
        if ai_service is not None:
            ai_service.log_batch_finished()
            ai_service.close()

    return result

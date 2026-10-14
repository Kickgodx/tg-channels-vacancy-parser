"""Ручной импорт вакансий: текст → shared ingestion → запись в БД.

Тот же pipeline, что у прогона по каналам (`rules_from_raw_post` +
`apply_ingest_stages` + `upsert_vacancies`), но источник — вставленный
пользователем текст. Пост-фильтры прогона (exclude_keywords, форматы, локации)
и порог vacancy_score здесь не применяются: вакансия добавляется по явному
действию пользователя. При включённом AI решение «вакансия или нет» принимает
LLM (is_job_post) — ``skip_if_vacancy_score_below`` принудительно обнуляется.

LLM не нужен (AI выключен или все тексты уже в базе) — импорт идёт в запросе
(``import_vacancy_texts``). Нужен — ставится LLM-джоба ``import``
(``run_import_job`` в воркере): вызовы модели идут вне транзакции, итог по
каждому тексту пишется в ``llm_jobs.result`` по мере обработки.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db import llm_job_repository as llm_job_repo
from backend.db import vacancy_repository as vacancy_repo
from backend.db.models import VacancyRow
from backend.db.session import session_scope
from backend.db.vacancy_mapper import row_to_vacancy
from backend.observability import record_import_item
from backend.schemas import VacancyImportItemResult, VacancyImportResponse
from backend.services.llm_job_events import publish_llm_job_event
from backend.services.llm_job_service import KIND_IMPORT, LlmDailyQuotaError, reserve_user_llm_calls
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.ingestion.policy import MANUAL_POLICY
from tg_jobs_parser.ingestion.process import apply_ingest_stages
from tg_jobs_parser.ingestion.rules import rules_from_raw_post
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parsers.base import RawPost

_LOG = logging.getLogger(__name__)

MANUAL_CHANNEL = "manual"
_MSG_EMPTY = "Пустой текст"
_MSG_EXISTS = "Такая вакансия уже есть в базе"


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
    total: int = 0  # текстов в запросе; у выполняемой джобы items ещё короче


def _text_to_vacancy(text: str, config: AppConfig, *, source_url: str | None) -> Vacancy:
    """Rules-извлечение из произвольного текста; канал-плейсхолдер ``manual``."""
    post = RawPost(
        channel_username=MANUAL_CHANNEL,
        post_id=uuid.uuid4().hex,
        text=text,
        post_date=datetime.now(UTC),
        post_url=source_url,
    )
    v = rules_from_raw_post(post, config)
    # source_url по умолчанию — t.me/s/<channel>; для ручного ввода это мусор.
    return v.model_copy(update={"source_url": source_url or ""})


def _enrich_status(
    ai_service,
    vacancy: Vacancy,
    config: AppConfig,
    *,
    skip_below: int,
) -> tuple[Vacancy | None, str | None, str | None]:
    """
    AI + MANUAL policy. Возвращает (vacancy|None, reject_status|None, save_warning|None).
    """
    stats = ai_service.stats
    before = (
        stats.skipped_score,
        stats.dropped_not_job,
        stats.errors,
        stats.rate_limit_skipped,
    )
    enriched = apply_ingest_stages(
        vacancy,
        config,
        policy=MANUAL_POLICY,
        ai_service=ai_service,
    )
    after = (
        stats.skipped_score,
        stats.dropped_not_job,
        stats.errors,
        stats.rate_limit_skipped,
    )
    d_score, d_notjob, d_err, d_rl = (a - b for a, b in zip(after, before, strict=False))

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


def _record_counts(result: ImportResult) -> None:
    counts: dict[str, int] = {}
    for it in result.items:
        counts[it.status] = counts.get(it.status, 0) + 1
    _LOG.info(
        "Импорт завершён: %s позиций (%s), AI=%s",
        len(result.items),
        ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "нет",
        result.ai_enabled,
    )


def needs_llm(session: Session, texts: list[str], *, source_url: str | None = None) -> bool:
    """Нужен ли LLM: AI включён и есть непустой текст, которого ещё нет в базе (по content-hash)."""
    config = load_config(get_settings().config_path)
    if not config.ai.enabled:
        return False
    ids = {_text_to_vacancy(t, config, source_url=source_url).id for t in texts if t.strip()}
    if not ids:
        return False
    existing = set(session.scalars(select(VacancyRow.id).where(VacancyRow.id.in_(ids))).all())
    return bool(ids - existing)


def import_vacancy_texts(
    session: Session,
    texts: list[str],
    *,
    source_url: str | None = None,
) -> ImportResult:
    """Импорт без LLM — в транзакции запроса. Дедупликация — по хэшу текста (id).

    Вызывается, когда ``needs_llm`` ложно: AI выключен (rules as-is, MANUAL policy)
    или все тексты уже в базе.
    """
    config = load_config(get_settings().config_path)
    result = ImportResult(ai_enabled=config.ai.enabled, total=len(texts))
    for i, raw in enumerate(texts):
        text = raw.strip()
        if not text:
            result.items.append(ImportItemResult(index=i, status="error", message=_MSG_EMPTY))
            continue
        v = _text_to_vacancy(text, config, source_url=source_url)
        existing = session.get(VacancyRow, v.id)
        if existing is not None:
            result.items.append(
                ImportItemResult(index=i, status="exists", message=_MSG_EXISTS, vacancy=row_to_vacancy(existing))
            )
            continue
        v = apply_ingest_stages(v, config, policy=MANUAL_POLICY) or v
        vacancy_repo.upsert_vacancies(session, [v], parse_run_id=None)
        result.items.append(ImportItemResult(index=i, status="created", vacancy=v))
        _LOG.info("Импорт: вакансия %s сохранена (rules)", v.id)
    for it in result.items:
        record_import_item(it.status)
    _record_counts(result)
    return result


def job_params(texts: list[str], source_url: str | None) -> dict:
    return {"texts": texts, "source_url": source_url}


def _item_dict(index: int, status: str, message: str | None = None, vacancy_id: str | None = None) -> dict:
    return {"index": index, "status": status, "message": message, "vacancy_id": vacancy_id}


def run_import_job(
    job_id: uuid.UUID,
    *,
    user_id: int,
    params: dict,
    result: dict | None,
    lease_owner: str,
) -> bool:
    """Выполнить джобу импорта (LLM-воркер). False — lease потерян, итог не дописан.

    Текст за текстом и без долгой транзакции: проверка дубля и списание квоты —
    короткими сессиями, вызов LLM — вне транзакции, затем одной транзакцией запись
    вакансии и итога по тексту (fenced по lease). Ретрай после падения воркера
    продолжает с первого текста без итога. Квота кончилась посреди импорта —
    остальные тексты разбираются правилами.
    """
    texts: list[str] = list(params.get("texts") or [])
    source_url: str | None = params.get("source_url")
    config = load_config(get_settings().config_path)
    state = dict(result or {})
    items: list[dict] = list(state.get("items") or [])
    done = {it["index"] for it in items}
    warning: str | None = state.get("warning")
    ai_enabled = bool(state.get("ai_enabled"))

    ai_service = None
    # Ручной импорт: score-гейт перед LLM отключён — реальные вакансии
    # с низким rules-score не должны отсекаться до модели.
    ingest_config = config.model_copy(update={"ai": config.ai.model_copy(update={"skip_if_vacancy_score_below": 0})})
    if not config.ai.enabled:
        # AI выключили, пока джоба стояла в очереди.
        warning = warning or "AI выключен — вакансии разобраны без LLM."
    else:
        try:
            from tg_jobs_parser.ai.enricher import AIEnrichmentService

            ai_service = AIEnrichmentService(ingest_config)
            ai_service.begin_batch(len(texts) - len(done))
            ai_enabled = True
        except Exception as e:
            warning = warning or f"AI не запущен ({e}); вакансии разобраны без LLM."
            _LOG.warning("Импорт %s: AI не инициализирован: %s", job_id, e)

    def _commit(item: dict, vacancy: Vacancy | None = None) -> bool:
        payload = {
            "total": len(texts),
            "ai_enabled": ai_enabled,
            "warning": warning,
            "items": [*items, item],
        }
        with session_scope() as session:
            if not llm_job_repo.patch_llm_job_result(session, job_id, payload, lease_owner=lease_owner):
                return False
            if vacancy is not None:
                vacancy_repo.upsert_vacancies(session, [vacancy], parse_run_id=None)
        items.append(item)
        record_import_item(item["status"])
        publish_llm_job_event(job_id, status="running", kind=KIND_IMPORT)
        return True

    try:
        for i, raw in enumerate(texts):
            if i in done:
                continue
            text = raw.strip()
            if not text:
                if not _commit(_item_dict(i, "error", _MSG_EMPTY)):
                    return False
                continue

            v = _text_to_vacancy(text, config, source_url=source_url)
            # Дедуп по content-hash: уже есть в БД — не тратим вызов LLM.
            with session_scope() as session:
                exists = session.get(VacancyRow, v.id) is not None
            if exists:
                if not _commit(_item_dict(i, "exists", _MSG_EXISTS, v.id)):
                    return False
                continue

            message = None
            if ai_service is not None:
                try:
                    with session_scope() as session:
                        reserve_user_llm_calls(session, user_id=user_id, calls=1)
                except LlmDailyQuotaError as e:
                    _LOG.warning("Импорт %s: %s — дальше без LLM", job_id, e)
                    warning = f"{e} Остальные тексты разобраны без LLM."
                    ai_service.log_batch_finished()
                    ai_service.close()
                    ai_service = None
            if ai_service is not None:
                enriched, reject_status, message = _enrich_status(
                    ai_service,
                    v,
                    ingest_config,
                    skip_below=config.ai.skip_if_vacancy_score_below,
                )
                if reject_status is not None:
                    if not _commit(_item_dict(i, reject_status, message)):
                        return False
                    continue
                if enriched is not None:
                    v = enriched
            else:
                # Без AI: rules as-is (MANUAL policy — без post-filters).
                v = apply_ingest_stages(v, config, policy=MANUAL_POLICY) or v

            if not _commit(_item_dict(i, "created", message, v.id), v):
                return False
            _LOG.info("Импорт %s: вакансия %s сохранена (AI=%s)", job_id, v.id, ai_service is not None)
    finally:
        if ai_service is not None:
            ai_service.log_batch_finished()
            ai_service.close()

    _record_counts(import_result_from_items(items, ai_enabled=ai_enabled, warning=warning, total=len(texts)))
    return True


def import_result_from_items(
    items: list[dict],
    *,
    ai_enabled: bool,
    warning: str | None,
    total: int,
    vacancies: dict[str, Vacancy] | None = None,
) -> ImportResult:
    return ImportResult(
        items=[
            ImportItemResult(
                index=it["index"],
                status=it["status"],
                message=it.get("message"),
                vacancy=(vacancies or {}).get(it.get("vacancy_id") or ""),
            )
            for it in sorted(items, key=lambda it: it["index"])
        ],
        ai_enabled=ai_enabled,
        warning=warning,
        total=total,
    )


def import_result_from_job(session: Session, data: dict | None) -> ImportResult:
    """Итог джобы импорта (в т.ч. частичный, пока она выполняется) с вакансиями из БД."""
    data = data or {}
    items: list[dict] = list(data.get("items") or [])
    ids = [it["vacancy_id"] for it in items if it.get("vacancy_id")]
    vacancies: dict[str, Vacancy] = {}
    if ids:
        rows = session.scalars(select(VacancyRow).where(VacancyRow.id.in_(ids))).all()
        vacancies = {r.id: row_to_vacancy(r) for r in rows}
    return import_result_from_items(
        items,
        ai_enabled=bool(data.get("ai_enabled")),
        warning=data.get("warning"),
        total=int(data.get("total") or 0),
        vacancies=vacancies,
    )


def to_import_response(result: ImportResult) -> VacancyImportResponse:
    items = [
        VacancyImportItemResult(index=it.index, status=it.status, message=it.message, vacancy=it.vacancy)
        for it in result.items
    ]
    return VacancyImportResponse(
        items=items,
        created=sum(1 for it in items if it.status == "created"),
        existing=sum(1 for it in items if it.status == "exists"),
        rejected=sum(1 for it in items if it.status in ("not_vacancy", "error")),
        ai_enabled=result.ai_enabled,
        warning=result.warning,
        total=result.total or len(items),
    )

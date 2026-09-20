"""Фоновый LLM-матчинг резюме к вакансиям (отдельно от прогонов парсинга)."""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from backend.db import repository as repo
from backend.db.session import session_scope
from backend.schemas import MatchProgress, VacancyQuery
from backend.services.progress_bus import progress_bus
from backend.settings import get_settings
from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient
from tg_jobs_parser.ai.rate_limit import LlmQuotaExhausted
from tg_jobs_parser.config.loader import load_config

_LOG = logging.getLogger(__name__)


@dataclass
class ActiveMatch:
    match_id: uuid.UUID
    resume_id: int
    user_id: int
    cancel: threading.Event = field(default_factory=threading.Event)
    status: str = "running"
    total: int = 0
    processed: int = 0
    matched: int = 0
    skipped_cached: int = 0
    errors: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None


class MatchRegistry:
    """In-memory состояние матчей: один активный за раз, история не персистится."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: ActiveMatch | None = None
        self._matches: dict[uuid.UUID, ActiveMatch] = {}

    def register(self, match_id: uuid.UUID, resume_id: int, user_id: int) -> ActiveMatch:
        with self._lock:
            if self._active is not None and self._active.status == "running":
                raise RuntimeError("Уже выполняется другой матчинг резюме")
            m = ActiveMatch(match_id=match_id, resume_id=resume_id, user_id=user_id)
            self._active = m
            self._matches[match_id] = m
            return m

    def get(self, match_id: uuid.UUID) -> ActiveMatch | None:
        with self._lock:
            return self._matches.get(match_id)

    def finish(self, match_id: uuid.UUID) -> None:
        with self._lock:
            m = self._matches.get(match_id)
            if m is not None and self._active is m:
                self._active = None


match_registry = MatchRegistry()


def _progress(m: ActiveMatch) -> MatchProgress:
    return MatchProgress(
        match_id=m.match_id,
        resume_id=m.resume_id,
        status=m.status,  # type: ignore[arg-type]
        total=m.total,
        processed=m.processed,
        matched=m.matched,
        skipped_cached=m.skipped_cached,
        errors=m.errors,
        tokens_in=m.tokens_in,
        tokens_out=m.tokens_out,
        error=m.error,
    )


def get_status(match_id: uuid.UUID) -> MatchProgress | None:
    m = match_registry.get(match_id)
    return _progress(m) if m is not None else None


def start_match(
    resume_id: int, query: VacancyQuery, *, limit: int, user_id: int
) -> uuid.UUID:
    match_id = uuid.uuid4()
    m = match_registry.register(match_id, resume_id, user_id)
    t = threading.Thread(
        target=_worker,
        args=(m, query, limit),
        name=f"match-{match_id}",
        daemon=True,
    )
    t.start()
    return match_id


def cancel_match(match_id: uuid.UUID) -> bool:
    m = match_registry.get(match_id)
    if m is None or m.status != "running":
        return False
    m.cancel.set()
    return True


def match_single(
    session, *, resume_text: str, vacancy_id: str, vacancy_text: str, resume_id: int
):
    """Синхронная оценка одной пары: вызов LLM + запись в ``vacancy_matches``."""
    cfg = load_config(get_settings().config_path)
    max_chars = cfg.ai.max_input_chars
    client = OpenRouterClient(cfg.ai)
    try:
        extraction, usage = client.match_with_fallback(
            resume_text=resume_text[:max_chars],
            vacancy_text=vacancy_text[:max_chars],
        )
    finally:
        client.close()
    return repo.upsert_vacancy_match(
        session,
        resume_id=resume_id,
        vacancy_id=vacancy_id,
        score=extraction.score,
        explanation=extraction.explanation,
        matching_skills=extraction.matching_skills,
        missing_skills=extraction.missing_skills,
        model=usage.model,
    )


def _worker(m: ActiveMatch, query: VacancyQuery, limit: int) -> None:
    client: OpenRouterClient | None = None
    try:
        cfg = load_config(get_settings().config_path)
        max_chars = cfg.ai.max_input_chars
        with session_scope() as session:
            resume = repo.get_resume(session, m.resume_id)
            if resume is None:
                m.status, m.error = "failed", "Резюме не найдено"
                return
            resume_text = resume.text[:max_chars]
            rows, m.skipped_cached = repo.list_vacancies_for_matching(
                session, query, resume_id=m.resume_id, limit=limit
            )
            # Материализуем до выхода из сессии — ORM-объекты детачатся.
            rows = [(r.id, (r.cleaned_text or "")[:max_chars]) for r in rows]
        m.total = len(rows)
        progress_bus.publish(m.match_id, _progress(m))
        if not rows:
            m.status = "completed"
            return

        client = OpenRouterClient(cfg.ai)
        max_workers = max(1, cfg.ai.concurrency)

        def _one(item: tuple[str, str]) -> tuple | None:
            if m.cancel.is_set():
                return None
            return client.match_with_fallback(
                resume_text=resume_text,
                vacancy_text=item[1],
            )

        pool = ThreadPoolExecutor(max_workers=max_workers)
        try:
            futures = {pool.submit(_one, item): item for item in rows}
            for fut in as_completed(futures):
                if m.cancel.is_set():
                    break
                vacancy_id = futures[fut][0]
                try:
                    result = fut.result()
                except LlmQuotaExhausted as e:
                    m.error = f"Лимит LLM исчерпан: {e}"
                    m.cancel.set()
                    m.processed += 1
                    m.errors += 1
                    break
                except Exception as e:
                    _LOG.warning("Матчинг вакансии %s упал: %s", vacancy_id, e)
                    m.errors += 1
                    m.processed += 1
                    progress_bus.publish(m.match_id, _progress(m))
                    continue
                if result is None:  # отмена до старта задачи
                    break
                extraction, usage = result
                with session_scope() as session:
                    repo.upsert_vacancy_match(
                        session,
                        resume_id=m.resume_id,
                        vacancy_id=vacancy_id,
                        score=extraction.score,
                        explanation=extraction.explanation,
                        matching_skills=extraction.matching_skills,
                        missing_skills=extraction.missing_skills,
                        model=usage.model,
                    )
                m.tokens_in += usage.prompt_tokens
                m.tokens_out += usage.completion_tokens
                m.matched += 1
                m.processed += 1
                progress_bus.publish(m.match_id, _progress(m))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        m.status = "cancelled" if m.cancel.is_set() else "completed"
    except Exception as e:
        _LOG.exception("Матчинг %s упал", m.match_id)
        m.status, m.error = "failed", str(e)
    finally:
        if client is not None:
            client.close()
        progress_bus.publish(m.match_id, _progress(m))
        progress_bus.publish_done(m.match_id)
        match_registry.finish(m.match_id)

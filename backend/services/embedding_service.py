"""Эмбеддинги вакансий и резюме: фоновая индексация, векторы запроса и резюме, похожие вакансии.

Индексацию запускает лидер API раз в ``ai.embeddings.sweep_interval_seconds``: новые и
изменённые вакансии получают вектор в пределах интервала. Пользовательские векторы (резюме,
текст смыслового поиска) считаются по запросу. Отдельный клиент OpenRouter — без суточного
бюджета пользовательского LLM: эмбеддинги на порядки дешевле и не должны его съедать.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import ResumeEmbeddingRow, ResumeRow, VacancyEmbeddingRow, VacancyRow
from backend.db.session import session_scope
from backend.db.vector_type import EMBEDDING_DIMENSIONS, query_vector
from backend.settings import get_settings
from jobdesk.config.loader import load_config
from jobdesk.config.schema import AiConfig

_LOG = logging.getLogger(__name__)

_QUERY_CACHE_SIZE = 256
# «Похожие»: не больше стольких вакансий одной компании; кандидатов берётся limit × factor.
SIMILAR_PER_COMPANY = 2
SIMILAR_CANDIDATES_FACTOR = 5


class EmbeddingsUnavailable(RuntimeError):
    """Смысловые функции выключены или провайдер недоступен."""


def _ai() -> AiConfig:
    return load_config(get_settings().config_path).ai


def current_model() -> str:
    """Модель, чьи векторы сейчас используются: векторы других моделей — как отсутствующие."""
    return _ai().embeddings.model


def instructed(instruction: str, text: str) -> str:
    """Текст запроса/резюме с инструкцией instruction-aware модели (формат Qwen3); вакансии — без неё."""
    return f"Instruct: {instruction}\nQuery:{text}" if instruction.strip() else text


def content_hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def vacancy_text(row: VacancyRow, *, max_chars: int) -> str:
    """Текст вакансии для эмбеддинга: поля разбора сверху (их вес в векторе выше), затем пост."""
    head = [
        f"Роль: {', '.join(row.detected_specializations or [])}" if row.detected_specializations else "",
        f"Грейд: {row.detected_grade}" if row.detected_grade and row.detected_grade != "unknown" else "",
        f"Технологии: {', '.join(row.technologies or [])}" if row.technologies else "",
        f"Формат: {row.work_format}" if row.work_format else "",
    ]
    body = (row.cleaned_text or row.raw_text or "").strip()
    text = "\n".join(h for h in head if h)
    return f"{text}\n\n{body}"[:max_chars] if text else body[:max_chars]


# --- клиент ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_client = None
_client_fp: tuple | None = None


def _get_client(ai: AiConfig):
    """Процессный клиент эмбеддингов со своим RPM-лимитом (общий счётчик — через порт Redis)."""
    from jobdesk.ai.clients.openrouter import OpenRouterClient
    from jobdesk.ai.rate_limit import OpenRouterRateLimiter

    global _client, _client_fp
    fp = (ai.base_url, ai.requests_per_minute, ai.request_timeout_seconds, ai.max_retries_on_error)
    with _client_lock:
        if _client is None or _client_fp != fp:
            if _client is not None:
                _client.close()
            limiter = OpenRouterRateLimiter(
                requests_per_minute=ai.requests_per_minute,
                max_retries_on_429=ai.max_retries_on_429,
                retry_base_delay_seconds=ai.retry_base_delay_seconds,
                stop_llm_when_rate_limited=False,
            )
            try:
                _client = OpenRouterClient(ai, rate_limiter=limiter)
            except RuntimeError as e:  # нет OPENROUTER_API_KEY
                raise EmbeddingsUnavailable(str(e)) from e
            _client_fp = fp
        return _client


def reset_embedding_client() -> None:
    global _client, _client_fp
    with _client_lock:
        if _client is not None:
            _client.close()
        _client, _client_fp = None, None
    _query_cache.clear()
    with _poison_lock:
        _poison.clear()


def embed_texts(texts: list[str], *, ai: AiConfig | None = None) -> list[list[float]]:
    ai = ai or _ai()
    if not ai.embeddings.enabled:
        raise EmbeddingsUnavailable("Эмбеддинги выключены (ai.embeddings.enabled: false)")
    try:
        vectors, _usage = _get_client(ai).embed(
            texts, model=ai.embeddings.model, dimensions=EMBEDDING_DIMENSIONS, providers=ai.embeddings.providers
        )
    except EmbeddingsUnavailable:
        raise
    except Exception as e:
        raise EmbeddingsUnavailable(f"Провайдер эмбеддингов недоступен: {e}") from e
    return vectors


# --- индексация вакансий ---------------------------------------------------------------


@dataclass
class SweepResult:
    embedded: int = 0
    unchanged: int = 0
    failed: bool = False
    # Вакансии, которые провайдер отверг поодиночке: отложены на ``POISON_RETRY_SECONDS``.
    skipped: int = 0


# Пачку с ошибкой повторяем по одной вакансии. Столько неудач подряд без единого успеха —
# провайдер недоступен целиком: проход останавливается, ничего не откладывается.
PROVIDER_DOWN_AFTER = 3
# Вакансия, которую провайдер отверг поодиночке, не участвует в индексации столько секунд
# (или до изменения её текста): иначе она первой попадала бы в каждый проход и тормозила его.
POISON_RETRY_SECONDS = 24 * 3600
_POISON_MAX = 10_000
_poison_lock = threading.Lock()
_poison: dict[str, tuple[str, float]] = {}  # vacancy_id → (content_hash, когда отложена, monotonic)


def _is_poisoned(vacancy_id: str, h: str) -> bool:
    with _poison_lock:
        entry = _poison.get(vacancy_id)
        if entry is None:
            return False
        if entry[0] != h or time.monotonic() - entry[1] >= POISON_RETRY_SECONDS:
            del _poison[vacancy_id]
            return False
        return True


def _mark_poisoned(vacancy_id: str, h: str) -> None:
    with _poison_lock:
        if len(_poison) >= _POISON_MAX:
            _poison.clear()
        _poison[vacancy_id] = (h, time.monotonic())


def _embed_one_by_one(
    batch: list[tuple[str, str, str]], ai: AiConfig
) -> tuple[list[tuple[tuple[str, str, str], list[float]]], bool]:
    """Пачка, которую провайдер отверг: по одной вакансии. Второе — провайдер недоступен целиком.

    Вакансия, которая падает сама, когда соседние проходят, откладывается (``_mark_poisoned``).
    ``PROVIDER_DOWN_AFTER`` неудач подряд — провайдер лёг: эти вакансии не откладываются.
    """
    done: list[tuple[tuple[str, str, str], list[float]]] = []
    failed: list[tuple[str, str, str]] = []
    in_a_row = 0
    provider_down = False
    for item in batch:
        try:
            [vector] = embed_texts([item[1]], ai=ai)
        except EmbeddingsUnavailable as e:
            failed.append(item)
            in_a_row += 1
            if in_a_row >= PROVIDER_DOWN_AFTER:
                _LOG.warning("Индексация эмбеддингов остановлена: %s", e)
                del failed[-in_a_row:]
                provider_down = True
                break
            _LOG.warning("Эмбеддинг вакансии %s не получен: %s", item[0], e)
            continue
        in_a_row = 0
        done.append((item, vector))
    if not done:
        # Не прошла ни одна: виноват провайдер, а не тексты.
        return done, True
    for vid, _, h in failed:
        _mark_poisoned(vid, h)
    if failed:
        _LOG.warning(
            "Эмбеддинги: %s вакансий отложено на %s ч — провайдер отвергает их текст: %s",
            len(failed),
            POISON_RETRY_SECONDS // 3600,
            ", ".join(vid for vid, _, _ in failed),
        )
    return done, provider_down


def _candidates(session: Session, *, model: str, limit: int) -> list[VacancyRow]:
    """Уникальные вакансии без вектора, с вектором другой модели или изменённые после него.

    Сначала активные, затем свежие: они нужны поиску и подбору раньше архива.
    """
    e = VacancyEmbeddingRow
    stmt = (
        select(VacancyRow)
        .outerjoin(e, e.vacancy_id == VacancyRow.id)
        .where(
            VacancyRow.is_duplicate.is_(False),
            or_(e.vacancy_id.is_(None), e.model != model, VacancyRow.updated_at > e.created_at),
        )
        .order_by(VacancyRow.archived_at.is_not(None), VacancyRow.post_date.desc().nulls_last(), VacancyRow.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def _upsert_vacancy_vectors(session: Session, rows: list[dict]) -> None:
    stmt = pg_insert(VacancyEmbeddingRow).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["vacancy_id"],
            set_={
                "model": stmt.excluded.model,
                "content_hash": stmt.excluded.content_hash,
                "embedding": stmt.excluded.embedding,
                "created_at": func.now(),
            },
        )
    )


def sweep_vacancies(*, max_items: int | None = None) -> SweepResult:
    """Один проход индексации; недоступный провайдер останавливает проход до следующего интервала.

    Пачка с ошибкой повторяется по одной вакансии: вакансию, которую провайдер отвергает сам по
    себе, проход откладывает (``POISON_RETRY_SECONDS`` или до изменения текста) и идёт дальше.
    """
    ai = _ai()
    cfg = ai.embeddings
    result = SweepResult()
    limit = cfg.max_per_sweep if max_items is None else max_items
    if not cfg.enabled or limit <= 0:
        return result
    with session_scope() as session:
        rows = _candidates(session, model=cfg.model, limit=limit)
        existing = {
            vid: h
            for vid, h, m in session.execute(
                select(
                    VacancyEmbeddingRow.vacancy_id, VacancyEmbeddingRow.content_hash, VacancyEmbeddingRow.model
                ).where(VacancyEmbeddingRow.vacancy_id.in_([r.id for r in rows]))
            ).all()
            if m == cfg.model
        }
        todo: list[tuple[str, str, str]] = []
        unchanged: list[str] = []
        for row in rows:
            text = vacancy_text(row, max_chars=cfg.max_input_chars)
            h = content_hash(text, cfg.model)
            if existing.get(row.id) == h:
                unchanged.append(row.id)
            elif _is_poisoned(row.id, h):
                result.skipped += 1
            else:
                todo.append((row.id, text, h))
        if unchanged:
            # Текст тот же (вакансию лишь встретили снова) — только отметка, без запроса к API.
            session.execute(
                VacancyEmbeddingRow.__table__.update()
                .where(VacancyEmbeddingRow.vacancy_id.in_(unchanged))
                .values(created_at=func.now())
            )
            result.unchanged = len(unchanged)
    for start in range(0, len(todo), cfg.batch_size):
        batch = todo[start : start + cfg.batch_size]
        provider_down = False
        try:
            done = list(zip(batch, embed_texts([t for _, t, _ in batch], ai=ai), strict=True))
        except EmbeddingsUnavailable as e:
            if len(batch) == 1:
                _LOG.warning("Индексация эмбеддингов остановлена: %s", e)
                done, provider_down = [], True
            else:
                # Пачку может валить одна вакансия: без разбора по одной она стояла бы первой
                # в каждом проходе и навсегда останавливала индексацию остальных.
                _LOG.warning("Пачка эмбеддингов не прошла (%s), повтор по одной вакансии", e)
                done, provider_down = _embed_one_by_one(batch, ai)
        if done:
            with session_scope() as session:
                _upsert_vacancy_vectors(
                    session,
                    [
                        {"vacancy_id": vid, "model": cfg.model, "content_hash": h, "embedding": vec}
                        for (vid, _, h), vec in done
                    ],
                )
            result.embedded += len(done)
        if provider_down:
            result.failed = True
            break
    if result.skipped:
        _LOG.info("Эмбеддинги: %s вакансий пропущено — провайдер отверг их раньше", result.skipped)
    if result.embedded:
        _LOG.info("Эмбеддинги: проиндексировано %s вакансий (без изменений %s)", result.embedded, result.unchanged)
    return result


# --- векторы пользователя ----------------------------------------------------------------

_query_cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
_query_lock = threading.Lock()


def query_embedding(text: str) -> list[float]:
    """Вектор текста смыслового поиска; последние ``_QUERY_CACHE_SIZE`` запросов — из памяти."""
    ai = _ai()
    key = (ai.embeddings.model, instructed(ai.embeddings.query_instruction, " ".join(text.split()).lower()))
    with _query_lock:
        hit = _query_cache.get(key)
        if hit is not None:
            _query_cache.move_to_end(key)
            return hit
    [vector] = embed_texts([key[1]], ai=ai)
    with _query_lock:
        _query_cache[key] = vector
        while len(_query_cache) > _QUERY_CACHE_SIZE:
            _query_cache.popitem(last=False)
    return vector


def resume_embedding(session: Session, resume: ResumeRow) -> list[float]:
    """Вектор резюме: из БД, если текст, инструкция и модель не менялись, иначе — запрос к API и upsert."""
    ai = _ai()
    text = instructed(ai.embeddings.resume_instruction, resume.text[: ai.embeddings.max_input_chars])
    h = content_hash(text, ai.embeddings.model)
    row = session.get(ResumeEmbeddingRow, resume.id)
    if row is not None and row.content_hash == h and row.model == ai.embeddings.model:
        return row.embedding
    [vector] = embed_texts([text], ai=ai)
    stmt = pg_insert(ResumeEmbeddingRow).values(
        resume_id=resume.id, model=ai.embeddings.model, content_hash=h, embedding=vector
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["resume_id"],
            set_={
                "model": stmt.excluded.model,
                "content_hash": stmt.excluded.content_hash,
                "embedding": stmt.excluded.embedding,
                "created_at": func.now(),
            },
        )
    )
    return vector


# --- выборки ------------------------------------------------------------------------------


def similar_vacancies(session: Session, vacancy: VacancyRow, *, limit: int) -> list[tuple[str, float]] | None:
    """Ближайшие активные уникальные вакансии; ``None`` — у вакансии ещё нет вектора текущей модели."""
    cfg = _ai().embeddings
    own = session.get(VacancyEmbeddingRow, vacancy.id)
    if own is None or own.model != cfg.model:
        return None
    e = VacancyEmbeddingRow
    distance = e.embedding.cosine_distance(query_vector(own.embedding))
    stmt = (
        select(e.vacancy_id, distance, VacancyRow.company_key)
        .join(VacancyRow, VacancyRow.id == e.vacancy_id)
        .where(
            e.vacancy_id != vacancy.id,
            e.model == cfg.model,
            VacancyRow.is_duplicate.is_(False),
            VacancyRow.archived_at.is_(None),
            # Почти тот же текст (перепост с правками) — не «похожая», а та же вакансия.
            distance >= cfg.near_duplicate_distance,
        )
        .order_by(distance, e.vacancy_id)
        .limit(limit * SIMILAR_CANDIDATES_FACTOR)
    )
    # Компания с потоком однотипных постов заняла бы весь список — не больше двух от одной.
    out: list[tuple[str, float]] = []
    per_company: dict[str, int] = {}
    for vid, d, company in session.execute(stmt).all():
        if company:
            if per_company.get(company, 0) >= SIMILAR_PER_COMPANY:
                continue
            per_company[company] = per_company.get(company, 0) + 1
        out.append((vid, float(d)))
        if len(out) >= limit:
            break
    return out


@dataclass
class EmbeddingCoverage:
    enabled: bool
    model: str
    vacancies: int
    embedded: int


def coverage(session: Session) -> EmbeddingCoverage:
    ai = _ai()
    e = VacancyEmbeddingRow
    total, embedded = session.execute(
        select(func.count(), func.count(e.vacancy_id))
        .select_from(VacancyRow)
        .outerjoin(e, and_(e.vacancy_id == VacancyRow.id, e.model == ai.embeddings.model))
        .where(VacancyRow.is_duplicate.is_(False))
    ).one()
    return EmbeddingCoverage(
        enabled=ai.embeddings.enabled, model=ai.embeddings.model, vacancies=int(total), embedded=int(embedded)
    )

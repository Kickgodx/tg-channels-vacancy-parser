"""Дамп и импорт данных PostgreSQL (перенос между инстансами)."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import (
    AIExtractionCacheRow,
    CoverLetterRow,
    ExchangeRateRow,
    LlmJobRow,
    ParseRunRow,
    ParseRunVacancyRow,
    VacancyMatchRow,
    VacancyRow,
    VacancySightingRow,
    writable_columns,
)
from backend.db.vacancy_repository import backfill_original_sightings

_LOG = logging.getLogger(__name__)

DUMP_FORMAT_VERSION = 1

TABLE_MODELS: list[tuple[str, type]] = [
    ("parse_runs", ParseRunRow),
    ("exchange_rates", ExchangeRateRow),
    ("vacancies", VacancyRow),
    ("parse_run_vacancies", ParseRunVacancyRow),
    ("vacancy_sightings", VacancySightingRow),
    ("ai_extraction_cache", AIExtractionCacheRow),
]

_RUN_LINKS_TABLE = "parse_run_vacancies"
_SIGHTINGS_TABLE = "vacancy_sightings"

ImportMode = Literal["merge", "replace"]


@dataclass(frozen=True)
class DumpStats:
    parse_runs: int = 0
    exchange_rates: int = 0
    vacancies: int = 0
    parse_run_vacancies: int = 0
    vacancy_sightings: int = 0
    ai_extraction_cache: int = 0


@dataclass(frozen=True)
class ImportStats:
    parse_runs: int = 0
    exchange_rates: int = 0
    vacancies: int = 0
    parse_run_vacancies: int = 0
    vacancy_sightings: int = 0
    ai_extraction_cache: int = 0


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _row_to_dict(row: object) -> dict[str, Any]:
    return {col.name: _serialize_value(getattr(row, col.name)) for col in writable_columns(type(row))}


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _deserialize_row(model: type, data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in writable_columns(model):
        if col.name not in data:
            continue
        raw = data[col.name]
        if raw is None:
            out[col.name] = None
            continue
        py_type = col.type.python_type
        if py_type is uuid.UUID:
            out[col.name] = uuid.UUID(str(raw))
        elif py_type is datetime:
            out[col.name] = _parse_datetime(str(raw))
        elif py_type is date:
            out[col.name] = _parse_date(str(raw))
        else:
            out[col.name] = raw
    return out


def dump_database(session: Session) -> dict[str, Any]:
    """Собрать все строки из таблиц приложения в JSON-совместимый dict."""
    tables: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    for name, model in TABLE_MODELS:
        rows = session.scalars(select(model)).all()
        tables[name] = [_row_to_dict(r) for r in rows]
        counts[name] = len(rows)
    _LOG.info(
        "Дамп: parse_runs=%s, exchange_rates=%s, vacancies=%s, ai_cache=%s",
        counts.get("parse_runs", 0),
        counts.get("exchange_rates", 0),
        counts.get("vacancies", 0),
        counts.get("ai_extraction_cache", 0),
    )
    return {
        "format_version": DUMP_FORMAT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "tables": tables,
    }


def write_dump_file(session: Session, path: Path) -> DumpStats:
    payload = dump_database(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tables = payload["tables"]
    return DumpStats(
        parse_runs=len(tables.get("parse_runs", [])),
        exchange_rates=len(tables.get("exchange_rates", [])),
        vacancies=len(tables.get("vacancies", [])),
        parse_run_vacancies=len(tables.get("parse_run_vacancies", [])),
        vacancy_sightings=len(tables.get(_SIGHTINGS_TABLE, [])),
        ai_extraction_cache=len(tables.get("ai_extraction_cache", [])),
    )


def load_dump_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Файл дампа должен содержать JSON-объект")
    version = data.get("format_version")
    if version != DUMP_FORMAT_VERSION:
        raise ValueError(f"Неподдерживаемая версия дампа: {version!r} (ожидается {DUMP_FORMAT_VERSION})")
    tables = data.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("В дампе отсутствует объект tables")
    return data


@dataclass(frozen=True)
class ReplaceLosses:
    """Данные пользователей, которые ``replace`` удалит каскадом вместе с вакансиями."""

    vacancy_matches: int = 0
    cover_letters: int = 0
    llm_jobs: int = 0


def count_replace_losses(session: Session) -> ReplaceLosses:
    """Сколько оценок, писем и LLM-задач с вакансией унесёт очистка ``vacancies``.

    В дамп они не входят: после ``replace`` их не восстановить.
    """

    def _count(stmt) -> int:
        return int(session.scalar(stmt) or 0)

    return ReplaceLosses(
        vacancy_matches=_count(select(func.count()).select_from(VacancyMatchRow)),
        cover_letters=_count(select(func.count()).select_from(CoverLetterRow)),
        llm_jobs=_count(select(func.count()).select_from(LlmJobRow).where(LlmJobRow.vacancy_id.is_not(None))),
    )


def _clear_tables(session: Session) -> None:
    for _, model in reversed(TABLE_MODELS):
        session.execute(delete(model))


def _upsert_rows(session: Session, model: type, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    pk_cols = [c.name for c in model.__table__.columns if c.primary_key]  # type: ignore[attr-defined]
    count = 0
    for raw in rows:
        data = _deserialize_row(model, raw)
        stmt = pg_insert(model).values(**data)
        update_cols = {k: data[k] for k in data if k not in pk_cols}
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
        session.execute(stmt)
        count += 1
    return count


def _backfill_run_links(session: Session) -> int:
    """Связка прогон ↔ вакансия из ``last_parse_run_id`` — для дампов, снятых до неё."""
    src = select(VacancyRow.last_parse_run_id, VacancyRow.id).join(
        ParseRunRow, ParseRunRow.id == VacancyRow.last_parse_run_id
    )
    stmt = pg_insert(ParseRunVacancyRow).from_select(["parse_run_id", "vacancy_id"], src).on_conflict_do_nothing()
    # Для INSERT SQLAlchemy без preserve_rowcount отдаёт rowcount -1 (запоминает только UPDATE/DELETE).
    return int(session.execute(stmt.execution_options(preserve_rowcount=True)).rowcount or 0)


def import_database(
    session: Session,
    payload: dict[str, Any],
    *,
    mode: ImportMode = "merge",
) -> ImportStats:
    """
    Загрузить дамп в БД.

    merge — UPSERT по первичному ключу (по умолчанию);
    replace — очистить все таблицы приложения, затем вставить из дампа.
    """
    tables = payload["tables"]
    if mode == "replace":
        _clear_tables(session)
        session.flush()

    stats: dict[str, int] = {}
    for name, model in TABLE_MODELS:
        raw_rows = tables.get(name, [])
        if not isinstance(raw_rows, list):
            raise ValueError(f"tables.{name} должен быть списком")
        if mode == "replace":
            for raw in raw_rows:
                session.add(model(**_deserialize_row(model, raw)))
            # Flush по таблице: без relationship ORM не гарантирует порядок INSERT под FK.
            session.flush()
            stats[name] = len(raw_rows)
        else:
            stats[name] = _upsert_rows(session, model, raw_rows)
    if _RUN_LINKS_TABLE not in tables:
        session.flush()
        stats[_RUN_LINKS_TABLE] = _backfill_run_links(session)
    if _SIGHTINGS_TABLE not in tables:
        # Дамп до vacancy_sightings: вакансии Telegram — оригиналы, о дублях данных нет.
        session.flush()
        stats[_SIGHTINGS_TABLE] = backfill_original_sightings(session)

    _LOG.info(
        "Импорт (%s): parse_runs=%s, exchange_rates=%s, vacancies=%s, ai_cache=%s",
        mode,
        stats.get("parse_runs", 0),
        stats.get("exchange_rates", 0),
        stats.get("vacancies", 0),
        stats.get("ai_extraction_cache", 0),
    )
    return ImportStats(
        parse_runs=stats.get("parse_runs", 0),
        exchange_rates=stats.get("exchange_rates", 0),
        vacancies=stats.get("vacancies", 0),
        parse_run_vacancies=stats.get(_RUN_LINKS_TABLE, 0),
        vacancy_sightings=stats.get(_SIGHTINGS_TABLE, 0),
        ai_extraction_cache=stats.get("ai_extraction_cache", 0),
    )


def import_dump_file(session: Session, path: Path, *, mode: ImportMode = "merge") -> ImportStats:
    return import_database(session, load_dump_file(path), mode=mode)

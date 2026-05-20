"""Дамп и импорт данных PostgreSQL (перенос между инстансами)."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.db.models import (
    AIExtractionCacheRow,
    ExchangeRateRow,
    ParseRunRow,
    VacancyRow,
)

_LOG = logging.getLogger(__name__)

DUMP_FORMAT_VERSION = 1

TABLE_MODELS: list[tuple[str, type]] = [
    ("parse_runs", ParseRunRow),
    ("exchange_rates", ExchangeRateRow),
    ("vacancies", VacancyRow),
    ("ai_extraction_cache", AIExtractionCacheRow),
]

ImportMode = Literal["merge", "replace"]


@dataclass(frozen=True)
class DumpStats:
    parse_runs: int = 0
    exchange_rates: int = 0
    vacancies: int = 0
    ai_extraction_cache: int = 0

    @property
    def total_rows(self) -> int:
        return self.parse_runs + self.exchange_rates + self.vacancies + self.ai_extraction_cache


@dataclass(frozen=True)
class ImportStats:
    parse_runs: int = 0
    exchange_rates: int = 0
    vacancies: int = 0
    ai_extraction_cache: int = 0

    @property
    def total_rows(self) -> int:
        return self.parse_runs + self.exchange_rates + self.vacancies + self.ai_extraction_cache


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _row_to_dict(row: object) -> dict[str, Any]:
    table = row.__table__  # type: ignore[attr-defined]
    return {
        col.name: _serialize_value(getattr(row, col.name))
        for col in table.columns
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _deserialize_row(model: type, data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in model.__table__.columns:  # type: ignore[attr-defined]
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
        "exported_at": datetime.now(timezone.utc).isoformat(),
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
        ai_extraction_cache=len(tables.get("ai_extraction_cache", [])),
    )


def load_dump_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Файл дампа должен содержать JSON-объект")
    version = data.get("format_version")
    if version != DUMP_FORMAT_VERSION:
        raise ValueError(
            f"Неподдерживаемая версия дампа: {version!r} (ожидается {DUMP_FORMAT_VERSION})"
        )
    tables = data.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("В дампе отсутствует объект tables")
    return data


def _clear_tables(session: Session) -> None:
    for _, model in reversed(TABLE_MODELS):
        session.execute(delete(model))


def _upsert_rows(session: Session, model: type, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    ins = pg_insert if dialect == "postgresql" else sqlite_insert
    pk_cols = [c.name for c in model.__table__.columns if c.primary_key]  # type: ignore[attr-defined]
    count = 0
    for raw in rows:
        data = _deserialize_row(model, raw)
        stmt = ins(model).values(**data)
        update_cols = {k: data[k] for k in data if k not in pk_cols}
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
        session.execute(stmt)
        count += 1
    return count


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
            stats[name] = len(raw_rows)
        else:
            stats[name] = _upsert_rows(session, model, raw_rows)

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
        ai_extraction_cache=stats.get("ai_extraction_cache", 0),
    )


def import_dump_file(session: Session, path: Path, *, mode: ImportMode = "merge") -> ImportStats:
    return import_database(session, load_dump_file(path), mode=mode)

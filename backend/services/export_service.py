"""Выгрузка вакансий в xlsx: потолок строк, потоковая запись, отдача из временного файла."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Iterable, Iterator
from typing import Any

from fastapi.responses import StreamingResponse

from backend.settings import get_settings
from tg_jobs_parser.exporters.excel_exporter import write_vacancies_xlsx
from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.vacancy import Vacancy

_LOG = logging.getLogger(__name__)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# До этого размера файл держится в памяти, дальше — на диске.
_SPOOL_MAX_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 256 * 1024


def export_row_limit() -> int:
    return get_settings().export_max_rows


def truncation_note(total: int, limit: int) -> str | None:
    """Пометка для файла, если под фильтр попало больше строк, чем выгружается."""
    if total <= limit:
        return None
    return f"обрезано до {limit} из {total} по текущей сортировке — уточните фильтры, чтобы выгрузить остальное"


def _iter_file(fileobj) -> Iterator[bytes]:
    try:
        fileobj.seek(0)
        while chunk := fileobj.read(_CHUNK_BYTES):
            yield chunk
    finally:
        fileobj.close()


def xlsx_response(
    vacancies: Iterable[Vacancy],
    *,
    total: int,
    limit: int,
    metrics: MetricsSummary | None,
    filename: str,
) -> StreamingResponse:
    """Собрать xlsx (пока открыта сессия запроса) во временный файл и отдать его потоком.

    ``total`` — сколько строк подходит под фильтр, ``limit`` — потолок выгрузки
    (``EXPORT_MAX_ROWS``); при обрезке в лист ``metrics_summary`` добавляется пометка,
    а в ответ — заголовок ``X-Export-Truncated: <выгружено>/<всего>``.
    """
    note = truncation_note(total, limit)
    extra: list[tuple[str, Any]] = [("exported_rows", min(total, limit)), ("matching_rows", total)]
    if note:
        extra.append(("truncated", note))
    # Закрывает _iter_file после отдачи (или except ниже) — не with: файл живёт дольше функции.
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES)  # noqa: SIM115
    try:
        written = write_vacancies_xlsx(spool, vacancies, metrics=metrics, summary_extra=extra)
    except BaseException:
        spool.close()
        raise
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if note:
        headers["X-Export-Truncated"] = f"{written}/{total}"
        _LOG.info("Экспорт %s обрезан: %s из %s строк", filename, written, total)
    return StreamingResponse(_iter_file(spool), media_type=XLSX_MEDIA_TYPE, headers=headers)

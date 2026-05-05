"""Экспорт результатов в Excel с несколькими листами."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.vacancy import Vacancy

_DATETIME_FMT = "DD.MM.YYYY HH:MM:SS"


def _naive_utc_for_excel(dt: datetime | None) -> datetime | None:
    """openpyxl/pandas не принимают timezone-aware datetime в ячейках Excel."""
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None):
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _excel_single_line(text: str | None) -> str:
    """Один сплошной ряд без переводов строк — строка таблицы не раздувается по высоте."""
    if not text:
        return ""
    t = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", t).strip()


def _post_id_only(full_id: str) -> str:
    """``qa_jobs-255541`` → ``255541`` (Telegram message id)."""
    if not full_id:
        return ""
    tail = full_id.rsplit("-", 1)[-1]
    return tail if tail.isdigit() else full_id


def _vacancy_row_for_excel(v: Vacancy, *, include_parse_dedup_columns: bool = True) -> dict[str, Any]:
    """Колонки без префикса ``detected_``, читаемые имена для отчёта."""
    d = v.model_dump(mode="python")
    row: dict[str, Any] = {
        "post_id": _post_id_only(str(d.get("id") or "")),
        "source_channel": d.get("source_channel"),
        "post_url": d.get("post_url"),
        "post_date": _naive_utc_for_excel(d.get("post_date")),
        "specializations": ", ".join(d.get("detected_specializations") or []),
        "grade": d.get("detected_grade"),
        "salary_min": d.get("detected_salary_min"),
        "salary_max": d.get("detected_salary_max"),
        "salary_avg": d.get("detected_salary_avg"),
        "salary_currency": d.get("salary_currency"),
        "work_format": d.get("work_format"),
        "country": d.get("country"),
        "city": d.get("city"),
        "company": d.get("company"),
        "contact": d.get("contact"),
        "technologies": ", ".join(d.get("technologies") or []),
        "required_experience": d.get("required_experience"),
        "employment_type": d.get("employment_type"),
        "vacancy_score": d.get("vacancy_score"),
    }
    if include_parse_dedup_columns:
        row["is_duplicate"] = d.get("is_duplicate")
        row["duplicate_group_id"] = d.get("duplicate_group_id")
        row["duplicate_reason"] = d.get("duplicate_reason")
        row["parsed_at"] = _naive_utc_for_excel(d.get("parsed_at"))
        row["source_url"] = d.get("source_url")
    else:
        row["duplicate_group_id"] = d.get("duplicate_group_id")
    row["raw_text"] = _excel_single_line(d.get("raw_text"))
    row["cleaned_text"] = _excel_single_line(d.get("cleaned_text"))
    return row


def _vacancies_df(rows: list[Vacancy], *, include_parse_dedup_columns: bool = True) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        [_vacancy_row_for_excel(v, include_parse_dedup_columns=include_parse_dedup_columns) for v in rows]
    )


def _apply_openpyxl_formatting(wb, sheet_names: list[str]) -> None:
    """
    Ширина колонок + формат дат (иначе Excel показывает ``#####`` при узкой колонке).
    ``raw_text`` / ``cleaned_text``: без переноса по словам — одна логическая строка, узкая высота ряда.
    """
    single_line_headers = {"raw_text", "cleaned_text"}
    for name in sheet_names:
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        headers: list[str | None] = []
        if ws.max_row < 1:
            continue
        for c in range(1, ws.max_column + 1):
            h = ws.cell(row=1, column=c).value
            headers.append(str(h) if h is not None else None)

        for col_idx in range(1, ws.max_column + 1):
            letter = get_column_letter(col_idx)
            header = headers[col_idx - 1] or ""
            max_w = 12.0
            is_single_line = header in single_line_headers

            for row_idx in range(1, ws.max_row + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                val = cell.value
                if isinstance(val, datetime):
                    cell.number_format = _DATETIME_FMT
                    max_w = max(max_w, 22.0)
                elif val is not None:
                    max_w = max(max_w, min(float(len(str(val))), 80.0))
                if is_single_line and row_idx > 1:
                    cell.alignment = Alignment(wrap_text=False, vertical="center")

            if is_single_line:
                ws.column_dimensions[letter].width = 52.0
            else:
                ws.column_dimensions[letter].width = min(max(max_w + 1.5, 10.0), 55.0)


def export_excel(
    path: str | Path,
    vacancies_all: list[Vacancy],
    vacancies_unique: list[Vacancy],
    duplicates: list[Vacancy],
    metrics: MetricsSummary,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    summary_rows = [
        ("total_posts_fetched", metrics.total_posts_fetched),
        ("total_vacancies", metrics.total_vacancies),
        ("unique_vacancies", metrics.unique_vacancies),
        ("duplicate_count", metrics.duplicate_count),
        ("vacancies_with_salary", metrics.vacancies_with_salary),
        ("vacancies_without_salary", metrics.vacancies_without_salary),
        ("daily_buckets", len(metrics.daily)),
        ("weekly_buckets", len(metrics.weekly)),
        ("monthly_buckets", len(metrics.monthly)),
        ("warnings", " | ".join(metrics.warnings)[:32700]),
    ]
    summary_df = pd.DataFrame(summary_rows, columns=["metric", "value"])

    df_spec_rows = []
    for spec, cnt in metrics.vacancies_by_specialization.items():
        st = metrics.salary_by_specialization.get(spec)
        df_spec_rows.append(
            {
                "specialization": spec,
                "vacancy_count": cnt,
                "salary_mean": st.mean if st else None,
                "salary_median": st.median if st else None,
                "vacancies_with_salary": st.count_with_salary if st else 0,
            }
        )
    df_spec = pd.DataFrame(df_spec_rows)

    df_grade_rows = []
    for grade, cnt in metrics.vacancies_by_grade.items():
        st = metrics.salary_by_grade.get(grade)
        df_grade_rows.append(
            {
                "grade": grade,
                "vacancy_count": cnt,
                "salary_mean": st.mean if st else None,
                "salary_median": st.median if st else None,
                "vacancies_with_salary": st.count_with_salary if st else 0,
            }
        )
    df_grade = pd.DataFrame(df_grade_rows)

    df_loc = pd.concat(
        [
            pd.DataFrame(metrics.vacancies_by_country.items(), columns=["location", "count"]).assign(
                kind="country"
            ),
            pd.DataFrame(metrics.vacancies_by_city.items(), columns=["location", "count"]).assign(
                kind="city"
            ),
        ],
        ignore_index=True,
    )

    df_work = pd.DataFrame(metrics.vacancies_by_work_format.items(), columns=["work_format", "count"])

    tech_rows = [{"technology": k, "count": v} for k, v in metrics.technology_top.items()]
    df_tech = pd.DataFrame(tech_rows)

    vacancy_sheets = ["vacancies_all", "vacancies_unique", "duplicates"]

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        _vacancies_df(vacancies_all, include_parse_dedup_columns=False).to_excel(
            writer, sheet_name="vacancies_all", index=False
        )
        _vacancies_df(vacancies_unique, include_parse_dedup_columns=False).to_excel(
            writer, sheet_name="vacancies_unique", index=False
        )
        _vacancies_df(duplicates).to_excel(writer, sheet_name="duplicates", index=False)
        summary_df.to_excel(writer, sheet_name="metrics_summary", index=False)
        df_spec_out = (
            df_spec.sort_values("vacancy_count", ascending=False)
            if not df_spec.empty and "vacancy_count" in df_spec.columns
            else df_spec
        )
        df_spec_out.to_excel(writer, sheet_name="metrics_by_specialization", index=False)
        df_grade_out = (
            df_grade.sort_values("vacancy_count", ascending=False)
            if not df_grade.empty and "vacancy_count" in df_grade.columns
            else df_grade
        )
        df_grade_out.to_excel(writer, sheet_name="metrics_by_grade", index=False)
        df_loc.to_excel(writer, sheet_name="metrics_by_location", index=False)
        df_work.to_excel(writer, sheet_name="metrics_by_work_format", index=False)
        df_tech_out = (
            df_tech.sort_values("count", ascending=False)
            if not df_tech.empty and "count" in df_tech.columns
            else df_tech
        )
        df_tech_out.to_excel(writer, sheet_name="metrics_by_technology", index=False)

        wb = writer.book
        _apply_openpyxl_formatting(wb, vacancy_sheets)
        # метрики: только ширина, без wrap
        for meta in (
            "metrics_summary",
            "metrics_by_specialization",
            "metrics_by_grade",
            "metrics_by_location",
            "metrics_by_work_format",
            "metrics_by_technology",
        ):
            if meta in wb.sheetnames:
                _apply_openpyxl_formatting(wb, [meta])

    return path

"""Сортировка вакансий для отчётов (Excel/JSON/таблицы)."""

from __future__ import annotations

from datetime import timezone

from tg_jobs_parser.models.vacancy import Vacancy


def vacancy_has_salary(v: Vacancy) -> bool:
    return (
        v.detected_salary_min is not None
        or v.detected_salary_max is not None
        or v.detected_salary_avg is not None
    )


def vacancy_post_timestamp(v: Vacancy) -> float:
    """Время поста для сравнения; без даты — в конец списка."""
    if v.post_date is None:
        return float("-inf")
    d = v.post_date
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).timestamp()


def vacancy_data_richness(v: Vacancy) -> int:
    """
    Полнота данных среди строк с одинаковой датой/времени:
    указана зарплата → выше; затем указан формат работы; затем указана страна.
    """
    s = 0
    if vacancy_has_salary(v):
        s += 100
    if (v.work_format or "").strip():
        s += 40
    if (v.country or "").strip():
        s += 10
    return s


def _sort_key_report(v: Vacancy) -> tuple:
    ts = vacancy_post_timestamp(v)
    neg_ts = -ts if ts != float("-inf") else float("inf")
    wf = (v.work_format or "").strip().lower()
    co = (v.country or "").strip().lower()
    return (
        neg_ts,
        -vacancy_data_richness(v),
        wf,
        co,
        v.id or "",
    )


def sort_vacancies_for_report(vacancies: list[Vacancy]) -> None:
    """
    На месте: **свежее** (позже по ``post_date``) сверху; при равной дате/времени выше строки с
    зарплатой, с заполненным ``work_format`` и ``country``, затем алфавит по формату и стране.
    """
    vacancies.sort(key=_sort_key_report)

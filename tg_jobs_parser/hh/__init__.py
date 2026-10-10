"""Клиент и пайплайн HH.ru (поиск + detail → Vacancy, без LLM)."""

from tg_jobs_parser.hh.client import HH_VACANCIES_DEPTH_LIMIT, HHApiError, HHClient
from tg_jobs_parser.hh.dedup import DedupedVacancy, dedupe_vacancies, vacancy_key
from tg_jobs_parser.hh.models import (
    VacancyDetail,
    VacancySearchParams,
    VacancySearchResponse,
    VacancyShort,
)

__all__ = [
    "HH_VACANCIES_DEPTH_LIMIT",
    "DedupedVacancy",
    "HHApiError",
    "HHClient",
    "VacancyDetail",
    "VacancySearchParams",
    "VacancySearchResponse",
    "VacancyShort",
    "dedupe_vacancies",
    "vacancy_key",
]

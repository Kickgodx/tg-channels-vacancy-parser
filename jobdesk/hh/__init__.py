"""Клиент и пайплайн HH.ru (поиск + detail → Vacancy, без LLM)."""

from jobdesk.hh.client import HH_VACANCIES_DEPTH_LIMIT, HHApiError, HHClient
from jobdesk.hh.dedup import DedupedVacancy, dedupe_vacancies, vacancy_key
from jobdesk.hh.models import (
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

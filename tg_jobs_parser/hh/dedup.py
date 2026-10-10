"""Дедуп мультигородных вакансий HH (порт логики hhparser)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from tg_jobs_parser.hh.models import VacancyShort

_HIGHLIGHT_RE = re.compile(r"</?highlighttext>", re.IGNORECASE)
_FIELD_SEP = "\x1f"


@dataclass(slots=True)
class DedupedVacancy:
    """Канон (первое появление) + копии в других городах."""

    id: str
    name: str
    employer: str | None
    area: str | None
    url: str | None
    duplicates: list[tuple[str, str | None]] = field(default_factory=list)

    @classmethod
    def from_vacancy(cls, vacancy: VacancyShort) -> DedupedVacancy:
        return cls(
            id=vacancy.id,
            name=vacancy.name,
            employer=vacancy.employer.name if vacancy.employer else None,
            area=vacancy.area.name if vacancy.area else None,
            url=vacancy.alternate_url,
        )


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(_HIGHLIGHT_RE.sub("", value).lower().split())


def vacancy_key(vacancy: VacancyShort) -> bytes:
    """
    16-байтный хэш содержимого.

    Город, адрес, дата и id не участвуют; формат работы, зарплата и опыт — участвуют.
    """
    snippet = vacancy.snippet
    parts = (
        _employer_part(vacancy),
        normalize_text(vacancy.name),
        normalize_text(snippet.requirement if snippet else None),
        normalize_text(snippet.responsibility if snippet else None),
        ",".join(sorted(item.id or "" for item in vacancy.work_format)),
        _salary_part(vacancy),
        (vacancy.experience.id or "") if vacancy.experience else "",
    )
    return hashlib.blake2b(_FIELD_SEP.join(parts).encode("utf-8"), digest_size=16).digest()


def vacancy_key_hex(vacancy: VacancyShort) -> str:
    return vacancy_key(vacancy).hex()


def dedupe_vacancies(vacancies: Iterable[VacancyShort]) -> list[DedupedVacancy]:
    unique: dict[bytes, DedupedVacancy] = {}
    for vacancy in vacancies:
        key = vacancy_key(vacancy)
        kept = unique.get(key)
        if kept is None:
            unique[key] = DedupedVacancy.from_vacancy(vacancy)
        else:
            kept.duplicates.append((vacancy.id, vacancy.area.name if vacancy.area else None))
    return list(unique.values())


def _employer_part(vacancy: VacancyShort) -> str:
    employer = vacancy.employer
    if employer is None:
        return ""
    if employer.id:
        return f"id:{employer.id}"
    return f"name:{normalize_text(employer.name)}"


def _salary_part(vacancy: VacancyShort) -> str:
    salary = vacancy.salary_range or vacancy.salary
    if salary is None:
        return ""
    return f"{salary.from_}|{salary.to}|{salary.currency}|{salary.gross}"

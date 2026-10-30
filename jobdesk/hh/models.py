"""Pydantic-модели ответов HH API (урезанные под наш sync)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HHModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class IdName(HHModel):
    id: str | None = None
    name: str | None = None


class Area(IdName):
    url: str | None = None


class Salary(HHModel):
    from_: int | None = Field(default=None, alias="from")
    to: int | None = None
    currency: str | None = None
    gross: bool | None = None


class SalaryRange(Salary):
    mode: IdName | None = None
    frequency: IdName | None = None


class Employer(HHModel):
    id: str | None = None
    name: str | None = None
    url: str | None = None
    alternate_url: str | None = None
    country_id: int | None = None
    trusted: bool | None = None


class Snippet(HHModel):
    requirement: str | None = None
    responsibility: str | None = None


class KeySkill(HHModel):
    name: str


class Phone(HHModel):
    country: str | None = None
    city: str | None = None
    number: str | None = None
    formatted: str | None = None
    comment: str | None = None


class Contacts(HHModel):
    name: str | None = None
    email: str | None = None
    phones: list[Phone] = Field(default_factory=list)


class Address(HHModel):
    city: str | None = None
    street: str | None = None
    building: str | None = None
    raw: str | None = None


class VacancyTest(HHModel):
    required: bool | None = None


class VacancySearchParams(HHModel):
    """Параметры GET /vacancies (query)."""

    text: str | None = None
    area: str | list[str] | None = None
    professional_role: str | list[str] | None = ["124", "96"]
    experience: str | list[str] | None = None
    employment_form: str | list[str] | None = None
    work_format: str | list[str] | None = None
    schedule: str | list[str] | None = None
    salary: int | None = None
    currency: str | None = None
    only_with_salary: bool | None = None
    period: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    order_by: str | None = None
    page: int = 0
    per_page: int = Field(default=100, ge=1, le=100)
    host: str | None = None

    def to_query(self) -> dict[str, Any]:
        raw = self.model_dump(mode="python", exclude_none=True)
        params: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, list):
                params[key] = [str(item) for item in value]
            elif isinstance(value, bool):
                params[key] = str(value).lower()
            else:
                params[key] = value
        return params


class VacancyShort(HHModel):
    id: str
    name: str
    url: str | None = None
    alternate_url: str | None = None
    apply_alternate_url: str | None = None
    published_at: str | None = None
    created_at: str | None = None
    archived: bool | None = None
    has_test: bool | None = None
    response_letter_required: bool | None = None
    area: Area | None = None
    salary: Salary | None = None
    salary_range: SalaryRange | None = None
    employer: Employer | None = None
    snippet: Snippet | None = None
    experience: IdName | None = None
    employment_form: IdName | None = None
    work_format: list[IdName] = Field(default_factory=list)
    type: IdName | None = None
    professional_roles: list[IdName] = Field(default_factory=list)


class VacancySearchResponse(HHModel):
    found: int = 0
    pages: int = 0
    page: int = 0
    per_page: int = 0
    items: list[VacancyShort] = Field(default_factory=list)
    alternate_url: str | None = None


class VacancyDetail(HHModel):
    id: str
    name: str
    description: str | None = None
    alternate_url: str | None = None
    apply_alternate_url: str | None = None
    apply_url: str | None = None
    published_at: str | None = None
    archived: bool | None = None
    closed_for_applicants: bool | None = None
    type: IdName | None = None
    area: Area | None = None
    address: Address | None = None
    professional_roles: list[IdName] = Field(default_factory=list)
    experience: IdName | None = None
    employment_form: IdName | None = None
    work_format: list[IdName] = Field(default_factory=list)
    salary_range: SalaryRange | None = None
    employer: Employer | None = None
    contacts: Contacts | None = None
    key_skills: list[KeySkill] = Field(default_factory=list)
    has_test: bool | None = None
    test: VacancyTest | None = None
    response_letter_required: bool | None = None

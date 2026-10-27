"""Что изменилось в перепосте вакансии по сравнению с первой публикацией.

Сравниваются только поля, у которых правка — сигнал, а не шум разбора: зарплата в валюте
поста (не в рублях — иначе разница курсов на разные даты выглядит как изменение), формат
работы, город, технологии. Страна не сравнивается: LLM пишет одну страну по-разному
(«Russia», «Россия», «RU», «РФ»), на живой базе это были все её «изменения». Пропавшее
значение изменением не считается: агрегатор часто перепечатывает пост без зарплаты или
города, а правила могут их не найти. Технология
считается добавленной или убранной, только если её название действительно появилось в тексте
или ушло из него: список LLM на почти том же тексте может отличаться и без правки.
"""

from __future__ import annotations

from typing import Protocol

from tg_jobs_parser.analytics.skill_gaps import mentions


class _Fields(Protocol):
    cleaned_text: str
    detected_salary_min: float | None
    detected_salary_max: float | None
    salary_currency: str | None
    work_format: str | None
    city: str | None
    technologies: list[str]


def _salary(v: _Fields) -> dict | None:
    if v.detected_salary_min is None and v.detected_salary_max is None:
        return None
    return {"min": v.detected_salary_min, "max": v.detected_salary_max, "currency": v.salary_currency}


def _same_text_value(a: str | None, b: str | None) -> bool:
    return " ".join((a or "").lower().split()) == " ".join((b or "").lower().split())


def vacancy_changes(original: _Fields, repost: _Fields) -> list[dict]:
    """Изменения перепоста: ``[{"field", "old", "new"}]`` (у технологий — ``added`` / ``removed``)."""
    out: list[dict] = []
    old_salary, new_salary = _salary(original), _salary(repost)
    if new_salary is not None and new_salary != old_salary:
        out.append({"field": "salary", "old": old_salary, "new": new_salary})
    for field in ("work_format", "city"):
        old, new = getattr(original, field), getattr(repost, field)
        if new and not _same_text_value(old, new):
            out.append({"field": field, "old": old, "new": new})
    old_techs = {t.lower(): t for t in original.technologies or []}
    new_techs = {t.lower(): t for t in repost.technologies or []}
    added = sorted(
        t
        for k, t in new_techs.items()
        if k not in old_techs and mentions(repost.cleaned_text, t) and not mentions(original.cleaned_text, t)
    )
    removed = sorted(
        t
        for k, t in old_techs.items()
        if k not in new_techs and mentions(original.cleaned_text, t) and not mentions(repost.cleaned_text, t)
    )
    if added or removed:
        out.append({"field": "technologies", "added": added, "removed": removed})
    return out


def is_edited_repost(reason: str | None) -> bool:
    """Нечёткий дубль: текст отличается, есть что сравнивать. У совпадения по хэшу текст тот же."""
    return bool(reason) and reason.split(":")[0] in ("fuzzy", "fuzzy_text")

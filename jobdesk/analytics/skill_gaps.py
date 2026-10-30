"""Сводка недостающих навыков резюме по сохранённым оценкам матчинга.

Навыки в ``missing_skills`` — свободный текст LLM («MySQL / ClickHouse», «Go (5+ лет)»),
поэтому агрегировать по строке нельзя: технологии ищутся в тексте по словарю и сводятся к
каноническим именам, остальное группируется по нормализованному тексту.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from jobdesk.config.schema import TECHNOLOGY_ALIASES, canonical_technologies

IMPORTANCE_RANK = {"critical": 3, "important": 2, "secondary": 1}
MAX_TECHNOLOGIES = 30
MAX_OTHER = 20
MAX_EXAMPLES = 3

_WORD = "a-z0-9а-я"
_PARENS_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_SPACES_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    return _SPACES_RE.sub(" ", t).strip()


class TechnologyMatcher:
    """Поиск технологий в коротком тексте: целые слова, длинные имена раньше вложенных."""

    def __init__(self, names: Iterable[str]) -> None:
        canon: dict[str, str] = {}
        for name in names:
            key = _norm(name)
            if key:
                canon.setdefault(key, canonical_technologies([name])[0])
        for alias, target in TECHNOLOGY_ALIASES.items():
            canon.setdefault(_norm(alias), target)
        self._patterns = [
            (re.compile(rf"(?<![{_WORD}]){re.escape(key)}(?![{_WORD}])"), name)
            for key, name in sorted(canon.items(), key=lambda kv: (-len(kv[0]), kv[0]))
        ]

    def find(self, text: str) -> list[str]:
        t = _norm(text)
        taken = [False] * len(t)
        found: list[str] = []
        for pattern, name in self._patterns:
            for m in pattern.finditer(t):
                if any(taken[m.start() : m.end()]):
                    continue
                taken[m.start() : m.end()] = [True] * (m.end() - m.start())
                if name not in found:
                    found.append(name)
        return found


def mentions(text: str, name: str) -> bool:
    """Название встречается в тексте целым словом (без учёта регистра и «ё»).

    ``TechnologyMatcher`` берёт самое длинное совпадение: в «Atlassian Jira» он найдёт
    «Atlassian Jira», а не «Jira». Проверка одной технологии — без этой конкуренции.
    """
    key = _norm(name)
    return bool(key) and re.search(rf"(?<![{_WORD}]){re.escape(key)}(?![{_WORD}])", _norm(text)) is not None


def other_skill_label(skill: str) -> str:
    """Навык без технологий: без пояснений в скобках, одиночные пробелы."""
    text = _SPACES_RE.sub(" ", _PARENS_RE.sub(" ", skill)).strip(" .,;:-—")
    return text


@dataclass
class MatchSkills:
    """Недостающие навыки одной оценки (порядок — от свежих оценок к старым)."""

    vacancy_id: str
    missing_skills: Sequence[dict]


@dataclass
class SkillGap:
    name: str
    vacancies: int = 0
    critical: int = 0
    important: int = 0
    secondary: int = 0
    vacancy_ids: list[str] = field(default_factory=list)

    @property
    def weight(self) -> int:
        return 3 * self.critical + 2 * self.important + self.secondary


@dataclass
class SkillGapSummary:
    evaluated: int
    technologies: list[SkillGap]
    other: list[SkillGap]


def _importance(item: dict) -> str:
    value = str(item.get("importance") or "important").lower()
    return value if value in IMPORTANCE_RANK else "important"


def _add(groups: dict[str, SkillGap], key: str, name: str, vacancy_id: str, importance: str) -> None:
    gap = groups.get(key)
    if gap is None:
        gap = groups[key] = SkillGap(name=name)
    gap.vacancies += 1
    setattr(gap, importance, getattr(gap, importance) + 1)
    if len(gap.vacancy_ids) < MAX_EXAMPLES:
        gap.vacancy_ids.append(vacancy_id)


def _ordered(groups: dict[str, SkillGap], limit: int) -> list[SkillGap]:
    items = sorted(groups.values(), key=lambda g: (-g.weight, -g.vacancies, g.name.lower()))
    return items[:limit]


def summarize_skill_gaps(matches: Sequence[MatchSkills], matcher: TechnologyMatcher) -> SkillGapSummary:
    """Сводка по оценкам: у вакансии на каждую запись — один голос с наивысшим приоритетом."""
    tech_groups: dict[str, SkillGap] = {}
    other_groups: dict[str, SkillGap] = {}
    for match in matches:
        tech_best: dict[str, tuple[str, str]] = {}
        other_best: dict[str, tuple[str, str]] = {}
        for raw in match.missing_skills or []:
            if not isinstance(raw, dict):
                continue
            skill = str(raw.get("skill") or "").strip()
            if not skill:
                continue
            importance = _importance(raw)
            techs = matcher.find(skill)
            if techs:
                targets = [(t.lower(), t) for t in techs]
                best = tech_best
            else:
                label = other_skill_label(skill)
                if not label:
                    continue
                targets = [(_norm(label), label)]
                best = other_best
            for key, name in targets:
                prev = best.get(key)
                if prev is None or IMPORTANCE_RANK[importance] > IMPORTANCE_RANK[prev[1]]:
                    best[key] = (prev[0] if prev else name, importance)
        for key, (name, importance) in tech_best.items():
            _add(tech_groups, key, name, match.vacancy_id, importance)
        for key, (name, importance) in other_best.items():
            _add(other_groups, key, name, match.vacancy_id, importance)
    return SkillGapSummary(
        evaluated=len(matches),
        technologies=_ordered(tech_groups, MAX_TECHNOLOGIES),
        other=_ordered(other_groups, MAX_OTHER),
    )

"""Контекст активного поиска для промпта."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jobdesk.config.schema import AppConfig


@dataclass(frozen=True)
class SearchContext:
    target_specializations: list[str]
    target_work_formats: list[str]
    exclude_keywords: list[str]
    allowed_specializations: list[str]
    allowed_work_formats: list[str]
    any_specialization: bool = False
    target_technologies: tuple[str, ...] = ()

    def to_prompt_block(self) -> str:
        if self.any_specialization:
            target_line = (
                "  target_specializations: (any IT role — backend, frontend, mobile, "
                "QA, DevOps, AI/ML, data, PHP, Rust, etc.)"
            )
            policy = (
                "ANY_IT_SPECIALIZATION=true: This is an IT job aggregator run. "
                "Set is_job_post=true for genuine IT hiring posts regardless of stack. "
                "Do NOT set is_job_post=false only because the role (e.g. PHP, Ruby, C#) "
                "is outside a fixed target list. Reject resumes, courses, spam, non-IT ads."
            )
        else:
            target_line = f"  target_specializations: {self.target_specializations or '(any)'}"
            policy = (
                "Prefer roles matching target_specializations when listed; "
                "still accept clear IT vacancies with other stacks unless clearly off-topic."
            )

        lines = [
            "SEARCH_CONTEXT (what the user is looking for; do not invent facts to match it):",
            target_line,
            f"  target_technologies: {list(self.target_technologies) or '(any)'}",
            f"  target_work_formats: {self.target_work_formats or '(any)'}",
            f"  exclude_keywords: {self.exclude_keywords[:30]}{'...' if len(self.exclude_keywords) > 30 else ''}",
            policy,
            "ALLOWED_SPECIALIZATIONS (roles; prefer these canonical names; you may add new role names "
            "if clearly stated, never languages or tools):",
            f"  {', '.join(self.allowed_specializations[:80])}",
            f"ALLOWED_WORK_FORMATS: {', '.join(self.allowed_work_formats)}",
        ]
        return "\n".join(lines)


def build_search_context(config: AppConfig) -> SearchContext:
    any_it = bool(config.filters.any_specialization)
    if any_it:
        target_specs: list[str] = []
    elif config.filters.specializations:
        target_specs = list(config.filters.specializations)
    else:
        target_specs = list(config.merged_specializations().keys())
    wf = config.filters.work_formats or ["any"]
    return SearchContext(
        target_specializations=target_specs,
        target_work_formats=list(wf),
        exclude_keywords=config.merged_exclude_keywords(),
        allowed_specializations=list(config.merged_specializations().keys()),
        allowed_work_formats=["remote", "hybrid", "office", "relocation", "unknown"],
        any_specialization=any_it,
        target_technologies=tuple(config.filters.technologies),
    )

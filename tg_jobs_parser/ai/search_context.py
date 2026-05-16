"""Контекст активного поиска для промпта."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tg_jobs_parser.config.schema import AppConfig


@dataclass(frozen=True)
class SearchContext:
    target_specializations: list[str]
    target_work_formats: list[str]
    exclude_keywords: list[str]
    allowed_specializations: list[str]
    allowed_work_formats: list[str]

    def to_prompt_block(self) -> str:
        lines = [
            "SEARCH_CONTEXT (what the user is looking for; do not invent facts to match it):",
            f"  target_specializations: {self.target_specializations or '(any)'}",
            f"  target_work_formats: {self.target_work_formats or '(any)'}",
            f"  exclude_keywords: {self.exclude_keywords[:30]}{'...' if len(self.exclude_keywords) > 30 else ''}",
            "ALLOWED_SPECIALIZATIONS (prefer these canonical names; you may add new role names if clearly stated):",
            f"  {', '.join(self.allowed_specializations[:80])}",
            f"ALLOWED_WORK_FORMATS: {', '.join(self.allowed_work_formats)}",
        ]
        return "\n".join(lines)


def build_search_context(config: AppConfig) -> SearchContext:
    specs = config.filters.specializations
    if not specs:
        specs = list(config.merged_specializations().keys())
    wf = config.filters.work_formats or ["any"]
    return SearchContext(
        target_specializations=list(specs),
        target_work_formats=list(wf),
        exclude_keywords=config.merged_exclude_keywords(),
        allowed_specializations=list(config.merged_specializations().keys()),
        allowed_work_formats=["remote", "hybrid", "office", "relocation", "unknown"],
    )

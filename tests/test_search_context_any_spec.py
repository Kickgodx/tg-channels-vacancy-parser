"""Контекст поиска: режим «любая IT-специализация»."""

from tg_jobs_parser.ai.search_context import build_search_context
from tg_jobs_parser.config.schema import AppConfig, FiltersConfig


def test_any_specialization_empty_target_and_prompt_flag():
    cfg = AppConfig(
        filters=FiltersConfig(
            specializations=["Python", "Go"],
            any_specialization=True,
        )
    )
    ctx = build_search_context(cfg)
    assert ctx.any_specialization is True
    assert ctx.target_specializations == []
    block = ctx.to_prompt_block()
    assert "ANY_IT_SPECIALIZATION=true" in block
    assert "PHP" in block


def test_explicit_specs_keep_targets():
    cfg = AppConfig(filters=FiltersConfig(specializations=["Go"], any_specialization=False))
    ctx = build_search_context(cfg)
    assert ctx.target_specializations == ["Go"]
    assert "ANY_IT_SPECIALIZATION=true" not in ctx.to_prompt_block()

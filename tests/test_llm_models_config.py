"""Единый каталог llm_models.yaml."""

import pytest

from tg_jobs_parser.ai.llm_models_registry import (
    load_llm_models_registry,
    resolve_llm_models_path,
)
from tg_jobs_parser.ai.models_catalog import label_for_model_id
from tg_jobs_parser.config.loader import ConfigLoadError, load_config


def test_llm_models_yaml_exists_and_has_gemini():
    reg = load_llm_models_registry("config.yaml")
    ids = reg.ids()
    assert "google/gemini-3.1-flash-lite" in ids
    entry = next(m for m in reg.models if m.id == "google/gemini-3.1-flash-lite")
    assert entry.name == "Gemini 3.1 Flash Lite"
    assert entry.cost == "0.25/1.5"
    assert "0.25/1.5" in entry.ui_label()


def test_config_loads_catalog_from_registry():
    reg_path = resolve_llm_models_path("config.yaml")
    reg = load_llm_models_registry(reg_path)
    cfg = load_config("config.yaml")

    assert len(cfg.llm_model_catalog) >= 3
    catalog_ids = {m.id for m in cfg.llm_model_catalog}
    assert catalog_ids == reg.ids()
    assert cfg.ai.model in catalog_ids
    # config.yaml задаёт ai.model явно (сейчас совпадает с default_model реестра)
    assert cfg.ai.model == reg.default_model
    label = label_for_model_id(cfg.llm_model_catalog, "google/gemini-3.1-flash-lite")
    assert "Gemini" in label


def test_resolve_llm_models_path_next_to_config():
    p = resolve_llm_models_path("config.yaml")
    assert p.name == "llm_models.yaml"
    assert p.is_file()


def test_project_config_models_are_all_in_catalog():
    cfg = load_config("config.yaml")
    catalog_ids = {m.id for m in cfg.llm_model_catalog}
    assert set(cfg.ai.resolved_models()) <= catalog_ids


def _config_with_ai(tmp_path, ai_yaml: str):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("ai:\n  model: anthropic/claude-haiku-5.5\n" + ai_yaml, encoding="utf-8")
    return cfg_file


@pytest.mark.parametrize("field", ["match_model", "cover_letter_model"])
def test_match_and_letter_models_outside_catalog_are_rejected(tmp_path, field):
    cfg_file = _config_with_ai(tmp_path, f"  {field}: nobody/missing-model\n")

    with pytest.raises(ConfigLoadError) as exc:
        load_config(cfg_file)

    assert f"ai.{field}" in str(exc.value)
    assert "nobody/missing-model" in str(exc.value)


def test_fallback_model_outside_catalog_is_rejected(tmp_path):
    cfg_file = _config_with_ai(
        tmp_path, "  fallback_models:\n    - google/gemini-3.1-flash-lite\n    - nobody/missing-model\n"
    )

    with pytest.raises(ConfigLoadError) as exc:
        load_config(cfg_file)

    assert "ai.fallback_models" in str(exc.value)
    assert "nobody/missing-model" in str(exc.value)


def test_legacy_fallback_model_outside_catalog_is_rejected(tmp_path):
    cfg_file = _config_with_ai(tmp_path, "  fallback_model: nobody/missing-model\n")

    with pytest.raises(ConfigLoadError, match="nobody/missing-model"):
        load_config(cfg_file)


def test_main_model_outside_catalog_is_config_load_error(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("ai:\n  model: nobody/missing-model\n", encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="nobody/missing-model"):
        load_config(cfg_file)

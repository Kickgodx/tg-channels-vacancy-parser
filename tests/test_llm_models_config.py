"""Единый каталог llm_models.yaml."""

from tg_jobs_parser.ai.llm_models_registry import (
    load_llm_models_registry,
    resolve_llm_models_path,
)
from tg_jobs_parser.ai.models_catalog import label_for_model_id
from tg_jobs_parser.config.loader import load_config


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

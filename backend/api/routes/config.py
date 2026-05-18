from __future__ import annotations

from fastapi import APIRouter, HTTPException

from tg_jobs_parser.config.loader import ConfigLoadError, load_config
from tg_jobs_parser.config.schema import DEFAULT_SPECIALIZATIONS
from tg_jobs_parser.extraction.grade_extractor import GRADE_OPTIONS
from tg_jobs_parser.ai.llm_models_registry import load_llm_models_registry, resolve_llm_models_path
from tg_jobs_parser.ai.models_catalog import catalog_model_ids, label_for_model_id
from tg_jobs_parser.utils.dates import parse_date, resolve_search_date_to
from backend.settings import get_settings

router = APIRouter(prefix="/config", tags=["config"])

WORK_FORMAT_OPTIONS = ["any", "remote", "hybrid", "office", "relocation", "exclude_office"]


@router.get("")
def get_config() -> dict:
    settings = get_settings()
    try:
        cfg = load_config(settings.config_path)
    except ConfigLoadError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    data = cfg.to_serializable_dict()
    data.pop("llm_model_catalog", None)
    return data


@router.get("/defaults")
def get_config_defaults() -> dict:
    """Значения по умолчанию для формы запуска в React UI."""
    settings = get_settings()
    try:
        cfg = load_config(settings.config_path)
    except ConfigLoadError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    df = parse_date(cfg.search.date_from)
    dt = resolve_search_date_to(cfg.search.date_to)
    return {
        "channels": list(cfg.sources.channels),
        "search": {
            "date_from": df.isoformat() if df else None,
            "date_to": cfg.search.date_to if cfg.search.date_to else "today",
            "date_to_resolved": dt.isoformat(),
            "max_days": cfg.search.max_days,
        },
        "filters": {
            "specializations": list(cfg.filters.specializations),
            "any_specialization": bool(getattr(cfg.filters, "any_specialization", False)),
            "exclude_keywords": list(cfg.filters.exclude_keywords),
            "countries": list(cfg.filters.countries),
            "cities": list(cfg.filters.cities),
            "work_formats": list(cfg.filters.work_formats),
        },
        "parser": {
            "vacancy_score_threshold": cfg.parser.vacancy_score_threshold,
            "vacancy_min_substantive_score": cfg.parser.vacancy_min_substantive_score,
        },
        "ai": {
            "enabled": cfg.ai.enabled,
            "model": cfg.ai.model,
            "fallback_models": list(cfg.ai.fallback_models or []),
            "use_cache": cfg.ai.use_cache,
        },
        "telegram": {"telethon_session_file": cfg.telegram.telethon_session_file},
        "specialization_options": list(DEFAULT_SPECIALIZATIONS.keys()),
        "work_format_options": WORK_FORMAT_OPTIONS,
        "grade_options": list(GRADE_OPTIONS),
    }


@router.get("/llm-models")
def get_llm_models() -> dict:
    settings = get_settings()
    path = resolve_llm_models_path(settings.config_path)
    reg = load_llm_models_registry(path)
    catalog = reg.catalog()
    ids = catalog_model_ids(catalog)
    return {
        "default_model": reg.default_model,
        "default_fallbacks": list(reg.default_fallbacks),
        "models": [
            {
                "id": m.id,
                "label": label_for_model_id(catalog, m.id),
                "provider": getattr(m, "provider", None),
            }
            for m in catalog
        ],
        "model_ids": ids,
    }

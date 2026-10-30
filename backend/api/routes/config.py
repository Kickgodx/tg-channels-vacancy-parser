from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import get_current_user, verify_api_key
from backend.settings import get_settings
from jobdesk.ai.llm_models_registry import (
    load_llm_models_registry,
    resolve_llm_models_path,
)
from jobdesk.ai.models_catalog import catalog_model_ids, label_for_model_id
from jobdesk.config.loader import load_config
from jobdesk.config.schema import DEFAULT_SPECIALIZATIONS
from jobdesk.extraction.grade_extractor import GRADE_OPTIONS
from jobdesk.utils.dates import resolve_search_date_from, resolve_search_date_to

router = APIRouter(
    prefix="/config",
    tags=["config"],
    dependencies=[Depends(verify_api_key), Depends(get_current_user)],
)

WORK_FORMAT_OPTIONS = [
    "any",
    "remote",
    "hybrid",
    "office",
    "relocation",
    "exclude_office",
]


@router.get("")
def get_config() -> dict:
    # Ошибку загрузки отвечает общий обработчик: 500 CONFIG_INVALID.
    cfg = load_config(get_settings().config_path)
    data = cfg.to_serializable_dict()
    data.pop("llm_model_catalog", None)
    return data


@router.get("/defaults")
def get_config_defaults() -> dict:
    """Значения по умолчанию для формы запуска в React UI."""
    cfg = load_config(get_settings().config_path)

    df = resolve_search_date_from(cfg.search.date_from)
    dt = resolve_search_date_to(cfg.search.date_to)
    return {
        "channels": list(cfg.sources.channels),
        "search": {
            "date_from": cfg.search.date_from if cfg.search.date_from else "week_ago",
            "date_from_resolved": df.isoformat() if df else None,
            "date_to": cfg.search.date_to if cfg.search.date_to else "today",
            "date_to_resolved": dt.isoformat(),
            "max_days": cfg.search.max_days,
        },
        "filters": {
            "specializations": list(cfg.filters.specializations),
            "any_specialization": bool(getattr(cfg.filters, "any_specialization", False)),
            "technologies": list(cfg.filters.technologies),
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
            "cache_backend": cfg.ai.cache_backend,
            "cache_ttl_days": cfg.ai.cache_ttl_days,
        },
        "telegram": {"telethon_session_file": cfg.telegram.telethon_session_file},
        "specialization_options": list(DEFAULT_SPECIALIZATIONS.keys()),
        "technology_options": cfg.merged_technologies(),
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

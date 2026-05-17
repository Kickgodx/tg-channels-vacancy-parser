"""Типы и утилиты каталога LLM (данные — в ``llm_models.yaml``, см. ``llm_models_registry``)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LlmModelEntry(BaseModel):
    """Модель для выбора в UI: id OpenRouter, отображаемое имя, ориентир по цене."""

    id: str = Field(min_length=1, description="Идентификатор модели OpenRouter")
    name: str = Field(min_length=1, description="Название в UI")
    cost: str = Field(
        default="",
        description="USD за 1M токенов input/output, напр. 0.25/1.5",
    )

    def ui_label(self) -> str:
        if self.cost:
            return f"{self.name} — ${self.cost} /1M ({self.id})"
        return f"{self.name} ({self.id})"


def dedupe_model_ids(model_ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for mid in model_ids:
        m = (mid or "").strip()
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def catalog_model_ids(catalog: list[LlmModelEntry]) -> list[str]:
    return [e.id for e in catalog]


def label_for_model_id(catalog: list[LlmModelEntry], model_id: str) -> str:
    for entry in catalog:
        if entry.id == model_id:
            return entry.ui_label()
    return model_id

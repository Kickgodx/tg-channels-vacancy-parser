"""Утилиты каталога моделей."""

from jobdesk.ai.models_catalog import LlmModelEntry


def test_llm_model_entry_ui_label():
    e = LlmModelEntry(id="x/y", name="Test", cost="1/2")
    assert "Test" in e.ui_label()
    assert "1/2" in e.ui_label()

"""Обычный прогон тестов не ходит в настоящий OpenRouter."""

from __future__ import annotations

import os

import pytest


def test_openrouter_key_blanked_outside_live_llm_tests():
    assert os.environ.get("OPENROUTER_API_KEY") == ""
    from backend.services.interactive_llm import get_interactive_client, reset_interactive_client

    reset_interactive_client()
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        get_interactive_client()

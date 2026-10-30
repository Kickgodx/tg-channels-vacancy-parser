"""Локальное подтягивание переменных из ``.env`` (если установлен python-dotenv)."""

from __future__ import annotations


def try_load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

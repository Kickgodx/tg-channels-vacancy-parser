"""Пути к golden/fixtures относительно корня репозитория."""

from __future__ import annotations

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parents[1]


def golden_extraction_path() -> Path:
    return _REPO_ROOT / "evals" / "golden" / "extraction.jsonl"


def fixtures_extraction_path() -> Path:
    return _REPO_ROOT / "evals" / "fixtures" / "extraction_outputs.json"


def reports_dir() -> Path:
    return _REPO_ROOT / "evals" / "reports"

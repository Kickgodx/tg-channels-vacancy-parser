"""Политика стадий ingest для разных источников (Telegram / manual)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IngestPolicy:
    """Управляет gate'ами после rules / AI.

    - Telegram: post-filters + порог score без AI.
    - Manual import: без post-filters; AI skip_below обычно 0 (через config AI-сервиса).
    """

    apply_post_filters: bool = True
    """Если True — ``apply_post_filters`` (спеки, exclude, формат, локация, score)."""

    score_threshold_without_ai: int | None = None
    """Если AI выключен и score ниже порога — отбросить (None = не проверять здесь)."""


TELEGRAM_POLICY = IngestPolicy(apply_post_filters=True, score_threshold_without_ai=None)
"""Порог без AI задаётся вызывающим из ``config.parser.vacancy_score_threshold``."""

MANUAL_POLICY = IngestPolicy(apply_post_filters=False, score_threshold_without_ai=None)

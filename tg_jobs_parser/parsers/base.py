"""Базовый интерфейс парсера канала."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from tg_jobs_parser.config.schema import AppConfig


@dataclass
class RawPost:
    """Сырой пост из источника."""

    channel_username: str
    post_id: str
    text: str
    post_date: datetime | None
    post_url: str | None


class BaseChannelParser(ABC):
    """Абстракция источника постов (web, будущий Telegram API)."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @abstractmethod
    def fetch_posts(self, channel_username: str) -> tuple[list[RawPost], str | None]:
        """
        Возвращает список постов и предупреждение об ограничениях (если есть).
        ``channel_username`` — без ``@``.
        """

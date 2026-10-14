"""Базовый интерфейс парсера канала."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from tg_jobs_parser.config.schema import AppConfig

if TYPE_CHECKING:
    from tg_jobs_parser.parse_cancel import ParseCancellationToken


@dataclass
class RawPost:
    """Сырой пост из источника."""

    channel_username: str
    post_id: str
    text: str
    post_date: datetime | None
    post_url: str | None


@dataclass(frozen=True)
class ChannelCheckpoint:
    """Сплошное покрытие канала: прочитаны все сообщения с ``covered_from`` по ``last_message_id``.

    Следующий прогон с ``date_from >= covered_from`` (и окном до «сегодня») читает
    только сообщения новее ``last_message_id`` (``min_id`` в Telethon).
    """

    last_message_id: int
    covered_from: date


class BaseChannelParser(ABC):
    """Абстракция источника постов (Telethon и др.)."""

    def __init__(
        self,
        config: AppConfig,
        *,
        cancellation: ParseCancellationToken | None = None,
    ) -> None:
        self.config = config
        self.cancellation = cancellation
        # Чекпоинты на вход (из БД) и новые — по каналам, прочитанным сплошь от новых
        # сообщений до границы окна. Парсеры без поддержки их просто не заполняют.
        self.checkpoints: dict[str, ChannelCheckpoint] = {}
        self.new_checkpoints: dict[str, ChannelCheckpoint] = {}

    @abstractmethod
    def fetch_posts(self, channel_username: str) -> tuple[list[RawPost], str | None]:
        """
        Возвращает список постов и предупреждение об ограничениях (если есть).
        ``channel_username`` — без ``@``.
        """

    def fetch_posts_many(self, channel_usernames: list[str]) -> list[tuple[str, list[RawPost], str | None]]:
        """
        Пакетный вариант: вернуть результаты по нескольким каналам.

        Дефолтная реализация — последовательный fallback через ``fetch_posts``.
        Конкретные парсеры (Telethon) переопределяют для распараллеливания.
        """
        out: list[tuple[str, list[RawPost], str | None]] = []
        for ch in channel_usernames:
            if self.cancellation:
                self.cancellation.check()
            posts, warn = self.fetch_posts(ch)
            out.append((ch, posts, warn))
        return out

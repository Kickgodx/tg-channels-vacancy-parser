"""Заглушка будущего адаптера Telethon/Pyrogram."""

from __future__ import annotations

from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.parsers.base import BaseChannelParser, RawPost


class TelegramApiPlaceholderParser(BaseChannelParser):
    """
    Перенаправляет на живую реализацию: см. ``TelegramTelethonParser``.
    """

    def fetch_posts(self, channel_username: str) -> tuple[list[RawPost], str | None]:
        raise NotImplementedError(
            "Telegram API адаптер не подключён. Используйте ``TelegramWebParser``."
        )

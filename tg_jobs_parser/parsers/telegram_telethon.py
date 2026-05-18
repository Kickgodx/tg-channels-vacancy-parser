"""Парсинг каналов/супергрупп через Telethon (пользовательский аккаунт)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.parsers.base import BaseChannelParser, RawPost
from tg_jobs_parser.parsers.channel_username import normalize_channel_username
from tg_jobs_parser.utils.dates import parse_date, resolve_search_date_to
from tg_jobs_parser.utils.env_loader import try_load_dotenv
from tg_jobs_parser.utils.logger import get_logger
from tg_jobs_parser.utils.text import compact_whitespace

_LOG = get_logger(__name__)


def telethon_import_error() -> ImportError | None:
    """Если пакета нет — возвращает ошибку импорта."""
    try:
        import telethon  # noqa: F401
    except ImportError as e:
        return e
    return None


def telegram_api_credentials() -> tuple[int, str]:
    """Читает ``TELEGRAM_API_ID`` и ``TELEGRAM_API_HASH`` из окружения (после ``try_load_dotenv``)."""
    try_load_dotenv()
    raw_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not raw_id or not api_hash:
        raise RuntimeError(
            "Не заданы TELEGRAM_API_ID / TELEGRAM_API_HASH. "
            "Создайте приложение на https://my.telegram.org/apps и положите значения в .env ."
        )
    try:
        api_id = int(raw_id)
    except ValueError as e:
        raise RuntimeError(f"TELEGRAM_API_ID должен быть числом, получено: {raw_id!r}") from e
    return api_id, api_hash


def _asyncio_run(coro):
    from tg_jobs_parser.utils.asyncio_windows import install_windows_asyncio_cleanup_helpers

    install_windows_asyncio_cleanup_helpers()
    return asyncio.run(coro)


async def _disconnect_client(client) -> None:
    try:
        if getattr(client, "is_connected", lambda: True)():
            await client.disconnect()
    except (ConnectionResetError, OSError, asyncio.CancelledError):
        pass


def _combine_utc(d, end_of_day: bool = False) -> datetime:
    if end_of_day:
        return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)


class TelegramTelethonParser(BaseChannelParser):
    """
    Получает историю сообщений от имени **вашего** Telegram-аккаунта.

    Первый запуск может запросить телефон, код из SMS и пароль 2FA — взаимодействие через консоль
    (интерактивный режим Telethon). После авторизации сохраняется файл сессии.

    Поддерживает пакетный режим ``fetch_posts_many``: один ``TelegramClient`` на все каналы и
    конкурентный обход через ``asyncio.gather`` с ограничением ``parser.channel_concurrency``.
    """

    def fetch_posts(self, channel_username: str) -> tuple[list[RawPost], str | None]:
        if err := telethon_import_error():
            raise RuntimeError(
                "Пакет telethon не установлен. Установите: poetry install"
            ) from err

        username = normalize_channel_username(channel_username)
        if not username:
            return [], "Пустое имя канала."

        try:
            return _asyncio_run(self._fetch_single_with_own_client(username))
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            _LOG.exception("Telethon: ошибка для @%s: %s", username, e)
            return [], f"@{username}: Telethon — {e}"

    def fetch_posts_many(
        self, channel_usernames: list[str]
    ) -> list[tuple[str, list[RawPost], str | None]]:
        if err := telethon_import_error():
            raise RuntimeError(
                "Пакет telethon не установлен. Установите: poetry install"
            ) from err

        usernames: list[str] = []
        out_placeholder: list[tuple[str, list[RawPost], str | None]] = []
        for ch in channel_usernames:
            u = normalize_channel_username(ch)
            if not u:
                out_placeholder.append((ch, [], "Пустое имя канала."))
                continue
            if u not in usernames:
                usernames.append(u)

        if not usernames:
            return out_placeholder

        results = _asyncio_run(self._fetch_many_async(usernames))
        return out_placeholder + results

    async def _fetch_single_with_own_client(
        self, username: str
    ) -> tuple[list[RawPost], str | None]:
        """Старый одиночный путь: свой клиент на один канал (используется fetch_posts)."""
        from telethon import TelegramClient

        api_id, api_hash = telegram_api_credentials()
        session_path = Path(self.config.telegram.telethon_session_file)
        session_path.parent.mkdir(parents=True, exist_ok=True)

        client = TelegramClient(session_path.as_posix(), api_id, api_hash)
        try:
            await self._start_client(client)
            return await self._iter_channel(client, username)
        finally:
            await _disconnect_client(client)

    async def _fetch_many_async(
        self, usernames: list[str]
    ) -> list[tuple[str, list[RawPost], str | None]]:
        """Один клиент + asyncio.gather с ограничением конкурентности."""
        from telethon import TelegramClient

        api_id, api_hash = telegram_api_credentials()
        session_path = Path(self.config.telegram.telethon_session_file)
        session_path.parent.mkdir(parents=True, exist_ok=True)

        concurrency = max(1, int(getattr(self.config.parser, "channel_concurrency", 4)))
        # Telethon при создании клиента сохраняет ссылку на текущий event loop; создаём его
        # внутри уже работающего цикла asyncio.run, всё корректно.
        client = TelegramClient(session_path.as_posix(), api_id, api_hash)
        try:
            await self._start_client(client)
            sem = asyncio.Semaphore(concurrency)
            _LOG.info(
                "Telethon: пакетный обход %s каналов, конкурентность=%s",
                len(usernames),
                concurrency,
            )

            cancel = self.cancellation

            async def _one(u: str) -> tuple[str, list[RawPost], str | None]:
                if cancel and cancel.is_cancelled():
                    return u, [], "Прервано (отмена)."
                async with sem:
                    try:
                        posts, warn = await self._iter_channel(client, u)
                        return u, posts, warn
                    except Exception as e:  # noqa: BLE001
                        _LOG.exception("Telethon: ошибка для @%s: %s", u, e)
                        return u, [], f"@{u}: Telethon — {e}"

            tasks = [asyncio.create_task(_one(u)) for u in usernames]
            try:
                return list(await asyncio.gather(*tasks))
            finally:
                if cancel and cancel.is_cancelled():
                    for t in tasks:
                        t.cancel()
        finally:
            await _disconnect_client(client)

    async def _start_client(self, client) -> None:
        """Один client.start() с подгрузкой телефона/2FA из .env (если есть)."""
        try_load_dotenv()
        phone = os.environ.get("TELEGRAM_PHONE", "").strip() or None
        pwd = os.environ.get("TELEGRAM_2FA_PASSWORD", "").strip() or None
        start_kw: dict = {}
        if phone:
            start_kw["phone"] = phone
        if pwd:
            start_kw["password"] = pwd
        await client.start(**start_kw)

        me = await client.get_me()
        if me is not None:
            _LOG.info("Telethon: сессия активна (%s).", getattr(me, "username", me.id))

    async def _iter_channel(self, client, username: str) -> tuple[list[RawPost], str | None]:
        """Вычитка одного канала на уже подключённом клиенте."""
        max_scan = self.config.parser.max_posts_per_channel

        df_d = parse_date(self.config.search.date_from)
        dt_d = resolve_search_date_to(self.config.search.date_to)
        dt_start = _combine_utc(df_d, end_of_day=False) if df_d else None
        dt_end = _combine_utc(dt_d, end_of_day=True) if dt_d else None
        use_date_window = dt_start is not None and dt_end is not None

        # При полном диапазоне дат крутим историю до выхода за date_from; limit в Telethon —
        # это потолок по числу **просмотренных** сообщений (включая пропущенные по дате).
        iter_limit = None if use_date_window else max_scan

        scanned = 0
        posts: list[RawPost] = []

        cancel = self.cancellation
        async for msg in client.iter_messages(username, limit=iter_limit):
            if cancel and cancel.is_cancelled():
                _LOG.warning("Telethon @%s: обход прерван (отмена).", username)
                break
            scanned += 1
            if scanned > max_scan:
                _LOG.warning(
                    "Telethon @%s: достигнут лимит max_posts_per_channel=%s просмотренных сообщений. "
                    "Увеличьте parser.max_posts_per_channel в config.yaml, если нужна более глубокая история.",
                    username,
                    max_scan,
                )
                break

            raw = getattr(msg, "message", None)
            if not raw or not str(raw).strip():
                continue

            md = getattr(msg, "date", None)
            if md is None:
                continue
            msg_dt = md
            if msg_dt.tzinfo is None:
                msg_dt = msg_dt.replace(tzinfo=timezone.utc)
            else:
                msg_dt = msg_dt.astimezone(timezone.utc)

            if use_date_window:
                if msg_dt > dt_end:
                    continue
                if msg_dt < dt_start:
                    break

            post_id = str(msg.id)
            text = compact_whitespace(str(raw))
            post_url = f"https://t.me/{username}/{post_id}"

            posts.append(
                RawPost(
                    channel_username=username,
                    post_id=post_id,
                    text=text,
                    post_date=msg_dt,
                    post_url=post_url,
                )
            )

        posts.sort(key=lambda p: p.post_date or datetime.min.replace(tzinfo=timezone.utc))
        cnt = len(posts)
        _LOG.info(
            "Telethon: @%s — текстовых сообщений в выборке: %s (просмотрено сообщений API: %s, лимит сканирования: %s)",
            username,
            cnt,
            scanned,
            max_scan,
        )
        return posts, None


__all__ = ["TelegramTelethonParser", "telethon_import_error", "telegram_api_credentials"]

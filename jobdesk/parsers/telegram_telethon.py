"""Парсинг каналов/супергрупп через Telethon (пользовательский аккаунт)."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from opentelemetry import trace

from jobdesk.observability.parse_metrics import (
    install_telethon_flood_filter,
    record_channel_fetched,
    record_flood_wait,
)
from jobdesk.parsers.base import (
    BaseChannelParser,
    ChannelCallback,
    ChannelCheckpoint,
    ForwardSource,
    RawPost,
)
from jobdesk.parsers.channel_username import normalize_channel_username
from jobdesk.utils.dates import resolve_search_date_from, resolve_search_date_to
from jobdesk.utils.env_loader import try_load_dotenv
from jobdesk.utils.logger import get_logger
from jobdesk.utils.text import compact_whitespace

_LOG = get_logger(__name__)
_TRACER = trace.get_tracer(__name__)

# Сколько раз за обход одного канала ждать FloodWait и продолжать с места остановки.
_MAX_FLOOD_RESUMES = 3

# Страница истории: GetHistoryRequest в ``iter_messages`` отдаёт до 100 сообщений
# (``_MAX_CHUNK_SIZE`` в Telethon). ``sleep_between_requests_seconds`` — интервал между страницами.
_HISTORY_PAGE_SIZE = 100


@dataclass(frozen=True)
class TelethonSessionInfo:
    """Результат интерактивной (или уже сохранённой) авторизации Telethon."""

    session_path: Path
    user_id: int | None
    username: str | None


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
    from jobdesk.utils.asyncio_windows import (
        install_windows_asyncio_cleanup_helpers,
    )

    install_windows_asyncio_cleanup_helpers()
    return asyncio.run(coro)


async def _disconnect_client(client) -> None:
    try:
        if getattr(client, "is_connected", lambda: True)():
            await client.disconnect()
    except (ConnectionResetError, OSError, asyncio.CancelledError):
        pass


async def start_telethon_client(client) -> object | None:
    """``client.start()`` с телефоном/2FA из .env; возвращает ``get_me()``."""
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
    return me


def ensure_telethon_session(session_file: str | Path) -> TelethonSessionInfo:
    """
    Интерактивная авторизация Telethon и сохранение файла сессии.

    При уже валидной сессии повторный ввод кода не требуется.
    """
    if err := telethon_import_error():
        raise RuntimeError(
            "Нужен Telethon: poetry install (и при необходимости --extras telethon). "
            "Запускайте: poetry run python -m jobdesk.cli login"
        ) from err

    from telethon import TelegramClient

    api_id, api_hash = telegram_api_credentials()
    session_path = Path(session_file)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    async def _run() -> TelethonSessionInfo:
        client = TelegramClient(session_path.as_posix(), api_id, api_hash)
        try:
            me = await start_telethon_client(client)
            user_id = getattr(me, "id", None) if me is not None else None
            username = getattr(me, "username", None) if me is not None else None
            return TelethonSessionInfo(
                session_path=session_path.resolve(),
                user_id=user_id,
                username=username,
            )
        finally:
            await _disconnect_client(client)

    return _asyncio_run(_run())


def forward_source(msg) -> ForwardSource | None:
    """Источник пересылки, если пост переслан из канала (не от пользователя и не скрытый).

    Username и название — из сущностей, пришедших вместе с историей (``msg.forward.chat``),
    без отдельного запроса к Telegram; нет сущности — только id канала.
    """
    fwd = getattr(msg, "fwd_from", None)
    channel_id = getattr(getattr(fwd, "from_id", None), "channel_id", None)
    if not isinstance(channel_id, int):
        return None
    chat = getattr(getattr(msg, "forward", None), "chat", None)
    username = getattr(chat, "username", None)
    if not username:
        # Каналы с несколькими username (Fragment) отдают их списком.
        usernames = [u for u in (getattr(chat, "usernames", None) or []) if getattr(u, "active", False)]
        username = getattr(usernames[0], "username", None) if usernames else None
    title = getattr(chat, "title", None)
    source_post = getattr(fwd, "channel_post", None)
    return ForwardSource(
        channel_id=channel_id,
        username=str(username).lower() if username else None,
        title=str(title) if title else None,
        post_id=source_post if isinstance(source_post, int) else None,
    )


def _combine_utc(d, end_of_day: bool = False) -> datetime:
    if end_of_day:
        return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=UTC)
    return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=UTC)


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
            raise RuntimeError("Пакет telethon не установлен. Установите: poetry install") from err

        username = normalize_channel_username(channel_username)
        if not username:
            return [], "Пустое имя канала."

        try:
            return _asyncio_run(self._fetch_single_with_own_client(username))
        except RuntimeError:
            raise
        except Exception as e:
            _LOG.exception("Telethon: ошибка для @%s: %s", username, e)
            return [], f"@{username}: Telethon — {e}"

    def fetch_posts_many(self, channel_usernames: list[str]) -> list[tuple[str, list[RawPost], str | None]]:
        return self._run_many(channel_usernames, on_channel=None)

    def stream_posts_many(self, channel_usernames: list[str], on_channel: ChannelCallback) -> None:
        self._run_many(channel_usernames, on_channel=on_channel)

    def _run_many(
        self,
        channel_usernames: list[str],
        *,
        on_channel: ChannelCallback | None,
    ) -> list[tuple[str, list[RawPost], str | None]]:
        if err := telethon_import_error():
            raise RuntimeError("Пакет telethon не установлен. Установите: poetry install") from err

        usernames: list[str] = []
        out_placeholder: list[tuple[str, list[RawPost], str | None]] = []
        for ch in channel_usernames:
            u = normalize_channel_username(ch)
            if not u:
                if on_channel is not None:
                    on_channel(ch, [], "Пустое имя канала.")
                else:
                    out_placeholder.append((ch, [], "Пустое имя канала."))
                continue
            if u not in usernames:
                usernames.append(u)

        if not usernames:
            return out_placeholder

        results = _asyncio_run(self._fetch_many_async(usernames, on_channel=on_channel))
        return out_placeholder + results

    async def _fetch_single_with_own_client(self, username: str) -> tuple[list[RawPost], str | None]:
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
        self,
        usernames: list[str],
        *,
        on_channel: ChannelCallback | None = None,
    ) -> list[tuple[str, list[RawPost], str | None]]:
        """Один клиент + asyncio.gather с ограничением конкурентности.

        С ``on_channel`` канал отдаётся колбэку сразу после вычитки, а в результат
        попадает без постов. Колбэк вызывается в отдельном потоке, но внутри
        семафора: пока он блокирован (очередь обработки полна), слот вычитки занят —
        новые каналы не читаются.
        """
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

            async def _deliver(item: tuple[str, list[RawPost], str | None]) -> tuple[str, list[RawPost], str | None]:
                if on_channel is None:
                    return item
                u, posts, warn = item
                await asyncio.to_thread(on_channel, u, posts, warn)
                return u, [], warn

            async def _one(u: str) -> tuple[str, list[RawPost], str | None]:
                if cancel and cancel.is_cancelled():
                    return u, [], "Прервано (отмена)."
                async with sem:
                    try:
                        posts, warn = await self._iter_channel(client, u)
                        item = (u, posts, warn)
                    except Exception as e:
                        _LOG.exception("Telethon: ошибка для @%s: %s", u, e)
                        item = (u, [], f"@{u}: Telethon — {e}")
                    return await _deliver(item)

            tasks = [asyncio.create_task(_one(u)) for u in usernames]
            task_to_user = {t: u for t, u in zip(tasks, usernames, strict=True)}
            if cancel:
                cancel.bind_async_tasks(asyncio.get_running_loop(), tasks)
            try:
                pending = set(tasks)
                results: list[tuple[str, list[RawPost], str | None]] = []
                while pending:
                    if cancel and cancel.is_cancelled():
                        for t in pending:
                            t.cancel()
                        done, pending = await asyncio.wait(pending)
                        for t in done:
                            if t.cancelled():
                                continue
                            try:
                                results.append(t.result())
                            except Exception as e:
                                u = task_to_user.get(t, "?")
                                results.append((u, [], f"@{u}: Telethon — {e}"))
                        break
                    done, pending = await asyncio.wait(
                        pending,
                        timeout=0.5,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in done:
                        if t.cancelled():
                            continue
                        try:
                            results.append(t.result())
                        except asyncio.CancelledError:
                            continue
                        except Exception as e:
                            u = task_to_user.get(t, "?")
                            results.append((u, [], f"@{u}: Telethon — {e}"))
                return results
            finally:
                if cancel:
                    cancel.clear_async_tasks()
        finally:
            await _disconnect_client(client)

    async def _start_client(self, client) -> None:
        """Один client.start() с подгрузкой телефона/2FA из .env (если есть)."""
        install_telethon_flood_filter()
        await start_telethon_client(client)

    async def _iter_channel(self, client, username: str) -> tuple[list[RawPost], str | None]:
        """Обход канала в span'е ``telegram.channel`` + метрики канала (см. ``_scan_channel``)."""
        started = time.monotonic()
        posts: list[RawPost] = []
        scanned = 0
        paused = 0.0
        result = "error"
        with _TRACER.start_as_current_span("telegram.channel", attributes={"telegram.channel": username}) as span:
            try:
                posts, warning, scanned, paused = await self._scan_channel(client, username)
                result = "partial" if warning else "ok"
                return posts, warning
            finally:
                span.set_attribute("telegram.result", result)
                span.set_attribute("telegram.posts", len(posts))
                span.set_attribute("telegram.messages_scanned", scanned)
                span.set_attribute("telegram.pause_seconds", round(paused, 3))
                record_channel_fetched(
                    result=result,
                    posts=len(posts),
                    scanned=scanned,
                    duration_seconds=time.monotonic() - started,
                    paused_seconds=paused,
                )

    async def _scan_channel(self, client, username: str) -> tuple[list[RawPost], str | None, int, float]:
        """Вычитка одного канала на уже подключённом клиенте: (посты, предупреждение, просмотрено, секунды пауз).

        Идёт от новых сообщений к старым. ``date_to`` в прошлом — старт с ``offset_date``,
        а не перебор всего, что новее окна. Есть чекпоинт канала, покрывающий
        ``date_from``, — читаем только сообщения новее него (``min_id``). FloodWait
        дольше ``parser.flood_wait_max_seconds`` или ошибка посреди обхода не
        выбрасывают уже прочитанные посты: канал возвращается частично, с предупреждением.
        ``parser.sleep_between_requests_seconds`` > 0 — запросы страниц истории не чаще раза в
        N секунд (как ``wait_time`` Telethon); фактически проспанное время возвращается для метрик.
        """
        from telethon.errors import FloodWaitError

        max_scan = self.config.parser.max_posts_per_channel
        pause_s = self.config.parser.sleep_between_requests_seconds

        df_d = resolve_search_date_from(self.config.search.date_from)
        dt_d = resolve_search_date_to(self.config.search.date_to)
        dt_start = _combine_utc(df_d, end_of_day=False) if df_d else None
        dt_end = _combine_utc(dt_d, end_of_day=True) if dt_d else None
        use_date_window = dt_start is not None and dt_end is not None
        # Окно упирается в «сейчас» — обход начинается с самого нового сообщения,
        # и только тогда прочитанное сплошь можно зафиксировать чекпоинтом.
        from_newest = dt_end is None or dt_end >= datetime.now(UTC)
        offset_date = None if from_newest else dt_end

        checkpoint = self.checkpoints.get(username)
        use_checkpoint = (
            self.config.parser.incremental_fetch
            and checkpoint is not None
            and use_date_window
            and from_newest
            and df_d >= checkpoint.covered_from
        )
        min_id = checkpoint.last_message_id if use_checkpoint else 0
        if min_id:
            trace.get_current_span().set_attribute("telegram.min_id", min_id)

        # При полном диапазоне дат крутим историю до выхода за date_from; limit в Telethon —
        # это потолок по числу **просмотренных** сообщений (включая пропущенные по дате).
        iter_limit = None if use_date_window else max_scan

        scanned = 0
        max_seen_id = 0
        reached_window_start = False
        stopped_early = False  # лимит, отмена или ошибка: покрытие не сплошное
        warning: str | None = None
        posts: list[RawPost] = []
        offset_id = 0  # продолжение после FloodWait: сообщения старше последнего просмотренного
        flood_resumes = 0
        paused = 0.0
        page_started = time.monotonic()  # ≈ момент запроса текущей страницы

        cancel = self.cancellation
        while True:
            try:
                async for msg in client.iter_messages(
                    username,
                    limit=iter_limit,
                    offset_date=offset_date,
                    offset_id=offset_id,
                    min_id=min_id,
                    # Темп задаём сами (pause_s), чтобы его было видно в метриках; встроенный
                    # Telethon при limit > 3000 (в т.ч. limit=None) — тот же интервал 1 с, но молча.
                    wait_time=0,
                ):
                    if cancel and cancel.is_cancelled():
                        _LOG.warning("Telethon @%s: обход прерван (отмена).", username)
                        stopped_early = True
                        break
                    scanned += 1
                    if scanned > max_scan:
                        # В parse_warnings, а не только в журнал: иначе неполное окно не видно в итоге прогона.
                        warning = (
                            f"@{username}: просмотрено {max_scan} сообщений (лимит parser.max_posts_per_channel) — "
                            f"канал прочитан частично: {len(posts)} постов. Увеличьте лимит, если нужна "
                            "более глубокая история."
                        )
                        _LOG.warning("Telethon %s", warning)
                        stopped_early = True
                        break
                    offset_id = msg.id
                    max_seen_id = max(max_seen_id, msg.id)
                    if pause_s > 0 and scanned % _HISTORY_PAGE_SIZE == 0:
                        # Страница дочитана — до следующего запроса выдерживаем интервал pause_s;
                        # время запроса и разбора страницы в него уже входит.
                        wait_s = pause_s - (time.monotonic() - page_started)
                        if wait_s > 0:
                            pause_started = time.monotonic()
                            resumed = await self._sleep_cancellable(wait_s)
                            paused += time.monotonic() - pause_started
                            if not resumed:
                                stopped_early = True
                                break
                        page_started = time.monotonic()

                    raw = getattr(msg, "message", None)
                    if not raw or not str(raw).strip():
                        continue

                    md = getattr(msg, "date", None)
                    if md is None:
                        continue
                    msg_dt = md
                    msg_dt = msg_dt.replace(tzinfo=UTC) if msg_dt.tzinfo is None else msg_dt.astimezone(UTC)

                    if use_date_window:
                        if msg_dt > dt_end:
                            continue
                        if msg_dt < dt_start:
                            reached_window_start = True
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
                            forward_from=forward_source(msg),
                        )
                    )
                break
            except FloodWaitError as e:
                wait_s = int(getattr(e, "seconds", 0) or 0)
                limit_s = self.config.parser.flood_wait_max_seconds
                if wait_s > limit_s or flood_resumes >= _MAX_FLOOD_RESUMES:
                    record_flood_wait(handled_by="gave_up", seconds=wait_s, request="GetHistoryRequest")
                    warning = (
                        f"@{username}: FloodWait {wait_s} с (ждём не дольше {limit_s} с, "
                        f"до {_MAX_FLOOD_RESUMES} раз) — канал прочитан частично: {len(posts)} постов."
                    )
                    _LOG.warning("Telethon %s", warning)
                    stopped_early = True
                    break
                flood_resumes += 1
                record_flood_wait(handled_by="resumed", seconds=wait_s, request="GetHistoryRequest")
                _LOG.warning(
                    "Telethon @%s: FloodWait %s с — ждём и продолжаем со сообщений старше id %s.",
                    username,
                    wait_s,
                    offset_id,
                )
                if not await self._sleep_cancellable(wait_s):
                    stopped_early = True
                    break
            except Exception as e:
                if not posts:
                    raise
                warning = f"@{username}: Telethon — {e}; канал прочитан частично: {len(posts)} постов."
                _LOG.warning("Telethon %s", warning)
                stopped_early = True
                break

        if from_newest and use_date_window and not stopped_early:
            # Дошли до date_from — покрыто с него. Кончились сообщения выше min_id —
            # покрытие сплошное с прошлым чекпоинтом. Кончилась вся история — с date_from.
            covered_from = checkpoint.covered_from if use_checkpoint and not reached_window_start else df_d
            last_id = max(max_seen_id, min_id)
            if last_id:
                self.new_checkpoints[username] = ChannelCheckpoint(last_message_id=last_id, covered_from=covered_from)

        posts.sort(key=lambda p: p.post_date or datetime.min.replace(tzinfo=UTC))
        cnt = len(posts)
        _LOG.info(
            "Telethon: @%s — текстовых сообщений в выборке: %s (просмотрено сообщений API: %s, "
            "лимит сканирования: %s%s%s)",
            username,
            cnt,
            scanned,
            max_scan,
            f", только новее id {min_id}" if min_id else "",
            f", паузы между страницами: {paused:.1f} с" if paused else "",
        )
        return posts, warning, scanned, paused

    async def _sleep_cancellable(self, seconds: float) -> bool:
        """Пауза с проверкой отмены раз в секунду. False — прогон отменён во время ожидания."""
        cancel = self.cancellation
        remaining = float(seconds)
        while remaining > 0:
            if cancel and cancel.is_cancelled():
                return False
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step
        return not (cancel and cancel.is_cancelled())


__all__ = [
    "TelegramTelethonParser",
    "telegram_api_credentials",
    "telethon_import_error",
]

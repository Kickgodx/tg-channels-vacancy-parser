"""Парсер публичных страниц ``https://t.me/s/<channel>``."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.parsers.base import BaseChannelParser, RawPost
from tg_jobs_parser.utils.dates import parse_date
from tg_jobs_parser.utils.logger import get_logger
from tg_jobs_parser.utils.text import compact_whitespace

_LOG = get_logger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def normalize_channel_username(value: str) -> str:
    """
    Принимает username, ``t.me/...`` или ``t.me/s/...`` и возвращает username.
    """
    v = value.strip()
    if not v:
        return v
    v = v.lstrip("@")
    if v.startswith("http://") or v.startswith("https://"):
        parsed = urlparse(v)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) >= 2 and path_parts[0].lower() == "s":
            return path_parts[1].split("?")[0]
        if path_parts:
            return path_parts[0].split("?")[0]
    parts = [p for p in v.split("/") if p]
    if parts and parts[0] in {"t.me", "telegram.me"} and len(parts) > 1:
        if parts[1].lower() == "s" and len(parts) > 2:
            return parts[2].split("?")[0]
        return parts[1].split("?")[0]
    return v.split("?")[0]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        if value.endswith("Z"):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _post_in_date_range(
    post_dt: datetime | None,
    date_from,
    date_to,
) -> bool:
    """
    True если пост следует включить.

    Без дат в конфиге — всё включаем. Без даты у поста — включаем (best-effort).
    """
    if date_from is None or date_to is None:
        return True
    if post_dt is None:
        return True
    d = post_dt.date()
    return date_from <= d <= date_to


class TelegramWebParser(BaseChannelParser):
    """Скрапинг публичной ленты без авторизации."""

    def fetch_posts(self, channel_username: str) -> tuple[list[RawPost], str | None]:
        user = normalize_channel_username(channel_username)
        if not user:
            return [], None

        date_from = parse_date(self.config.search.date_from)
        date_to = parse_date(self.config.search.date_to)

        warning_parts: list[str] = []
        if date_from is None or date_to is None:
            warning_parts.append(
                "Задан неполный диапазон дат в config — фильтрация по датам отключена."
            )

        collected: dict[str, RawPost] = {}
        cursor_before: str | None = None
        max_posts = self.config.parser.max_posts_per_channel
        sleep_s = self.config.parser.sleep_between_requests_seconds
        timeout = self.config.parser.request_timeout_seconds

        with httpx.Client(
            headers={"User-Agent": _DEFAULT_UA},
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            while len(collected) < max_posts:
                url = f"https://t.me/s/{user}"
                if cursor_before:
                    url = f"{url}?before={cursor_before}"
                try:
                    resp = client.get(url)
                except httpx.HTTPError as e:
                    _LOG.error("HTTP ошибка для %s: %s", url, e)
                    warning_parts.append(f"Сетевая ошибка при запросе {url}: {e}")
                    break
                if resp.status_code != 200:
                    _LOG.warning("Код ответа %s для %s", resp.status_code, url)
                    warning_parts.append(f"Канал @{user}: HTTP {resp.status_code} для {url}")
                    break

                soup = BeautifulSoup(resp.text, "lxml")
                wraps = soup.select(".tgme_widget_message_wrap")
                if not wraps:
                    warning_parts.append(
                        f"Канал @{user}: в HTML не найдены блоки сообщений "
                        "(изменилась вёрстка или канал недоступен)."
                    )
                    break

                page_ids: list[int] = []
                oldest_on_page: datetime | None = None

                for wrap in wraps:
                    msg = wrap.select_one(".tgme_widget_message")
                    if msg is None:
                        continue
                    data_post = msg.get("data-post") or ""
                    m = re.match(r"^(.+)-(\d+)$", data_post)
                    if not m:
                        continue
                    ch, post_id_str = m.group(1), m.group(2)
                    try:
                        page_ids.append(int(post_id_str))
                    except ValueError:
                        pass

                    if post_id_str in collected:
                        continue

                    date_el = wrap.select_one("a.tgme_widget_message_date time")
                    post_dt = _parse_dt(date_el.get("datetime")) if date_el else None

                    link_el = wrap.select_one("a.tgme_widget_message_date")
                    href = link_el.get("href") if link_el else None
                    post_url = href if href else f"https://t.me/{ch}/{post_id_str}"

                    text_el = wrap.select_one(".tgme_widget_message_text")
                    raw_text = text_el.get_text("\n", strip=True) if text_el else ""
                    text = compact_whitespace(raw_text.replace("\n", " "))

                    if post_dt and (oldest_on_page is None or post_dt < oldest_on_page):
                        oldest_on_page = post_dt

                    if _post_in_date_range(post_dt, date_from, date_to):
                        collected[post_id_str] = RawPost(
                            channel_username=user,
                            post_id=post_id_str,
                            text=text,
                            post_date=post_dt,
                            post_url=post_url,
                        )

                next_before = self._extract_before_cursor(soup, page_ids)

                reached_history_end = False
                if next_before is None:
                    warning_parts.append(
                        f"@{user}: конец доступной истории в веб-просмотре "
                        "(или нет предыдущей страницы)."
                    )
                    reached_history_end = True
                elif cursor_before == next_before:
                    warning_parts.append(f"@{user}: пагинация застряла на before={cursor_before}.")
                    reached_history_end = True

                cursor_before = next_before

                if oldest_on_page and date_from:
                    if oldest_on_page.date() < date_from:
                        warning_parts.append(
                            f"@{user}: часть истории недоступна без API; "
                            f"остановка на сообщениях старше {date_from}."
                        )
                        reached_history_end = True

                if reached_history_end:
                    break
                if len(collected) >= max_posts:
                    break

                time.sleep(max(sleep_s, 0))

        posts = list(collected.values())
        posts.sort(
            key=lambda p: p.post_date or datetime.min.replace(tzinfo=timezone.utc),
            reverse=False,
        )

        warn = " ".join(warning_parts) if warning_parts else None
        _LOG.info("Канал @%s: собрано постов: %s", user, len(posts))
        return posts, warn

    def _extract_before_cursor(self, soup: BeautifulSoup, page_ids: list[int]) -> str | None:
        """Определяет параметр ``before`` для следующей страницы."""
        prev = soup.select_one('link[rel="prev"][href*="before="]')
        if prev and prev.has_attr("href"):
            hm = re.search(r"before=(\d+)", prev["href"])
            if hm:
                return hm.group(1)

        more = soup.select_one("a.tme_messages_more[href]")
        if more and more.has_attr("href"):
            hm = re.search(r"before=(\d+)", more["href"])
            if hm:
                return hm.group(1)

        if page_ids:
            return str(min(page_ids))
        return None

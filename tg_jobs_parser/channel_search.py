"""
Best-effort поиск публичных каналов по ключевым словам без официального API Telegram.

Стратегия: HTML-поиск DuckDuckGo (может ограничиваться по IP/верстке — предусмотрен fallback).
"""

from __future__ import annotations

import re

import httpx

from tg_jobs_parser.utils.logger import get_logger

_LOG = get_logger(__name__)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


class ChannelSearchBackend:
    """Интерфейс расширяемого провайдера поиска."""

    name = "stub"

    def search(self, query: str, limit: int = 15) -> list[str]:
        return []


def extract_telegram_candidates(html: str, limit: int) -> list[str]:
    urls = re.findall(r"https?://(?:www\.)?t\.me/(?:s/)?([A-Za-z0-9_]+)", html)
    out: list[str] = []
    seen: set[str] = set()
    skip = {"joinchat", "addstickers", "iv", "proxy", "settings", "faq"}
    for u in urls:
        lu = u.lower()
        if lu in skip:
            continue
        if lu not in seen:
            seen.add(lu)
            out.append(u)
        if len(out) >= limit:
            break
    return out


class DuckDuckGoHtmlBackend(ChannelSearchBackend):
    """Парсинг ``html.duckduckgo.com`` (best-effort)."""

    name = "duckduckgo_html"

    def search(self, query: str, limit: int = 15) -> list[str]:
        try:
            with httpx.Client(
                headers={
                    "User-Agent": _UA,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": "https://duckduckgo.com/",
                },
                timeout=25.0,
                follow_redirects=True,
            ) as client:
                resp = client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": f"{query} site:t.me"},
                )
                resp.raise_for_status()
                return extract_telegram_candidates(resp.text, limit)
        except httpx.HTTPError as e:
            _LOG.warning("DDG search failed (%s): %s", query, e)
            return []


def search_channels(keywords: str, limit: int = 15) -> tuple[list[str], str]:
    """
    Возвращает найденные ``username`` и имя использованного бэкенда.
    Если бэкенд не сработал — пустой список ``[]`` из ``duckduckgo_html``.
    """
    backend = DuckDuckGoHtmlBackend()
    found = backend.search(keywords.strip(), limit=limit)
    if found:
        return found, backend.name
    fallback = ChannelSearchBackend()
    return fallback.search(keywords.strip(), limit=limit), "stub"

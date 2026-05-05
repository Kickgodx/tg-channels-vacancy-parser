"""Нормализация имени канала / ссылки Telegram."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_channel_username(value: str) -> str:
    """
    Принимает username, ``t.me/...`` или ``t.me/s/...`` и возвращает username без ``@``.
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

"""Контакты рекрутера для кнопки «Написать»: кандидаты из текста минус каналы и подписи каналов."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.db.models import ChannelCandidateRow, VacancyRow
from backend.schemas import RecruiterContactOut, RecruiterContacts
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.extraction.recruiter_contacts import find_recruiter_contacts
from tg_jobs_parser.hh.mapper import HH_CHANNEL

# Подпись канала: упоминание без слов отклика в такой доле вакансий канала (и не реже)...
SIGNATURE_MIN_SHARE = 0.3
# ... и не меньше стольких вакансий — иначе в маленьком канале любой рекрутер выглядел бы подписью.
SIGNATURE_MIN_POSTS = 5
MAX_APPLICATION_LINKS = 3


def known_channel_usernames(session: Session) -> set[str]:
    """Каналы, которые мы читаем или видели в пересылках, — в нижнем регистре."""
    names = {str(c).lower() for c in session.scalars(select(VacancyRow.source_channel).distinct())}
    names |= {
        str(u).lower()
        for u in session.scalars(select(ChannelCandidateRow.username).where(ChannelCandidateRow.username.isnot(None)))
    }
    cfg = load_config(get_settings().config_path)
    names |= {c.strip().lstrip("@").rsplit("/", 1)[-1].lower() for c in cfg.sources.channels if c.strip()}
    return names


def _is_signature(session: Session, channel: str, handle: str) -> bool:
    """Упоминание стоит под заметной долей вакансий канала — это подпись, а не рекрутер."""
    v = VacancyRow
    # В username только [A-Za-z0-9_]; «_» в LIKE — любой символ, экранируем.
    esc = handle.replace("_", "\\_")
    mentions = or_(v.raw_text.ilike(f"%@{esc}%"), v.raw_text.ilike(f"%t.me/{esc}%"))
    total, mentioned = session.execute(
        select(func.count(), func.count().filter(mentions)).where(
            v.source_channel == channel, v.is_duplicate.is_(False)
        )
    ).one()
    return bool(total) and mentioned >= SIGNATURE_MIN_POSTS and mentioned / total >= SIGNATURE_MIN_SHARE


def _norm_url(url: str) -> str:
    u = url.strip().lower()
    for prefix in ("https://", "http://", "www."):
        u = u.removeprefix(prefix)
    return u.rstrip("/")


def application_links(vacancy: VacancyRow) -> list[str]:
    """Ссылки на отклик без страницы самой вакансии (она — «Открыть пост»), повторов и t.me.

    У HH две ссылки: форма отклика и страница вакансии — вторая совпадает с ``post_url``.
    Ссылки t.me — это контакты и каналы: их показывают кнопки «Написать».
    """
    skip = {_norm_url(vacancy.post_url)} if vacancy.post_url else set()
    out: list[str] = []
    for url in vacancy.application_urls or []:
        key = _norm_url(str(url))
        if not key or key in skip or key.startswith(("t.me/", "telegram.me/")):
            continue
        skip.add(key)
        out.append(str(url).strip())
    return out[:MAX_APPLICATION_LINKS]


def recruiter_contacts(session: Session, vacancy: VacancyRow) -> RecruiterContacts:
    # ``contact`` у Telegram — строка из того же текста (часто подпись канала); у HH — контакты из API.
    extra = vacancy.contact if vacancy.source_channel == HH_CHANNEL else None
    text = "\n".join(t for t in (vacancy.raw_text, extra) if t)
    out: list[RecruiterContactOut] = []
    for c in find_recruiter_contacts(text, known_channels=known_channel_usernames(session)):
        if c.kind != "email" and not c.has_contact_hint and _is_signature(session, vacancy.source_channel, c.value):
            continue
        url = f"mailto:{c.value}" if c.kind == "email" else f"https://t.me/{c.value}"
        out.append(RecruiterContactOut(kind=c.kind, value=c.value, url=url, has_contact_hint=c.has_contact_hint))
    return RecruiterContacts(vacancy_id=vacancy.id, contacts=out, application_urls=application_links(vacancy))

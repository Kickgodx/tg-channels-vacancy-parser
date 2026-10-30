"""Контакты рекрутера: кандидаты из текста, отсев каналов, подписей и рекламы, эндпоинт."""

from __future__ import annotations

import pytest

from backend.db.models import ChannelCandidateRow, VacancyRow
from backend.services.recruiter_contacts import SIGNATURE_MIN_POSTS, recruiter_contacts
from jobdesk.extraction.recruiter_contacts import find_recruiter_contacts

_ids = iter(range(1, 1_000_000))


def _values(text: str, known: set[str] | None = None) -> list[tuple[str, str, bool]]:
    return [(c.kind, c.value, c.has_contact_hint) for c in find_recruiter_contacts(text, known_channels=known or set())]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Senior QA. 📩Для вопросов и резюме - @chloe_cay", [("telegram", "chloe_cay", True)]),
        (
            "Контакты: resume@vitalpartners.ru 🔥 Подписаться на наши каналы / @best_itjob / @it_rab",
            [("email", "resume@vitalpartners.ru", True)],
        ),
        ("Больше вакансий для продактов и проджектов: @forproducts", []),
        ("Вакансия опубликована в @jtbl_vacancy", []),
        ("Пишите: https://t.me/hr_anna", [("telegram", "hr_anna", True)]),
        ("Пост: https://t.me/somechannel/123", []),
        ("Interested? Feel free to DM me! @Alona_Recruiter", [("telegram", "Alona_Recruiter", True)]),
        ("Резюме — hr@kaspi.kz, подробнее https://teletype.in/@gameberg/xyz", [("email", "hr@kaspi.kz", True)]),
        ("Отклик через бота @hire_me_bot", [("telegram_bot", "hire_me_bot", True)]),
        ("noreply@site.ru и logo@2x.png", []),
    ],
)
def test_find_recruiter_contacts(text, expected):
    assert _values(text) == expected


def test_known_channels_dropped_and_hinted_first():
    text = "🖋 @OlegHolodov88 👉@progjob. Контакты: @masha_hr"
    assert _values(text, {"progjob"}) == [
        ("telegram", "masha_hr", True),
        ("telegram", "OlegHolodov88", False),
    ]


def _row(text: str, *, channel: str = "jobs_ch", contact: str | None = None) -> VacancyRow:
    i = next(_ids)
    return VacancyRow(
        id=f"rc-{i}",
        source_post_key=f"{channel}-{i}",
        source_channel=channel,
        source_url=f"https://t.me/s/{channel}",
        raw_text=text,
        cleaned_text=text,
        contact=contact,
        detected_specializations=[],
        technologies=[],
    )


def test_channel_signature_dropped_unless_hinted(db_session):
    rows = [_row(f"Вакансия {i}. ✈️@sign_admin") for i in range(SIGNATURE_MIN_POSTS)]
    hinted = _row("Вакансия. Отклики: @sign_admin")
    lone = _row("Вакансия. 🖋 @lone_recruiter")
    db_session.add_all([*rows, hinted, lone])
    db_session.add(ChannelCandidateRow(channel_id=1, username="ForwardedChan"))
    db_session.add(_row("Вакансия @forwardedchan @jobs_ch"))
    db_session.commit()

    assert recruiter_contacts(db_session, rows[0]).contacts == []
    assert [c.value for c in recruiter_contacts(db_session, hinted).contacts] == ["sign_admin"]
    lone_out = recruiter_contacts(db_session, lone).contacts
    assert [(c.value, c.url) for c in lone_out] == [("lone_recruiter", "https://t.me/lone_recruiter")]


def test_hh_contact_field_used_telegram_contact_field_ignored(db_session):
    hh = _row("Python developer", channel="hh", contact="Анна, anna@corp.ru")
    tg = _row("Python developer", contact="@gameberg")
    db_session.add_all([hh, tg])
    db_session.commit()
    out = recruiter_contacts(db_session, hh).contacts
    assert [(c.kind, c.url) for c in out] == [("email", "mailto:anna@corp.ru")]
    assert recruiter_contacts(db_session, tg).contacts == []


def test_endpoint(admin_client, db_session):
    v = _row("Контакты: @masha_hr")
    db_session.add(v)
    db_session.commit()
    r = admin_client.get(f"/api/v1/vacancies/{v.id}/contacts")
    assert r.status_code == 200
    assert r.json()["contacts"] == [
        {"kind": "telegram", "value": "masha_hr", "url": "https://t.me/masha_hr", "has_contact_hint": True}
    ]
    assert admin_client.get("/api/v1/vacancies/nope/contacts").status_code == 404


def test_application_links_skip_vacancy_page_telegram_and_repeats(db_session):
    from backend.services.recruiter_contacts import application_links

    hh = _row("Python developer", channel="hh")
    hh.post_url = "https://hh.ru/vacancy/1?tech_source=api"
    hh.application_urls = [
        "https://hh.ru/applicant/vacancy_response?vacancyId=1&tech_source=api",
        "https://hh.ru/vacancy/1?tech_source=api",
    ]
    assert application_links(hh) == ["https://hh.ru/applicant/vacancy_response?vacancyId=1&tech_source=api"]

    tg = _row("Вакансия")
    tg.post_url = "https://t.me/jobs_ch/5"
    tg.application_urls = ["https://t.me/hr_anna", "https://forms.gle/abc", "http://www.forms.gle/abc/"]
    assert application_links(tg) == ["https://forms.gle/abc"]

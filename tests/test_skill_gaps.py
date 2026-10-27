"""Сводка недостающих навыков резюме: агрегация (без БД) и эндпоинт /resumes/{id}/skill-gaps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.db import repository as repo
from backend.db.models import VacancyRow
from backend.db.session import session_scope
from tg_jobs_parser.analytics.skill_gaps import MatchSkills, TechnologyMatcher, summarize_skill_gaps

_MATCHER = TechnologyMatcher(["Go", "MySQL", "ClickHouse", "Kafka", "Kubernetes", "Jenkins", "C", "C++"])


def _m(vid: str, *skills: tuple[str, str]) -> MatchSkills:
    return MatchSkills(vacancy_id=vid, missing_skills=[{"skill": s, "importance": imp} for s, imp in skills])


def _by_name(items):
    return {g.name: g for g in items}


def test_two_technologies_in_one_skill():
    s = summarize_skill_gaps([_m("v1", ("MySQL / ClickHouse", "secondary"))], _MATCHER)
    techs = _by_name(s.technologies)
    assert set(techs) == {"MySQL", "ClickHouse"}
    assert techs["MySQL"].vacancies == 1
    assert s.other == []


def test_alias_and_parenthetical_collapse_to_one_technology():
    s = summarize_skill_gaps([_m("v1", ("Golang", "important")), _m("v2", ("Go (5+ лет)", "critical"))], _MATCHER)
    go = _by_name(s.technologies)["Go"]
    assert go.vacancies == 2
    assert (go.critical, go.important) == (1, 1)
    assert go.vacancy_ids == ["v1", "v2"]


def test_longer_name_wins_over_nested():
    assert _MATCHER.find("C/C++ (чтение кода)") == ["C++", "C"]
    assert _MATCHER.find("C++") == ["C++"]


def test_other_skill_normalized():
    s = summarize_skill_gaps(
        [_m("v1", ("Английский B2 (устный)", "critical")), _m("v2", ("английский  b2", "secondary"))], _MATCHER
    )
    assert s.technologies == []
    [eng] = s.other
    assert eng.name == "Английский B2"
    assert eng.vacancies == 2


def test_highest_importance_per_vacancy():
    s = summarize_skill_gaps([_m("v1", ("Kafka", "secondary"), ("Kafka Streams", "critical"))], _MATCHER)
    kafka = _by_name(s.technologies)["Kafka"]
    assert (kafka.vacancies, kafka.critical, kafka.secondary) == (1, 1, 0)


def test_order_by_weight_then_count():
    s = summarize_skill_gaps(
        [
            _m("v1", ("Kubernetes", "critical"), ("Jenkins", "secondary")),
            _m("v2", ("Jenkins", "secondary")),
        ],
        _MATCHER,
    )
    assert [g.name for g in s.technologies] == ["Kubernetes", "Jenkins"]


# --- API ---


def _register(client, email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _vacancy(vid: str, *, technologies=(), archived=False, duplicate=False) -> VacancyRow:
    return VacancyRow(
        id=vid,
        source_post_key=f"k-{vid}",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        post_date=datetime(2026, 9, 1, tzinfo=UTC),
        raw_text=vid,
        cleaned_text=vid,
        technologies=list(technologies),
        archived_at=datetime(2026, 9, 2, tzinfo=UTC) if archived else None,
        is_duplicate=duplicate,
    )


def _match(session, rid: int, vid: str, missing: list[dict], created: datetime) -> None:
    row = repo.upsert_vacancy_match(
        session,
        resume_id=rid,
        vacancy_id=vid,
        overall_score=50,
        confidence=80,
        breakdown={},
        explanation="",
        matching_skills=[],
        missing_skills=missing,
        strengths=[],
        risks=[],
        model="m",
    )
    row.created_at = created


def _resume(client, token: str) -> int:
    r = client.post("/api/v1/resumes", headers=_auth(token), json={"name": "CV", "text": "python"})
    return r.json()["id"]


def test_empty_summary(api_client):
    token = _register(api_client, "a@example.com")
    rid = _resume(api_client, token)
    r = api_client.get(f"/api/v1/resumes/{rid}/skill-gaps", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"resume_id": rid, "evaluated": 0, "technologies": [], "other": []}


def test_foreign_resume_404(api_client):
    owner = _register(api_client, "a@example.com")
    rid = _resume(api_client, owner)
    other = _register(api_client, "b@example.com")
    r = api_client.get(f"/api/v1/resumes/{rid}/skill-gaps", headers=_auth(other))
    assert r.status_code == 404


def test_summary_with_market_demand(api_client):
    token = _register(api_client, "a@example.com")
    rid = _resume(api_client, token)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    with session_scope() as session:
        # Каталог: Kafka у трёх активных, плюс архивная и дубль — не в спросе.
        # «Jenkins» (в двух написаниях) у пяти активных — попадает в словарь из каталога;
        # «Backend» у пяти, но это роль — в словарь не входит.
        session.add_all(
            [
                _vacancy("m1", technologies=["Kafka", "Jenkins", "Backend"]),
                _vacancy("m2", technologies=["Kafka", "Jenkins", "Backend"]),
                _vacancy("m3", technologies=["Kafka", "Jenkins", "Backend"]),
                _vacancy("j4", technologies=["Jenkins", "Backend"]),
                _vacancy("j5", technologies=["jenkins", "Backend"]),
                _vacancy("m4", technologies=["Kafka"], archived=True),
                _vacancy("m5", technologies=["Kafka"], duplicate=True),
                _vacancy("old", archived=True),
            ]
        )
        session.flush()
        _match(session, rid, "m1", [{"skill": "Kafka", "importance": "critical"}], t0)
        _match(
            session,
            rid,
            "old",
            [
                {"skill": "Jenkins (CI)", "importance": "secondary"},
                {"skill": "Backend на Go", "importance": "secondary"},
                {"skill": "Английский B2", "importance": "important"},
            ],
            t0 + timedelta(days=1),
        )
    r = api_client.get(f"/api/v1/resumes/{rid}/skill-gaps", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["evaluated"] == 2
    techs = {t["name"]: t for t in body["technologies"]}
    assert [t["name"] for t in body["technologies"]] == ["Kafka", "Go", "Jenkins"]
    assert techs["Kafka"]["market_vacancies"] == 3
    assert techs["Kafka"]["share"] == 0.5
    assert techs["Jenkins"]["market_vacancies"] == 5
    assert techs["Go"]["market_vacancies"] == 0
    assert techs["Jenkins"]["vacancy_ids"] == ["old"]
    [eng] = body["other"]
    assert eng["name"] == "Английский B2"
    assert eng["market_vacancies"] is None


def test_mentions_whole_word_only():
    from tg_jobs_parser.analytics.skill_gaps import mentions

    assert mentions("Трекер Atlassian Jira, Ёлка", "jira")
    assert mentions("ёлка", "Елка")
    assert not mentions("JiraSync", "Jira")
    assert not mentions("текст", "")

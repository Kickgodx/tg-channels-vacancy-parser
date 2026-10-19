"""Технологии отдельно от специализаций.

1. ``vacancies.technologies``: JSON → JSONB + GIN-индекс (фильтр по технологии идёт ``@>``,
   как по специализации в 019).
2. Языки и фреймворки из ``detected_specializations`` (Python, Go, Java, PHP, C++, .NET, 1С…)
   переезжают в ``technologies``; технологии приводятся к каноническому написанию
   (Golang → Go, Apache Kafka → Kafka, регистр), повторы схлопываются, порядок сохраняется.
   «Python Developer» без другой роли получает роль Software Engineer; голое «Python» —
   остаётся без роли (такие вакансии переразбирает LLM: ``POST /vacancies/reextract``).
3. ``saved_filters.params``: языки из ``specializations`` → ``technologies``.

Словари — копия ``tg_jobs_parser/config/schema.py`` на момент миграции (код приложения
миграции не импортируют). Снимки прогонов (``parse_runs``) — история, их не трогаем:
``FiltersConfig`` переносит языки при чтении. Downgrade возвращает только тип колонки.

Revision ID: 037_separate_technologies
Revises: 036_merge_qa_specializations
Create Date: 2026-10-20
"""

import json
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "037_separate_technologies"
down_revision: Union[str, None] = "036_merge_qa_specializations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_vacancies_technologies_gin"

_TECHNOLOGIES = [
    "Python", "Java", "Kotlin", "Swift", "Go", "JavaScript", "TypeScript", "PHP", "C++", "C#",
    ".NET", "Rust", "Ruby", "Scala", "Node.js", "1С", "SQL", "React", "Vue", "Angular", "Flutter",
    "Django", "FastAPI", "Flask", "Spring Boot", "Spring", "Kafka", "RabbitMQ", "Docker",
    "Kubernetes", "Terraform", "Ansible", "PostgreSQL", "MySQL", "Redis", "MongoDB", "ClickHouse",
    "Elasticsearch", "Airflow", "Spark", "PyTorch", "TensorFlow", "Selenium", "Playwright",
    "Pytest", "Appium", "REST", "GraphQL", "gRPC", "Linux", "AWS", "GCP", "Azure",
    # Только в специализациях от LLM.
    "C", "Objective-C", "Dart", "Elixir", "Erlang", "Haskell", "Clojure", "Perl", "Lua", "Delphi",
    "Solidity", "Bitrix", "Laravel", "Symfony", "Ruby on Rails", "Next.js", "Svelte",
    "React Native", "Unity", "Unreal Engine", "Godot",
]  # fmt: skip

_ALIASES = {
    "golang": "Go", "go lang": "Go", "go-lang": "Go", "python3": "Python", "python 3": "Python",
    "postgres": "PostgreSQL", "postgre": "PostgreSQL", "postgre sql": "PostgreSQL",
    "apache kafka": "Kafka", "apache airflow": "Airflow", "apache spark": "Spark",
    "rest api": "REST", "rest-api": "REST", "restful": "REST", "restful api": "REST",
    "js": "JavaScript", "ts": "TypeScript", "node": "Node.js", "nodejs": "Node.js",
    "node js": "Node.js", "k8s": "Kubernetes", "dotnet": ".NET", ".net core": ".NET",
    "c#/.net": ".NET", "c# .net": ".NET", "c#.net": ".NET", "1c": "1С",
    "1c:предприятие": "1С", "1с:предприятие": "1С", "reactjs": "React", "react.js": "React",
    "vuejs": "Vue", "vue.js": "Vue", "angularjs": "Angular", "spring framework": "Spring",
    "elastic": "Elasticsearch", "elastic search": "Elasticsearch", "ror": "Ruby on Rails",
    "nextjs": "Next.js",
}  # fmt: skip

_CATALOG = {t.lower(): t for t in _TECHNOLOGIES}
_DEV = r"(?:developer|engineer|dev|разработчик|программист)"
_TECH_ROLE_RE = re.compile(rf"^(?P<tech>.+?)[\s\-]+{_DEV}$|^{_DEV}[\s\-]+(?P<tech2>.+)$", re.IGNORECASE)
_GENERIC_ROLE = "Software Engineer"


def _tech(name: str) -> str | None:
    key = " ".join(name.strip().lower().split())
    return (_ALIASES.get(key) or _CATALOG.get(key)) if key else None


def _canonical_techs(names: list) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = " ".join(str(raw).split())
        if not name:
            continue
        name = _tech(name) or name
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _techs_in_spec(name: str) -> tuple[list[str], bool]:
    if "/" in name:
        parts = [_tech(p) for p in name.split("/")]
        if all(parts):
            return [p for p in parts if p], False
    tech = _tech(name)
    if tech:
        return [tech], False
    m = _TECH_ROLE_RE.match(name.strip())
    if m:
        tech = _tech(m.group("tech") or m.group("tech2") or "")
        if tech:
            return [tech], True
    return [], False


def _split(specs: list, techs: list) -> tuple[list[str], list[str]]:
    roles: list[str] = []
    moved: list[str] = []
    developer = False
    for raw in specs:
        name = str(raw).strip()
        if not name:
            continue
        found, is_dev = _techs_in_spec(name)
        if found:
            moved.extend(found)
            developer = developer or is_dev
        elif name not in roles:
            roles.append(name)
    if developer and not roles:
        roles = [_GENERIC_ROLE]
    return roles, _canonical_techs([*techs, *moved])


def _as_list(value) -> list:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, list) else []


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"]: c["type"] for c in sa.inspect(bind).get_columns("vacancies")}
    if not isinstance(columns["technologies"], JSONB):
        op.execute("ALTER TABLE vacancies ALTER COLUMN technologies TYPE jsonb USING technologies::jsonb")
    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON vacancies USING gin (technologies)")

    updates = []
    for vid, specs, techs in bind.execute(sa.text("SELECT id, detected_specializations, technologies FROM vacancies")):
        specs, techs = _as_list(specs), _as_list(techs)
        new_specs, new_techs = _split(specs, techs)
        if new_specs != specs or new_techs != techs:
            updates.append({"id": vid, "s": json.dumps(new_specs), "t": json.dumps(new_techs)})
    if updates:
        bind.execute(
            sa.text(
                "UPDATE vacancies SET detected_specializations = CAST(:s AS jsonb), "
                "technologies = CAST(:t AS jsonb) WHERE id = :id"
            ),
            updates,
        )

    filter_updates = []
    for fid, params in bind.execute(sa.text("SELECT id, params FROM saved_filters")):
        data = json.loads(params) if isinstance(params, str) else params
        if not isinstance(data, dict) or not isinstance(data.get("specializations"), list):
            continue
        roles, techs = _split(data["specializations"], _as_list(data.get("technologies")))
        if roles != data["specializations"] or techs != _as_list(data.get("technologies")):
            data = {**data, "specializations": roles, "technologies": techs}
            filter_updates.append({"id": fid, "p": json.dumps(data, ensure_ascii=False)})
    if filter_updates:
        bind.execute(sa.text("UPDATE saved_filters SET params = CAST(:p AS json) WHERE id = :id"), filter_updates)


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute("ALTER TABLE vacancies ALTER COLUMN technologies TYPE json USING technologies::json")

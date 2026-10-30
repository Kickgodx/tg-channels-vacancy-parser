"""Формат набора данных eval эмбеддингов и чтение/запись JSONL."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jobdesk.evals.paths import REPO_ROOT

DATA_DIR = REPO_ROOT / "evals" / "embeddings" / "data"
CACHE_DIR = REPO_ROOT / "evals" / "embeddings" / "cache"
# Ручные поисковые запросы — без персональных данных, поэтому в git (в отличие от DATA_DIR).
MANUAL_QUERIES_PATH = REPO_ROOT / "evals" / "golden" / "embedding_queries.jsonl"


@dataclass
class CorpusDoc:
    """Уникальная вакансия; ``text`` — ровно то, что прод отправляет в эмбеддинг (``vacancy_text``)."""

    id: str
    text: str
    specializations: list[str] = field(default_factory=list)
    grade: str = "unknown"
    technologies: list[str] = field(default_factory=list)
    company_key: str | None = None


@dataclass
class DupPair:
    """Скрытая копия и исходная вакансия корпуса: копия должна найти оригинал первым."""

    dup_id: str
    dup_text: str
    original_id: str


@dataclass
class ResumeCase:
    """Резюме и LLM-оценки его матчей (``overall_score`` 0–100) — разметка для подбора."""

    resume_id: int
    text: str
    matches: dict[str, int] = field(default_factory=dict)


@dataclass
class Query:
    """Поисковый запрос. ``source_id`` — вакансия, по которой его написала LLM (у ручных — None)."""

    qid: str
    text: str
    kind: str  # keywords | natural | manual
    source_id: str | None = None


@dataclass
class Judgment:
    """Оценка релевантности вакансии запросу: 0 — нет, 1 — частично, 2 — подходит."""

    qid: str
    vacancy_id: str
    grade: int
    judge_model: str = ""


def write_jsonl(path: Path, rows: Iterable[Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(asdict(row) if hasattr(row, "__dataclass_fields__") else row, ensure_ascii=False))
            f.write("\n")
            n += 1
    return n


def append_jsonl(path: Path, rows: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(asdict(row), ensure_ascii=False))
            f.write("\n")


def read_jsonl[T](path: Path, cls: type[T]) -> list[T]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [cls(**json.loads(line)) for line in f if line.strip()]


@dataclass
class Dataset:
    corpus: list[CorpusDoc]
    dups: list[DupPair]
    resumes: list[ResumeCase]
    queries: list[Query]
    judgments: list[Judgment]

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> Dataset:
        corpus = read_jsonl(data_dir / "corpus.jsonl", CorpusDoc)
        if not corpus:
            raise FileNotFoundError(
                f"Нет {data_dir / 'corpus.jsonl'}: сначала выгрузите набор — scripts/build_embedding_eval.py"
            )
        queries = read_jsonl(data_dir / "queries.jsonl", Query) + read_jsonl(MANUAL_QUERIES_PATH, Query)
        return cls(
            corpus=corpus,
            dups=read_jsonl(data_dir / "dups.jsonl", DupPair),
            resumes=read_jsonl(data_dir / "resumes.jsonl", ResumeCase),
            queries=queries,
            judgments=read_jsonl(data_dir / "judgments.jsonl", Judgment),
        )

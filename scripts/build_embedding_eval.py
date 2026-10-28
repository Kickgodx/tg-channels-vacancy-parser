"""Выгрузка набора для eval эмбеддингов из БД — транзакция READ ONLY, база не меняется.

  .venv/Scripts/python.exe scripts/build_embedding_eval.py [--out evals/embeddings/data]

Пишет corpus.jsonl (уникальные вакансии, текст — как у прод-индексации), dups.jsonl (скрытые
копии → оригинал), resumes.jsonl (резюме + LLM-оценки матчей). Каталог в .gitignore: там тексты
вакансий и резюме. Дальше — ``python -m tg_jobs_parser.evals.embeddings``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, aliased

from backend.db.models import ResumeRow, VacancyMatchRow, VacancyRow
from backend.services.embedding_service import vacancy_text
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.evals.embeddings.data import DATA_DIR, CorpusDoc, DupPair, ResumeCase, write_jsonl


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", type=Path, default=DATA_DIR)
    args = p.parse_args()

    settings = get_settings()
    max_chars = load_config(settings.config_path).ai.embeddings.max_input_chars
    engine = create_engine(settings.database_url)
    with Session(engine) as s:
        s.execute(text("SET TRANSACTION READ ONLY"))
        rows = list(s.scalars(select(VacancyRow).where(VacancyRow.is_duplicate.is_(False)).order_by(VacancyRow.id)))
        corpus = [
            CorpusDoc(
                id=r.id,
                text=vacancy_text(r, max_chars=max_chars),
                specializations=list(r.detected_specializations or []),
                grade=r.detected_grade or "unknown",
                technologies=list(r.technologies or []),
                company_key=r.company_key,
            )
            for r in rows
        ]
        ids = {d.id for d in corpus}

        dups: dict[str, DupPair] = {}
        copies = s.scalars(select(VacancyRow).where(VacancyRow.is_duplicate.is_(True))).all()
        orig = aliased(VacancyRow)
        group_original = dict(
            s.execute(
                select(orig.duplicate_group_id, orig.id).where(
                    orig.is_duplicate.is_(False), orig.duplicate_group_id.is_not(None)
                )
            ).all()
        )
        for c in copies:
            target = c.duplicate_of or group_original.get(c.duplicate_group_id)
            if target in ids and c.id not in dups:
                dups[c.id] = DupPair(dup_id=c.id, dup_text=vacancy_text(c, max_chars=max_chars), original_id=target)

        resumes = []
        for r in s.scalars(select(ResumeRow).order_by(ResumeRow.id)):
            matches = dict(
                s.execute(
                    select(VacancyMatchRow.vacancy_id, VacancyMatchRow.overall_score).where(
                        VacancyMatchRow.resume_id == r.id
                    )
                ).all()
            )
            resumes.append(ResumeCase(resume_id=r.id, text=r.text[:max_chars], matches=matches))
        s.rollback()

    print(f"corpus: {write_jsonl(args.out / 'corpus.jsonl', corpus)}")
    print(f"dups: {write_jsonl(args.out / 'dups.jsonl', dups.values())}")
    print(f"resumes: {write_jsonl(args.out / 'resumes.jsonl', resumes)} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

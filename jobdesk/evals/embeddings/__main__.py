"""CLI eval эмбеддингов.

  # 0. выгрузка набора из БД (только чтение): .venv/Scripts/python.exe scripts/build_embedding_eval.py
  # 1. синтетические запросы по вакансиям (LLM, один раз):
  .venv/Scripts/python.exe -m jobdesk.evals.embeddings queries --n 150
  # 2. эмбеддинги всех вариантов (кэш), пул + LLM-судья, отчёт:
  .venv/Scripts/python.exe -m jobdesk.evals.embeddings run
  .venv/Scripts/python.exe -m jobdesk.evals.embeddings run --variants 3-small qwen3-8b+instr --no-judge

Отчёт — evals/reports/embeddings_<timestamp>.{md,json}. Повторный run берёт векторы и оценки из
кэша и платит только за новое (новый вариант, новые документы в пуле судьи).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import UTC, datetime

from jobdesk.evals.embeddings import llm
from jobdesk.evals.embeddings.data import DATA_DIR, Dataset, Query, append_jsonl, write_jsonl
from jobdesk.evals.embeddings.embedder import DEFAULT_VARIANTS, VARIANTS, Embedder
from jobdesk.evals.embeddings.evaluate import (
    Prepared,
    judged_query_set,
    render_markdown,
    run_variant,
    similar_qid,
)
from jobdesk.evals.embeddings.metrics import equivalent_threshold
from jobdesk.evals.paths import reports_dir

# Порог «почти тот же текст» в «Похожих» (backend NEAR_DUPLICATE_DISTANCE), подобран под 3-small.
BASE_DUP_THRESHOLD = 0.02
BASELINE = "3-small"


def _client(config_path: str):
    from jobdesk.ai.clients.openrouter import OpenRouterClient
    from jobdesk.config.loader import load_config
    from jobdesk.utils.env_loader import try_load_dotenv

    try_load_dotenv()
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        raise SystemExit("OPENROUTER_API_KEY не задан")
    cfg = load_config(config_path)
    return OpenRouterClient(cfg.ai), cfg.ai


def cmd_queries(args: argparse.Namespace) -> int:
    path = DATA_DIR / "queries.jsonl"
    if path.exists() and not args.force:
        print(f"{path} уже есть — --force, чтобы сгенерировать заново")
        return 1
    ds = Dataset.load()
    client, ai = _client(args.config)
    model = args.model or ai.model
    docs = llm.sample_sources(ds.corpus, args.n, seed=args.seed)
    queries = llm.generate_queries(llm.openrouter_chat(client, model), docs)
    write_jsonl(path, queries)
    print(f"{len(queries)} запросов по {len(docs)} вакансиям ({model}) -> {path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    unknown = [v for v in args.variants if v not in VARIANTS]
    if unknown:
        raise SystemExit(f"Неизвестные варианты: {unknown}; есть: {list(VARIANTS)}")
    ds = Dataset.load()
    client, ai = _client(args.config)
    prep = Prepared.build(ds)
    print(
        f"Корпус {len(ds.corpus)}, запросов {len(ds.queries)} (синтетических {len(prep.synthetic)}), "
        f"дублей {len(ds.dups)}, резюме {len(ds.resumes)}, троек {len(prep.triplets)}"
    )

    def embed_fn(texts, *, model, dimensions):
        vectors, usage = client.embed(texts, model=model, dimensions=dimensions)
        return vectors, usage.prompt_tokens

    embedder = Embedder(embed_fn, batch_size=args.batch_size)
    variants = [VARIANTS[v] for v in args.variants]
    runs = []
    for v in variants:
        print(f"-> {v.name} ({v.model})", flush=True)
        runs.append(run_variant(v, prep, embedder))

    if args.judge:
        judged = _judge_pool(args, prep, runs, client, ai)
        if judged:
            prep.refresh_judgments()
            runs = [run_variant(r.variant, prep, embedder) for r in runs]  # векторы уже в кэше

    base = next((r for r in runs if r.variant.name == BASELINE), None)
    tokens: dict[str, str] = {}
    for r in runs:
        if base is not None:
            r.metrics["scale.dup_threshold_equiv"] = equivalent_threshold(base.nn_dist, BASE_DUP_THRESHOLD, r.nn_dist)
        st = embedder.stats.get(r.variant.model)
        tokens[r.variant.name] = str(st.tokens if st else 0)
    extra = {"cost.tokens_this_run": tokens}

    table = render_markdown(runs, extra)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = reports_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Eval эмбеддингов {ts}\n\nКорпус {len(ds.corpus)} вакансий, размерность 1536, "
        f"синтетических запросов {len(prep.synthetic)}, троек {len(prep.triplets)}, пар дублей {len(ds.dups)}.\n\n"
    )
    (out_dir / f"embeddings_{ts}.md").write_text(header + table + "\n", encoding="utf-8", newline="\n")
    (out_dir / f"embeddings_{ts}.json").write_text(
        json.dumps(
            {r.variant.name: {k: (None if math.isnan(v) else v) for k, v in r.metrics.items()} for r in runs},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(table)
    print(f"\nОтчёт: {out_dir / f'embeddings_{ts}.md'}")
    return 0


def _judge_pool(args, prep: Prepared, runs, client, ai) -> int:
    """Пул: объединение top-10 всех вариантов по запросам выборки; судья оценивает неоценённое."""
    model = args.judge_model or ai.model
    chat = llm.openrouter_chat(client, model)
    by_id = {d.id: d for d in prep.ds.corpus}
    total = 0
    queries = judged_query_set(prep, args.judge_n)
    for n, q in enumerate(queries, 1):
        known = prep.grades_by_qid.get(q.qid, {})
        pool = list(dict.fromkeys(vid for r in runs for vid in r.top_ids.get(q.qid, [])))
        todo = [by_id[v] for v in pool if v not in known]
        if not todo:
            continue
        new = llm.judge(chat, q, todo, judge_model=model)
        append_jsonl(DATA_DIR / "judgments.jsonl", new)
        prep.ds.judgments.extend(new)
        total += len(new)
        print(f"  судья {n}/{len(queries)}: «{q.text[:50]}» +{len(new)} оценок", flush=True)
    anchors = [prep.ds.corpus[a] for a in prep.similar_anchors]
    for n, anchor in enumerate(anchors, 1):
        qid = similar_qid(anchor.id)
        known = prep.grades_by_qid.get(qid, {})
        pool = list(dict.fromkeys(vid for r in runs for vid in r.top_ids.get(qid, [])))
        todo = [by_id[v] for v in pool if v not in known]
        if not todo:
            continue
        new = llm.judge(
            chat,
            Query(qid=qid, text="", kind="similar", source_id=anchor.id),
            todo,
            judge_model=model,
            system=llm.SIMILAR_SYSTEM,
            header=f"### Вакансия-образец\n{anchor.text[:2000]}\n\n## Список для оценки",
        )
        append_jsonl(DATA_DIR / "judgments.jsonl", new)
        prep.ds.judgments.extend(new)
        total += len(new)
        print(f"  судья «похожие» {n}/{len(anchors)}: +{len(new)} оценок", flush=True)
    print(f"Судья ({model}): новых оценок {total}")
    return total


def main(argv: list[str] | None = None) -> int:
    # Консоль Windows (cp1251) не печатает часть символов из ответов LLM — заменяем, а не падаем.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="python -m jobdesk.evals.embeddings", description=__doc__.split("\n")[0])
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("queries", help="синтетические поисковые запросы по вакансиям (LLM)")
    q.add_argument("--n", type=int, default=150, help="сколько вакансий (по 2 запроса на каждую)")
    q.add_argument("--model", default="", help="модель генерации (по умолчанию ai.model)")
    q.add_argument("--seed", type=int, default=0)
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_queries)

    r = sub.add_parser("run", help="эмбеддинги, LLM-судья и отчёт")
    r.add_argument("--variants", nargs="+", default=DEFAULT_VARIANTS, help=f"из {list(VARIANTS)}")
    r.add_argument("--judge", action=argparse.BooleanOptionalAction, default=True)
    r.add_argument("--judge-n", type=int, default=50, help="синтетических запросов в пул судьи (+ все ручные)")
    r.add_argument("--judge-model", default="", help="модель судьи (по умолчанию ai.model)")
    r.add_argument("--batch-size", type=int, default=32)
    r.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

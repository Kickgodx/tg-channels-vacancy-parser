"""Опциональный live-прогон golden extraction через OpenRouter.

Usage:
  .venv/Scripts/python.exe -m tg_jobs_parser.evals.run_extraction
  .venv/Scripts/python.exe -m tg_jobs_parser.evals.run_extraction --limit 5

Требует OPENROUTER_API_KEY и ai.enabled (берётся config.yaml или --config).
Отчёт: evals/reports/extraction_<timestamp>.json (в .gitignore).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient
from tg_jobs_parser.ai.prompts import EXTRACT_PROMPT_VERSION
from tg_jobs_parser.ai.search_context import SearchContext
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.evals.golden import load_extraction_cases
from tg_jobs_parser.evals.paths import reports_dir
from tg_jobs_parser.evals.score_extraction import aggregate_scores, score_extraction
from tg_jobs_parser.utils.env_loader import try_load_dotenv


def _default_search_context() -> SearchContext:
    return SearchContext(
        target_specializations=[],
        target_work_formats=["any"],
        exclude_keywords=[],
        allowed_specializations=[
            "Go",
            "Golang",
            "Python",
            "Java",
            "Frontend",
            "Backend",
            "DevOps",
            "SRE",
            "QA",
            "Automation QA",
            "SDET",
            "iOS",
            "Android",
            "PHP",
            "Rust",
            "ML",
            "Analyst",
            "Data Analyst",
        ],
        allowed_work_formats=["remote", "hybrid", "office", "relocation", "unknown"],
        any_specialization=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live golden extraction eval")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Путь к config.yaml (default: config.yaml)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Макс. кейсов (0 = все)")
    parser.add_argument(
        "--min-exact-rate",
        type=float,
        default=0.0,
        help="Exit 1 если exact_match_rate ниже порога (0 = не проверять)",
    )
    args = parser.parse_args(argv)

    try_load_dotenv()
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        print("OPENROUTER_API_KEY не задан", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    if not cfg.ai.enabled:
        print("ai.enabled=false в конфиге — включите AI для live eval", file=sys.stderr)
        return 2

    cases = load_extraction_cases()
    if args.limit > 0:
        cases = cases[: args.limit]

    ctx = _default_search_context()
    client = OpenRouterClient(cfg.ai)
    rows: list[dict] = []
    scores: list[dict] = []
    try:
        for case in cases:
            cid = case["id"]
            text = case["text"]
            expected = case["expected"]
            try:
                extraction, usage = client.extract_with_fallback(
                    text=text,
                    search_context=ctx,
                )
                actual = extraction.model_dump(mode="json")
                score = score_extraction(actual, expected)
                rows.append(
                    {
                        "id": cid,
                        "ok": score["all_matched"],
                        "accuracy": score["accuracy"],
                        "field_results": score["field_results"],
                        "model": usage.model,
                        "tokens_in": usage.prompt_tokens,
                        "tokens_out": usage.completion_tokens,
                        "actual": actual,
                    }
                )
                scores.append(score)
                mark = "OK" if score["all_matched"] else "FAIL"
                print(f"[{mark}] {cid} accuracy={score['accuracy']:.2f}")
            except Exception as e:
                print(f"[ERR] {cid}: {e}", file=sys.stderr)
                rows.append({"id": cid, "ok": False, "error": str(e)})
                scores.append(
                    {
                        "field_results": {},
                        "matched": 0,
                        "total": 1,
                        "accuracy": 0.0,
                        "all_matched": False,
                    }
                )
    finally:
        client.close()

    summary = aggregate_scores(scores)
    report = {
        "prompt_version": EXTRACT_PROMPT_VERSION,
        "model": cfg.ai.model,
        "summary": summary,
        "cases": rows,
    }
    out_dir = reports_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"extraction_{stamp}.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"\nexact_match_rate={summary['exact_match_rate']:.3f} "
        f"mean_field_accuracy={summary['mean_field_accuracy']:.3f} "
        f"cases={summary['cases']}"
    )
    print(f"report: {out_path}")
    if args.min_exact_rate > 0 and summary["exact_match_rate"] < args.min_exact_rate:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

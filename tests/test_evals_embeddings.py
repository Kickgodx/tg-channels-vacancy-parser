"""Eval эмбеддингов: метрики, кэш векторов, разбор ответов LLM — офлайн, без БД и сети."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tg_jobs_parser.evals.embeddings import metrics as m
from tg_jobs_parser.evals.embeddings.data import CorpusDoc, Dataset, DupPair, Judgment, Query, ResumeCase
from tg_jobs_parser.evals.embeddings.embedder import VARIANTS, Embedder
from tg_jobs_parser.evals.embeddings.evaluate import Prepared, render_markdown, run_variant
from tg_jobs_parser.evals.embeddings.llm import generate_queries, judge, sample_sources


def _unit(rows):
    x = np.asarray(rows, dtype=np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def test_ranks_mrr_recall():
    sims = np.array([[0.9, 0.5, 0.1], [0.2, 0.3, 0.8], [0.6, 0.7, 0.1]])
    ranks = m.ranks_of_targets(sims, [0, 1, 0])
    assert ranks.tolist() == [1, 2, 2]
    assert m.mrr_at(ranks, 10) == pytest.approx((1 + 0.5 + 0.5) / 3)
    assert m.mrr_at(ranks, 1) == pytest.approx(1 / 3)
    assert m.recall_at(ranks, 1) == pytest.approx(1 / 3)


def test_top_k_excludes_self_and_orders():
    x = _unit([[1, 0], [0.9, 0.1], [0, 1], [0.5, 0.5]])
    nb = m.top_k(x @ x.T, 2, exclude_self=True)
    assert nb[0].tolist() == [1, 3]
    assert 2 not in nb[0]


def test_ndcg_precision_and_coverage():
    grades = {"a": 2, "b": 1, "c": 0}
    assert m.ndcg_at(["a", "b", "c"], grades, 3) == pytest.approx(1.0)
    worse = m.ndcg_at(["c", "b", "a"], grades, 3)
    assert 0 < worse < 1
    assert math.isnan(m.ndcg_at(["c"], {"c": 0}, 3))
    assert m.precision_at(["a", "x", "c"], grades, 3) == pytest.approx(1 / 3)
    assert m.precision_at(["a", "b"], grades, 2, min_grade=2) == pytest.approx(0.5)
    assert m.judged_share(["a", "x"], grades, 2) == pytest.approx(0.5)


def test_label_and_exact_agreement():
    labels = [frozenset({"QA"}), frozenset({"QA", "AQA"}), frozenset({"Backend"}), frozenset()]
    nb = np.array([[1, 2], [0, 2], [0, 1], [0, 1]])
    # якорь 0: 1 совпал, 2 нет → 0.5; якорь 1: 0.5; якорь 2: 0; якорь 3 без меток пропущен
    assert m.label_agreement(nb, labels) == pytest.approx(1 / 3)
    assert m.exact_agreement(np.array([[1, 2]]), ["middle", "middle", None]) == pytest.approx(1.0)


def test_build_triplets_hard_negative_shares_stack_not_role():
    roles = [frozenset({"Backend"}), frozenset({"Backend"}), frozenset({"ML"}), frozenset({"Frontend"})]
    grades = ["middle", "middle", "middle", "middle"]
    techs = [frozenset({"python", "django"}), frozenset({"go"}), frozenset({"python", "django", "torch"}), frozenset()]
    trips = m.build_triplets(roles, grades, techs, ["a", "b", "c", "d"], n=5)
    assert (0, 1, 2) in trips
    x = _unit([[1, 0], [0.9, 0.2], [0.2, 1], [0, 1]])
    assert m.triplet_accuracy(x, trips) == pytest.approx(1.0)


def test_equivalent_threshold_matches_percentile():
    base = np.linspace(0, 1, 101)
    other = np.linspace(0, 0.5, 101)
    assert m.equivalent_threshold(base, 0.1, other) == pytest.approx(0.05, abs=0.01)


def test_variant_query_text_mirrors_prod_and_instruction():
    assert VARIANTS["3-small"].query_text("  Python   FastAPI ") == "python fastapi"
    q = VARIANTS["qwen3-8b+instr"].query_text("Go")
    assert q.startswith("Instruct: ") and q.endswith("\nQuery:go")
    assert VARIANTS["qwen3-8b"].resume_text("CV") == "CV"


class _FakeEmbed:
    """Детерминированный «эмбеддинг»: мешок символов → вектор; считает вызовы, падает на больших батчах."""

    def __init__(self, max_batch: int = 100):
        self.calls = 0
        self.max_batch = max_batch

    def __call__(self, texts, *, model, dimensions):
        self.calls += 1
        if len(texts) > self.max_batch:
            raise RuntimeError("batch too large")
        out = []
        for t in texts:
            v = np.zeros(dimensions, dtype=np.float32)
            for ch in t.lower():
                v[ord(ch) % dimensions] += 1.0
            v[0] += 0.01
            out.append((v * 3).tolist())  # ненормированный, как у gemini/pplx
        return out, 7 * len(texts)


def test_embedder_caches_normalizes_and_splits_batches(tmp_path):
    fake = _FakeEmbed(max_batch=2)
    emb = Embedder(fake, batch_size=4, cache_dir=tmp_path)
    x = emb.embed("m/x", ["a", "b", "c", "a"])
    assert x.shape == (4, 1536)
    assert np.allclose(np.linalg.norm(x, axis=1), 1.0)
    assert np.allclose(x[0], x[3])
    calls = fake.calls
    # новый Embedder читает кэш с диска — в API не ходит
    emb2 = Embedder(fake, cache_dir=tmp_path)
    emb2.embed("m/x", ["c", "b"])
    assert fake.calls == calls
    assert emb2.stats["m/x"].cached == 2


def _dataset():
    corpus = [
        CorpusDoc("v1", "python backend django", ["Backend"], "middle", ["Python", "Django"], "a"),
        CorpusDoc("v2", "python backend fastapi", ["Backend"], "middle", ["Python"], "b"),
        CorpusDoc("v3", "qa manual testing", ["QA"], "junior", ["Jira"], "c"),
        CorpusDoc("v4", "ml python torch django", ["ML"], "middle", ["Python", "Django", "Torch"], "d"),
        CorpusDoc("v5", "frontend react typescript", ["Frontend"], "senior", ["React"], "e"),
    ]
    return Dataset(
        corpus=corpus,
        dups=[DupPair("d1", "python backend django!", "v1")],
        resumes=[ResumeCase(1, "python django backend", {"v1": 90, "v2": 70, "v3": 10, "v4": 40, "v5": 5, "zz": 1})],
        queries=[
            Query("k-v3", "qa testing", "keywords", "v3"),
            Query("m-01", "python backend", "manual", None),
        ],
        judgments=[],
    )


def test_run_variant_end_to_end_offline(tmp_path):
    ds = _dataset()
    prep = Prepared.build(ds, n_triplets=10)
    emb = Embedder(_FakeEmbed(), cache_dir=tmp_path)
    run = run_variant(VARIANTS["3-small"], prep, emb)
    assert run.metrics["search.recall@10"] == 1.0
    assert run.metrics["dups.recall@1"] == 1.0
    assert "resume.spearman" in run.metrics
    assert "judged.ndcg@10" not in run.metrics
    assert set(run.top_ids) == {"k-v3", "m-01"}

    # «Похожие»: образец v1, без себя и почти-копий; оценки судьи → similar.judged.*
    prep.similar_anchors = [0]
    run = run_variant(VARIANTS["3-small"], prep, emb)
    assert "v1" not in run.top_ids["s-v1"] and len(run.top_ids["s-v1"]) == 4
    assert "similar.judged.ndcg@5" not in run.metrics

    ds.judgments = [
        Judgment("m-01", "v1", 2),
        Judgment("m-01", "v2", 2),
        Judgment("m-01", "v3", 0),
        Judgment("s-v1", "v2", 2),
        Judgment("s-v1", "v3", 0),
    ]
    prep.refresh_judgments()
    run = run_variant(VARIANTS["3-small"], prep, emb)
    assert 0 <= run.metrics["judged.ndcg@10"] <= 1
    assert 0 <= run.metrics["similar.judged.ndcg@5"] <= 1
    assert run.metrics["similar.judged.coverage@5"] == pytest.approx(2 / 5)
    table = render_markdown([run])
    assert "| search.mrr@10 |" in table


def test_sample_sources_round_robin_by_role():
    corpus = [CorpusDoc(f"q{i}", "x" * 400, ["QA"]) for i in range(10)] + [CorpusDoc("g1", "x" * 400, ["Go"])]
    picked = sample_sources(corpus, 3)
    assert "g1" in {d.id for d in picked}


def test_generate_queries_and_judge_parse_llm_json():
    docs = [CorpusDoc("abcdef0123456789", "vacancy one"), CorpusDoc("fedcba9876543210", "vacancy two")]

    def chat(messages):
        if "Запрос:" in messages[1]["content"]:
            return {"grades": {"1": 2, "2": "0", "3": 1}}
        return {"items": [{"i": 2, "keywords": "go senior", "natural": "Ищу go"}, {"i": 9, "keywords": "x"}]}

    qs = generate_queries(chat, docs)
    assert [(q.kind, q.source_id) for q in qs] == [("keywords", docs[1].id), ("natural", docs[1].id)]
    js = judge(chat, qs[0], docs, judge_model="j")
    assert [(j.vacancy_id, j.grade) for j in js] == [(docs[0].id, 2), (docs[1].id, 0)]


def test_judge_skips_failed_chunk():
    def chat(messages):
        raise RuntimeError("boom")

    assert judge(chat, Query("q", "t", "manual"), [CorpusDoc("a", "t")], judge_model="j") == []

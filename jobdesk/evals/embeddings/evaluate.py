"""Прогон одного варианта модели по всем задачам набора и сводный отчёт."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np

from jobdesk.evals.embeddings import metrics as m
from jobdesk.evals.embeddings.data import Dataset, Query
from jobdesk.evals.embeddings.embedder import Embedder, Variant
from jobdesk.evals.embeddings.llm import sample_sources

K = 10
K_SIMILAR = 5  # «Похожие» в UI — короткий список
MIN_RESUME_MATCHES = 5
# Как в проде (NEAR_DUPLICATE_DISTANCE): ближе — та же вакансия, а не похожая.
NEAR_DUPLICATE_DISTANCE = 0.02


def similar_qid(vacancy_id: str) -> str:
    return f"s-{vacancy_id[:12]}"


@dataclass
class Prepared:
    """Общее для всех вариантов: индексы, метки, тройки — чтобы модели сравнивались на одном и том же."""

    ds: Dataset
    index: dict[str, int]
    roles: list[frozenset[str]]
    grades: list[str | None]
    primary_role: list[str | None]
    triplets: list[tuple[int, int, int]]
    synthetic: list[Query]
    similar_anchors: list[int] = field(default_factory=list)
    grades_by_qid: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def build(cls, ds: Dataset, *, n_triplets: int = 600, n_similar: int = 40) -> Prepared:
        corpus = ds.corpus
        roles = [frozenset(d.specializations) for d in corpus]
        grades = [d.grade if d.grade and d.grade != "unknown" else None for d in corpus]
        techs = [frozenset(t.lower() for t in d.technologies) for d in corpus]
        companies = [d.company_key for d in corpus]
        index = {d.id: i for i, d in enumerate(corpus)}
        p = cls(
            ds=ds,
            index=index,
            roles=roles,
            grades=grades,
            primary_role=[d.specializations[0] if d.specializations else None for d in corpus],
            triplets=m.build_triplets(roles, grades, techs, companies, n=n_triplets),
            synthetic=[q for q in ds.queries if q.source_id in index],
            similar_anchors=[index[d.id] for d in sample_sources(corpus, n_similar, seed=1)],
        )
        p.refresh_judgments()
        return p

    def refresh_judgments(self) -> None:
        by_q: dict[str, dict[str, int]] = {}
        for j in self.ds.judgments:
            by_q.setdefault(j.qid, {})[j.vacancy_id] = j.grade
        self.grades_by_qid = by_q


def judged_query_set(prep: Prepared, n_synthetic: int, *, seed: int = 0) -> list[Query]:
    """Запросы для пула судьи: все ручные + детерминированная выборка синтетических."""
    manual = [q for q in prep.ds.queries if q.kind == "manual"]
    synth = sorted(prep.synthetic, key=lambda q: q.qid)
    random.Random(seed).shuffle(synth)
    return manual + synth[:n_synthetic]


@dataclass
class VariantRun:
    variant: Variant
    metrics: dict[str, float]
    top_ids: dict[str, list[str]]  # qid → top-K id вакансий (для пула судьи)
    nn_dist: np.ndarray


def run_variant(variant: Variant, prep: Prepared, embedder: Embedder) -> VariantRun:
    ds = prep.ds
    ids = [d.id for d in ds.corpus]
    docs = embedder.embed(variant.model, [d.text for d in ds.corpus])
    out: dict[str, float] = {}

    # --- поиск: запросы → вакансии ---
    queries = ds.queries
    qv = embedder.embed(variant.model, [variant.query_text(q.text) for q in queries])
    q_sims = qv @ docs.T
    q_top = m.top_k(q_sims, K)
    top_ids = {q.qid: [ids[j] for j in q_top[i]] for i, q in enumerate(queries)}

    synth_rows = [i for i, q in enumerate(queries) if q.source_id in prep.index]
    if synth_rows:
        ranks = m.ranks_of_targets(q_sims[synth_rows], [prep.index[queries[i].source_id] for i in synth_rows])
        out["search.mrr@10"] = m.mrr_at(ranks, K)
        out["search.recall@1"] = m.recall_at(ranks, 1)
        out["search.recall@10"] = m.recall_at(ranks, 10)
        out["search.recall@50"] = m.recall_at(ranks, 50)
        for kind in ("keywords", "natural"):
            sel = [n for n, i in enumerate(synth_rows) if queries[i].kind == kind]
            if sel:
                out[f"search.{kind}.mrr@10"] = m.mrr_at(ranks[sel], K)
        role_hits = []
        for i in synth_rows:
            src_roles = prep.roles[prep.index[queries[i].source_id]]
            if src_roles:
                role_hits.append(np.mean([bool(src_roles & prep.roles[j]) for j in q_top[i]]))
        out["search.role_precision@10"] = float(np.mean(role_hits)) if role_hits else math.nan

    ndcgs, p_any, p_full, judged = [], [], [], []
    for q in queries:
        grades = prep.grades_by_qid.get(q.qid)
        if not grades:
            continue
        top = top_ids[q.qid]
        val = m.ndcg_at(top, grades, K)
        if not math.isnan(val):
            ndcgs.append(val)
        p_any.append(m.precision_at(top, grades, K, min_grade=1))
        p_full.append(m.precision_at(top, grades, K, min_grade=2))
        judged.append(m.judged_share(top, grades, K))
    if p_any:
        out["judged.ndcg@10"] = float(np.mean(ndcgs)) if ndcgs else math.nan
        out["judged.precision@10"] = float(np.mean(p_any))
        out["judged.precision@10_strict"] = float(np.mean(p_full))
        out["judged.coverage@10"] = float(np.mean(judged))
        out["judged.queries"] = float(len(p_any))

    # --- похожие: вакансия → вакансии ---
    d_sims = docs @ docs.T
    nbrs = m.top_k(d_sims, K, exclude_self=True)
    out["similar.role_agreement@10"] = m.label_agreement(nbrs, prep.roles)
    out["similar.grade_agreement@10"] = m.exact_agreement(nbrs, prep.grades)
    out["similar.triplet_acc"] = m.triplet_accuracy(docs, prep.triplets)
    out["similar.silhouette_role"] = m.silhouette_by_label(docs, prep.primary_role)

    # «Похожие» глазами судьи: top-5 соседей образца без почти-копий, как в проде.
    s_ndcg, s_any, s_full, s_judged = [], [], [], []
    for a in prep.similar_anchors:
        row = d_sims[a].copy()
        row[a] = -np.inf
        row[1.0 - row < NEAR_DUPLICATE_DISTANCE] = -np.inf
        qid = similar_qid(ids[a])
        top = [ids[j] for j in m.top_k(row[None, :], K_SIMILAR)[0] if np.isfinite(row[j])]
        top_ids[qid] = top
        grades = prep.grades_by_qid.get(qid)
        if not grades:
            continue
        val = m.ndcg_at(top, grades, K_SIMILAR)
        if not math.isnan(val):
            s_ndcg.append(val)
        s_any.append(m.precision_at(top, grades, K_SIMILAR, min_grade=1))
        s_full.append(m.precision_at(top, grades, K_SIMILAR, min_grade=2))
        s_judged.append(m.judged_share(top, grades, K_SIMILAR))
    if s_any:
        out["similar.judged.ndcg@5"] = float(np.mean(s_ndcg)) if s_ndcg else math.nan
        out["similar.judged.precision@5"] = float(np.mean(s_any))
        out["similar.judged.precision@5_strict"] = float(np.mean(s_full))
        out["similar.judged.coverage@5"] = float(np.mean(s_judged))

    # --- дубли: скрытая копия должна найти оригинал ---
    dups = [p for p in ds.dups if p.original_id in prep.index]
    if dups:
        dv = embedder.embed(variant.model, [p.dup_text for p in dups])
        targets = [prep.index[p.original_id] for p in dups]
        ranks = m.ranks_of_targets(dv @ docs.T, targets)
        out["dups.recall@1"] = m.recall_at(ranks, 1)
        out["dups.mrr@10"] = m.mrr_at(ranks, K)
        pair_dist = 1.0 - np.einsum("ij,ij->i", dv, docs[targets])
        out["dups.pair_distance_p50"] = float(np.median(pair_dist))
        out["dups.pair_distance_p90"] = float(np.quantile(pair_dist, 0.9))

    nn = m.nearest_distances(docs)
    out["scale.nn_distance_p5"] = float(np.quantile(nn, 0.05))
    out["scale.nn_distance_p50"] = float(np.median(nn))
    rng = np.random.default_rng(0)
    a, b = rng.integers(0, len(ids), 4000), rng.integers(0, len(ids), 4000)
    keep = a != b
    out["scale.random_pair_cos"] = float(np.mean(np.einsum("ij,ij->i", docs[a[keep]], docs[b[keep]])))

    # --- подбор под резюме: согласие косинуса с LLM-оценкой матча ---
    rhos, weights = [], []
    for r in ds.resumes:
        pairs = [(prep.index[v], s) for v, s in r.matches.items() if v in prep.index]
        if len(pairs) < MIN_RESUME_MATCHES:
            continue
        rv = embedder.embed(variant.model, [variant.resume_text(r.text)])[0]
        sims = docs[[i for i, _ in pairs]] @ rv
        rho = m.spearman(sims.tolist(), [s for _, s in pairs])
        if not math.isnan(rho):
            rhos.append(rho)
            weights.append(len(pairs))
    if rhos:
        out["resume.spearman"] = float(np.average(rhos, weights=weights))
        out["resume.pairs"] = float(sum(weights))

    return VariantRun(variant=variant, metrics=out, top_ids=top_ids, nn_dist=nn)


# Направление «лучше»: True — больше лучше; None — справочная строка, без выделения.
_DIRECTION: dict[str, bool | None] = {
    "judged.coverage@10": None,
    "similar.judged.coverage@5": None,
    "judged.queries": None,
    "resume.pairs": None,
    "dups.pair_distance_p50": None,
    "dups.pair_distance_p90": None,
    "scale.nn_distance_p5": None,
    "scale.nn_distance_p50": None,
    "scale.random_pair_cos": None,
    "scale.dup_threshold_equiv": None,
}


def render_markdown(runs: list[VariantRun], extra_rows: dict[str, dict[str, str]] | None = None) -> str:
    names = [r.variant.name for r in runs]
    keys: list[str] = []
    for r in runs:
        keys += [k for k in r.metrics if k not in keys]
    lines = ["| метрика | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for k in keys:
        vals = [r.metrics.get(k, math.nan) for r in runs]
        finite = [v for v in vals if not math.isnan(v)]
        best = max(finite) if finite and _DIRECTION.get(k, True) else None
        cells = []
        for v in vals:
            if math.isnan(v):
                cells.append("—")
            else:
                s = f"{v:.0f}" if k.endswith((".queries", ".pairs")) else f"{v:.3f}"
                cells.append(f"**{s}**" if best is not None and v == best and len(finite) > 1 else s)
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    for label, row in (extra_rows or {}).items():
        lines.append(f"| {label} | " + " | ".join(row.get(n, "—") for n in names) + " |")
    return "\n".join(lines)

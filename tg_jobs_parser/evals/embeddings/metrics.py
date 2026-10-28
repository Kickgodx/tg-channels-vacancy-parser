"""Метрики eval эмбеддингов. Векторы на входе L2-нормированы: скалярное произведение = косинус.

Абсолютные значения косинуса между моделями несравнимы (у каждой своя шкала), поэтому почти
все метрики — ранговые: на каком месте нужный документ, совпадают ли метки у соседей.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Sequence

import numpy as np


def ranks_of_targets(sims: np.ndarray, targets: Sequence[int]) -> np.ndarray:
    """1-based место ``targets[i]`` в строке ``sims[i]`` (при равенстве — оптимистично)."""
    t = np.asarray(targets)
    target_sims = sims[np.arange(len(t)), t]
    return (sims > target_sims[:, None]).sum(axis=1) + 1


def mrr_at(ranks: np.ndarray, k: int = 10) -> float:
    if len(ranks) == 0:
        return float("nan")
    return float(np.mean(np.where(ranks <= k, 1.0 / ranks, 0.0)))


def recall_at(ranks: np.ndarray, k: int) -> float:
    return float(np.mean(ranks <= k)) if len(ranks) else float("nan")


def top_k(sims: np.ndarray, k: int, *, exclude_self: bool = False) -> np.ndarray:
    """Индексы k ближайших по убыванию сходства; ``exclude_self`` — для матрицы корпус×корпус."""
    s = sims.copy()
    if exclude_self:
        np.fill_diagonal(s, -np.inf)
    k = min(k, s.shape[1])
    part = np.argpartition(-s, k - 1, axis=1)[:, :k]
    order = np.argsort(-np.take_along_axis(s, part, axis=1), axis=1, kind="stable")
    return np.take_along_axis(part, order, axis=1)


def dcg(gains: Sequence[float]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at(ranked_ids: Sequence[str], grades: dict[str, int], k: int = 10) -> float:
    """nDCG@k по оценкам 0/1/2; неоценённый документ — 0. Нет ни одного релевантного — NaN."""
    ideal = dcg(sorted(grades.values(), reverse=True)[:k])
    if ideal == 0:
        return float("nan")
    return dcg([grades.get(d, 0) for d in ranked_ids[:k]]) / ideal


def precision_at(ranked_ids: Sequence[str], grades: dict[str, int], k: int = 10, *, min_grade: int = 1) -> float:
    return sum(1 for d in ranked_ids[:k] if grades.get(d, 0) >= min_grade) / k


def judged_share(ranked_ids: Sequence[str], grades: dict[str, int], k: int = 10) -> float:
    """Доля top-k с оценкой: ниже 1.0 — модель не была в пуле, её nDCG занижен."""
    return sum(1 for d in ranked_ids[:k] if d in grades) / k


def label_agreement(neighbors: np.ndarray, labels: Sequence[frozenset[str]]) -> float:
    """Средняя доля соседей, у которых есть хоть одна общая метка (роль) с якорем."""
    vals = []
    for i, row in enumerate(neighbors):
        if not labels[i]:
            continue
        vals.append(np.mean([bool(labels[i] & labels[j]) for j in row]))
    return float(np.mean(vals)) if vals else float("nan")


def exact_agreement(neighbors: np.ndarray, values: Sequence[str | None]) -> float:
    """Доля соседей с тем же значением (грейд); якоря и соседи без значения пропускаются."""
    vals = []
    for i, row in enumerate(neighbors):
        if values[i] is None:
            continue
        known = [values[j] for j in row if values[j] is not None]
        if known:
            vals.append(np.mean([v == values[i] for v in known]))
    return float(np.mean(vals)) if vals else float("nan")


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def build_triplets(
    roles: Sequence[frozenset[str]],
    grades: Sequence[str | None],
    techs: Sequence[frozenset[str]],
    companies: Sequence[str | None],
    *,
    n: int,
    seed: int = 0,
) -> list[tuple[int, int, int]]:
    """Тройки (якорь, позитив, трудный негатив).

    Позитив — та же роль и грейд, другая компания. Негатив — роль без пересечения с якорем, но
    с максимально похожим стеком: проверяет «роль важнее технологий» (Python-бэкенд ≠ Python-ML).
    """
    rng = random.Random(seed)
    by_key: dict[tuple[frozenset[str], str], list[int]] = {}
    for i, (r, g) in enumerate(zip(roles, grades, strict=True)):
        if r and g:
            by_key.setdefault((r, g), []).append(i)
    anchors = [i for i in range(len(roles)) if roles[i] and grades[i] and techs[i]]
    rng.shuffle(anchors)
    out: list[tuple[int, int, int]] = []
    for a in anchors:
        if len(out) >= n:
            break
        pos = [
            j
            for j in by_key[(roles[a], grades[a])]
            if j != a and (companies[j] is None or companies[j] != companies[a])
        ]
        if not pos:
            continue
        best, best_j = 0.0, -1
        for j in range(len(roles)):
            if roles[j] and not (roles[j] & roles[a]):
                s = jaccard(techs[a], techs[j])
                if s > best:
                    best, best_j = s, j
        if best_j < 0 or best < 0.3:
            continue
        out.append((a, rng.choice(pos), best_j))
    return out


def triplet_accuracy(x: np.ndarray, triplets: Sequence[tuple[int, int, int]]) -> float:
    if not triplets:
        return float("nan")
    t = np.asarray(triplets)
    sp = np.einsum("ij,ij->i", x[t[:, 0]], x[t[:, 1]])
    sn = np.einsum("ij,ij->i", x[t[:, 0]], x[t[:, 2]])
    return float(np.mean(sp > sn))


def silhouette_by_label(x: np.ndarray, labels: Sequence[str | None], *, min_size: int = 20) -> float:
    """Силуэт кластеров «основная роль» (метки с ≥ ``min_size`` вакансий), косинусная метрика."""
    from sklearn.metrics import silhouette_score

    counts = Counter(lab for lab in labels if lab)
    idx = [i for i, lab in enumerate(labels) if lab and counts[lab] >= min_size]
    used = {labels[i] for i in idx}
    if len(used) < 2:
        return float("nan")
    return float(silhouette_score(x[idx], [labels[i] for i in idx], metric="cosine"))


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    from scipy.stats import spearmanr

    if len(a) < 3:
        return float("nan")
    return float(spearmanr(a, b).statistic)


def nearest_distances(x: np.ndarray) -> np.ndarray:
    """Косинусное расстояние каждой вакансии до ближайшей другой (для калибровки порога дублей)."""
    sims = x @ x.T
    np.fill_diagonal(sims, -np.inf)
    return 1.0 - sims.max(axis=1)


def equivalent_threshold(base_nn: np.ndarray, base_threshold: float, other_nn: np.ndarray) -> float:
    """Порог для другой модели на том же перцентиле распределения ближайших соседей."""
    p = float(np.mean(base_nn < base_threshold))
    return float(np.quantile(other_nn, p)) if p > 0 else float(other_nn.min())

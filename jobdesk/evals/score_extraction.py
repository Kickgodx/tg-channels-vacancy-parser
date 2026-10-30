"""Field-level scoring: actual LLM extraction vs golden expected."""

from __future__ import annotations

from typing import Any

# Поля, которые сравниваем как множества строк (порядок не важен).
_SET_FIELDS = frozenset(
    {
        "detected_specializations",
        "technologies",
        "application_urls",
    }
)
# Числовые поля с допуском (относительный / абсолютный).
_NUMERIC_FIELDS = frozenset({"salary_min", "salary_max", "confidence"})

_SALARY_ABS_TOL = 1.0
_SALARY_REL_TOL = 0.02
_CONFIDENCE_ABS_TOL = 0.05


def _norm_str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s.casefold() if s else None


def _norm_str_set(v: object) -> set[str]:
    if v is None:
        return set()
    if isinstance(v, str):
        s = v.strip()
        return {s.casefold()} if s else set()
    if isinstance(v, list):
        out: set[str] = set()
        for x in v:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.add(s.casefold())
        return out
    s = str(v).strip()
    return {s.casefold()} if s else set()


def _as_float(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _numbers_close(a: float | None, b: float | None, *, field: str) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if field == "confidence":
        return abs(a - b) <= _CONFIDENCE_ABS_TOL
    abs_tol = _SALARY_ABS_TOL
    rel = _SALARY_REL_TOL * max(abs(a), abs(b), 1.0)
    return abs(a - b) <= max(abs_tol, rel)


def _get_field(obj: dict[str, Any] | Any, name: str) -> object:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def score_extraction(
    actual: dict[str, Any] | Any,
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Сравнивает только поля, присутствующие в ``expected``.

    Returns:
        dict с keys: field_results, matched, total, accuracy, all_matched
    """
    if not expected:
        return {
            "field_results": {},
            "matched": 0,
            "total": 0,
            "accuracy": 1.0,
            "all_matched": True,
        }

    field_results: dict[str, bool] = {}
    for name, exp_val in expected.items():
        act_val = _get_field(actual, name)
        if name in _SET_FIELDS:
            ok = _norm_str_set(act_val) == _norm_str_set(exp_val)
        elif name in _NUMERIC_FIELDS:
            ok = _numbers_close(_as_float(act_val), _as_float(exp_val), field=name)
        elif isinstance(exp_val, bool) or name == "is_job_post":
            ok = bool(act_val) is bool(exp_val)
        elif exp_val is None:
            ok = act_val is None or (isinstance(act_val, str) and not str(act_val).strip())
        else:
            ok = _norm_str(act_val) == _norm_str(exp_val)
        field_results[name] = ok

    matched = sum(1 for v in field_results.values() if v)
    total = len(field_results)
    accuracy = matched / total if total else 1.0
    return {
        "field_results": field_results,
        "matched": matched,
        "total": total,
        "accuracy": accuracy,
        "all_matched": matched == total,
    }


def aggregate_scores(case_scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Агрегат по списку результатов ``score_extraction`` (+ опционально id)."""
    if not case_scores:
        return {
            "cases": 0,
            "exact_match_rate": 1.0,
            "mean_field_accuracy": 1.0,
            "per_field": {},
        }

    exact = sum(1 for s in case_scores if s.get("all_matched"))
    mean_acc = sum(float(s.get("accuracy") or 0) for s in case_scores) / len(case_scores)

    field_hits: dict[str, list[bool]] = {}
    for s in case_scores:
        for fname, ok in (s.get("field_results") or {}).items():
            field_hits.setdefault(fname, []).append(bool(ok))

    per_field = {
        name: (sum(1 for x in hits if x) / len(hits) if hits else 1.0) for name, hits in sorted(field_hits.items())
    }
    return {
        "cases": len(case_scores),
        "exact_match_rate": exact / len(case_scores),
        "mean_field_accuracy": mean_acc,
        "per_field": per_field,
    }

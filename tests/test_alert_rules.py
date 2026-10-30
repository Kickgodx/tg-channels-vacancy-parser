"""Правила алертов (observability/alerts/): структура и метрики (офлайн).

Вычисление правил проверяет promtool (scripts/check_alerts.sh). Здесь — то, что promtool
не ловит: имя метрики в ``expr``, которой код больше не отдаёт. Такое правило молча
перестаёт срабатывать — ``absent`` и сравнения по пустому ряду ничего не возвращают.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RULE_FILES = sorted((ROOT / "observability" / "alerts").glob("*.yml"))

# Ключевые слова PromQL без скобок; функции и агрегаты отсекаются по «(» после имени.
_PROMQL_KEYWORDS = {"and", "or", "unless", "bool", "offset", "by", "without", "on", "ignoring"}
# Метрики самого Prometheus.
_PROMETHEUS_BUILTIN = {"up"}


def _alerts() -> list[dict]:
    rules = []
    for path in RULE_FILES:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for group in doc["groups"]:
            rules.extend(r for r in group["rules"] if "alert" in r)
    return rules


def metric_names(expr: str) -> set[str]:
    """Имена метрик в выражении PromQL (без функций, меток, окон и модификаторов)."""
    expr = re.sub(r'"(?:[^"\\]|\\.)*"', "", expr)
    expr = re.sub(r"\{[^}]*\}", "", expr)
    expr = re.sub(r"\[[^\]]*\]", "", expr)
    expr = re.sub(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", "", expr)
    names = set(re.findall(r"\b([a-zA-Z_:][a-zA-Z0-9_:]*)\b(?!\s*\()", expr))
    return names - _PROMQL_KEYWORDS


def _declared_metric_names() -> set[str]:
    """Имена, которые отдают процессы приложения и pg-backup."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from prometheus_client import REGISTRY, CollectorRegistry
    from prometheus_fastapi_instrumentator import Instrumentator

    # Импорт модулей регистрирует их метрики в REGISTRY.
    import backend.observability as app_metrics
    import backend.workers.liveness  # noqa: F401
    import jobdesk.hh.metrics
    import jobdesk.observability.llm_metrics
    import jobdesk.observability.parse_metrics  # noqa: F401

    names = set(REGISTRY._names_to_collectors)
    # db_* и прочие агрегаты считает сборщик процесса API; describe() не ходит в БД.
    names |= {fam.name for fam in app_metrics._DBStatsCollector().describe()}

    # HTTP-метрики instrumentator регистрирует на старте приложения — в своём registry.
    registry = CollectorRegistry()
    app = FastAPI()
    Instrumentator(registry=registry).instrument(app)
    with TestClient(app):
        pass
    names |= set(registry._names_to_collectors)

    script = (ROOT / "scripts" / "pg_backup.sh").read_text(encoding="utf-8")
    names |= set(re.findall(r'echo "(pg_backup_[a-z_]+) ', script))
    return names | _PROMETHEUS_BUILTIN


def test_rule_files_exist():
    assert RULE_FILES, "нет правил в observability/alerts/"


@pytest.mark.parametrize("rule", _alerts(), ids=lambda r: r["alert"])
def test_alert_has_severity_and_summary(rule):
    assert rule.get("labels", {}).get("severity") in {"critical", "warning"}
    assert rule.get("annotations", {}).get("summary")
    assert rule.get("annotations", {}).get("description")


def test_alert_names_are_unique():
    names = [r["alert"] for r in _alerts()]
    assert len(names) == len(set(names))


def test_alert_metrics_are_declared_in_code():
    declared = _declared_metric_names()
    missing = {
        rule["alert"]: sorted(metric_names(rule["expr"]) - declared)
        for rule in _alerts()
        if metric_names(rule["expr"]) - declared
    }
    assert not missing, f"метрик из правил нет в коде: {missing}"


def test_metric_names_parser():
    expr = """
      sum by (reason) (increase(llm_enrich_disabled_total{job="x", queue="a b"}[24h])) > 0
      and on (job) absent(up{job="w"})
      or time() - fx_latest_rate_timestamp_seconds > 3 * 86400
    """
    assert metric_names(expr) == {"llm_enrich_disabled_total", "up", "fx_latest_rate_timestamp_seconds"}


def test_renamed_metric_is_reported_missing():
    assert "llm_enrich_disabled_totl" not in _declared_metric_names()
    assert metric_names("increase(llm_enrich_disabled_totl[1h]) > 0") == {"llm_enrich_disabled_totl"}

"""Генератор дашборда Grafana «k6 load test» (observability/.../json/k6.json).

Дашборд собирается кодом, чтобы панели были единообразны; после правки —
`python scripts/loadtest/make_k6_dashboard.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "observability/grafana/provisioning/dashboards/json/k6.json"
DS = {"type": "prometheus", "uid": "prometheus"}
K6 = 'testid=~"$testid"'
BACKEND = 'job="$backend_job"'


def panel(pid: int, title: str, x: int, y: int, targets: list[tuple[str, str]], unit: str = "short", w: int = 12):
    return {
        "datasource": DS,
        "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}, "unit": unit}, "overrides": []},
        "gridPos": {"h": 8, "w": w, "x": x, "y": y},
        "id": pid,
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "none"},
        },
        "targets": [
            {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": chr(ord("A") + i)}
            for i, (expr, legend) in enumerate(targets)
        ],
        "title": title,
        "type": "timeseries",
    }


def row(pid: int, title: str, y: int):
    return {"collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "id": pid, "title": title, "type": "row"}


panels = [
    row(1, "k6 — со стороны клиента", 0),
    panel(2, "RPS (k6)", 0, 1, [(f"sum by (testid) (rate(k6_http_reqs_total{{{K6}}}[30s]))", "{{testid}}")], "reqps"),
    panel(
        3,
        "Задержка (k6)",
        12,
        1,
        [
            (f"max by (testid) (k6_http_req_duration_p50{{{K6}}})", "p50 {{testid}}"),
            (f"max by (testid) (k6_http_req_duration_p95{{{K6}}})", "p95 {{testid}}"),
            (f"max by (testid) (k6_http_req_duration_p99{{{K6}}})", "p99 {{testid}}"),
        ],
        "s",
    ),
    panel(
        4,
        "Доля неуспешных проверок (k6)",
        0,
        9,
        [(f"1 - avg by (testid) (k6_checks_rate{{{K6}}})", "{{testid}}")],
        "percentunit",
    ),
    panel(5, "Виртуальные пользователи (k6)", 12, 9, [(f"sum by (testid) (k6_vus{{{K6}}})", "{{testid}}")]),
    row(6, "Backend — со стороны сервера ($backend_job)", 17),
    panel(
        7,
        "RPS backend по статусу",
        0,
        18,
        [(f"sum by (status) (rate(http_requests_total{{{BACKEND}}}[30s]))", "{{status}}")],
        "reqps",
    ),
    panel(
        8,
        "Задержка backend",
        12,
        18,
        [
            (
                f"histogram_quantile({q}, sum by (le) (rate(http_request_duration_seconds_bucket{{{BACKEND}}}[30s])))",
                f"p{int(q * 100)}",
            )
            for q in (0.5, 0.95, 0.99)
        ],
        "s",
    ),
    panel(
        9,
        "RPS backend по эндпоинтам",
        0,
        26,
        [(f"topk(10, sum by (handler) (rate(http_requests_total{{{BACKEND}}}[30s])))", "{{handler}}")],
        "reqps",
    ),
    panel(
        10,
        "Пул БД backend",
        12,
        26,
        [
            (f"sum(db_pool_checked_out{{{BACKEND}}})", "занято"),
            (f"sum(db_pool_max_connections{{{BACKEND}}})", "лимит"),
            (f"sum(http_requests_inprogress{{{BACKEND}}})", "запросов в работе"),
        ],
    ),
]

dashboard = {
    "annotations": {
        "list": [
            {
                "builtIn": 1,
                "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True,
                "hide": True,
                "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts",
                "type": "dashboard",
            }
        ]
    },
    "description": "Нагрузочный тест: k6 (remote write, scripts/loadtest/run.sh PROM_RW=1) и backend под нагрузкой",
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 1,
    "id": None,
    "links": [],
    "panels": panels,
    "refresh": "10s",
    "schemaVersion": 39,
    "tags": ["tg-jobs-parser", "loadtest", "k6"],
    "templating": {
        "list": [
            {
                "current": {},
                "datasource": DS,
                "definition": "label_values(k6_vus, testid)",
                "includeAll": True,
                "multi": True,
                "name": "testid",
                "label": "Прогон k6",
                "query": {"query": "label_values(k6_vus, testid)", "refId": "testid"},
                "refresh": 2,
                "sort": 2,
                "type": "query",
            },
            {
                "current": {"selected": True, "text": "tg-jobs-loadtest", "value": "tg-jobs-loadtest"},
                "name": "backend_job",
                "label": "Backend",
                "options": [
                    {"selected": True, "text": "tg-jobs-loadtest", "value": "tg-jobs-loadtest"},
                    {"selected": False, "text": "tg-jobs-backend", "value": "tg-jobs-backend"},
                ],
                "query": "tg-jobs-loadtest,tg-jobs-backend",
                "type": "custom",
            },
        ]
    },
    "time": {"from": "now-30m", "to": "now"},
    "timepicker": {},
    "timezone": "browser",
    "title": "k6 load test",
    "uid": "tgjp-k6",
    "version": 1,
}

OUT.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print(f"записан {OUT}")

"""Логи и метрики HTTP-клиента HH."""

from __future__ import annotations

import logging

import httpx
from prometheus_client import REGISTRY

from jobdesk.hh.client import HHClient, HHClientConfig
from jobdesk.hh.metrics import _endpoint_label


def _sample(name: str, labels: dict[str, str]) -> float:
    return float(REGISTRY.get_sample_value(name, labels) or 0.0)


def test_endpoint_label_collapses_vacancy_id():
    assert _endpoint_label("/vacancies/12345") == "/vacancies/:id"
    assert _endpoint_label("/vacancies") == "/vacancies"
    assert _endpoint_label("/vacancies/") == "/vacancies"


def test_request_logs_and_metrics(caplog, monkeypatch):
    cfg = HHClientConfig(api_token="tok", contact_email="dev@example.com")
    client = HHClient(cfg)

    def fake_http_request(method, url, **kwargs):
        req = httpx.Request(method, "https://api.hh.ru/vacancies")
        return httpx.Response(
            200,
            json={"found": 1, "pages": 1, "page": 0, "per_page": 1, "items": [{"id": "1"}]},
            request=req,
        )

    monkeypatch.setattr(client, "_http_request", fake_http_request)
    before = _sample(
        "hh_api_requests_total",
        {"method": "GET", "endpoint": "/vacancies", "status": "200"},
    )
    with caplog.at_level(logging.INFO, logger="jobdesk.hh.client"):
        payload = client.request("GET", "/vacancies", params={"text": "python"})
    client.close()

    assert payload["found"] == 1
    assert any("HH REQUEST GET /vacancies" in r.message for r in caplog.records)
    assert any("HH RESPONSE GET /vacancies → 200" in r.message for r in caplog.records)
    assert any("found=1" in r.message for r in caplog.records)
    assert (
        _sample(
            "hh_api_requests_total",
            {"method": "GET", "endpoint": "/vacancies", "status": "200"},
        )
        == before + 1
    )


def test_detail_response_preview(caplog, monkeypatch):
    cfg = HHClientConfig(api_token="tok", contact_email="dev@example.com")
    client = HHClient(cfg)

    def fake_http_request(method, url, **kwargs):
        req = httpx.Request(method, "https://api.hh.ru/vacancies/42")
        return httpx.Response(
            200,
            json={
                "id": "42",
                "name": "QA",
                "employer": {"name": "Acme"},
                "area": {"name": "Москва"},
                "description": "x" * 5000,
            },
            request=req,
        )

    monkeypatch.setattr(client, "_http_request", fake_http_request)
    with caplog.at_level(logging.INFO, logger="jobdesk.hh.client"):
        client.request("GET", "/vacancies/42")
    client.close()

    body_logs = [r.message for r in caplog.records if "HH RESPONSE" in r.message]
    assert body_logs
    assert "id='42'" in body_logs[0]
    assert "employer='Acme'" in body_logs[0]
    assert "x" * 100 not in body_logs[0]

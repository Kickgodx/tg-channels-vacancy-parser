from __future__ import annotations


def test_channels_search_endpoint_gone(api_client):
    resp = api_client.post("/api/v1/channels/search", json={"query": "python jobs"})
    assert resp.status_code == 404

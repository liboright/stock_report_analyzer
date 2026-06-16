"""健康检查 & settings 接口。"""
from __future__ import annotations


def test_health(client) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_settings_snapshot(client) -> None:
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.json()
    assert "anthropic_model" in body
    assert "mineru_key_set" in body
    # placeholder key 不算 set
    assert body["anthropic_key_set"] is False
    assert body["mineru_key_set"] is False

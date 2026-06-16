"""任务状态 & SSE 测试（M1 占位：仅返回 404 + 简单 health）。"""
from __future__ import annotations


def test_task_status_404(client) -> None:
    r = client.get("/tasks/9999")
    assert r.status_code == 404

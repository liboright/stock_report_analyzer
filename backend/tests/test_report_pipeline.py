"""M3 报告生成测试（mock subprocess，不真跑 claude CLI）。"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _wait_run_done(client, run_id: int, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        r = client.get(f"/tasks/{run_id}")
        if r.status_code != 200:
            time.sleep(0.3)
            continue
        body = r.json()
        last_status = body["status"]
        if last_status in {"done", "failed"}:
            return body
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} 在 {timeout}s 内未结束，最后 status={last_status}")


def _make_fake_proc(returncode: int = 0, stdout: str = "ok", stderr: str = ""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _make_stage1_output(tmp_path, company: str) -> Path:
    """在 REPORT_DATA_PATH/{company}/md/research_file/ 下写一个假的业务概况（按 docs/artifacts.md 规范）。"""
    from app.config import get_settings
    settings = get_settings()
    out = settings.REPORT_DATA_PATH / company / "md" / "research_file"
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{company}_业务概况.md"
    p.write_text(
        f"# {company} 业务概况（mock）\n\n## 公司业务\n主营 mock 业务。\n",
        encoding="utf-8",
    )
    return p


def test_generate_requires_company(client) -> None:
    r = client.post("/reports/generate", json={"company": "不存在的公司"})
    assert r.status_code == 404


def test_claude_skill_runner_builds_command() -> None:
    import shutil
    from app.services import claude_skill_runner
    from app.config import get_settings
    s = get_settings()
    cmd = claude_skill_runner._build_command(
        "stage1_business_understanding", "宁德时代", 2023,
        [Path(s.REPORT_DATA_PATH), Path(s.DEEP_RESEARCH_PATH)],
    )
    # cmd[0] 是 shutil.which 解析出的可执行路径（Windows 下是 .cmd 全路径）
    assert cmd[0] == shutil.which("claude")
    assert cmd[1] == "-p"
    prompt = cmd[2]
    assert prompt == "/stage1_business_understanding 宁德时代 2023"
    assert "--bare" in cmd
    # add-dir 都加进去了（REPORT_DATA_PATH + DEEP_RESEARCH_PATH，共 2 个）
    add_dir_count = sum(1 for x in cmd if x == "--add-dir")
    assert add_dir_count == 2


def test_claude_skill_runner_rejects_unknown_skill() -> None:
    from app.services import claude_skill_runner
    with pytest.raises(claude_skill_runner.ClaudeSkillError, match="不支持的 skill"):
        claude_skill_runner._build_command("stage99_doesnt_exist", "宁德时代", None, [])


def test_generate_full_flow_with_subprocess_mock(client, tmp_env) -> None:
    """端到端：建公司 → trigger generate → mock subprocess 落盘产物 → 验证。"""
    # 1) 建公司
    r = client.post("/companies", json={"name": "宁德时代"})
    assert r.status_code == 201

    # 2) 准备产物路径（subprocess mock 会"创建"它）
    expected_output = _make_stage1_output(tmp_env, "宁德时代")

    # 3) mock subprocess.run
    fake_proc = _make_fake_proc(returncode=0, stdout="done", stderr="")
    with patch("app.services.claude_skill_runner.subprocess.run", return_value=fake_proc) as mock_run:
        # 4) trigger generate
        r = client.post(
            "/reports/generate",
            json={"company": "宁德时代", "year": 2023, "skill": "stage1_business_understanding"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        run_id = body["run_id"]
        assert body["status"] == "queued"
        assert body["skill"] == "stage1_business_understanding"

        # 5) 等待完成
        time.sleep(0.5)
        final = _wait_run_done(client, run_id, timeout=30.0)
        assert final["status"] == "done", final
        assert final["error"] is None
        assert final["current_stage"] == 1

        # 6) 验证 subprocess.run 被调用，参数正确
        import shutil
        assert mock_run.called
        called_args = mock_run.call_args
        cmd = called_args.args[0]
        assert cmd[0] == shutil.which("claude")
        assert cmd[1] == "-p"
        assert cmd[2] == "/stage1_business_understanding 宁德时代 2023"
        assert "--bare" in cmd
        # env 注入了 ANTHROPIC_API_KEY（可能为空字符串，但 key 必须在 env dict 里）
        env = called_args.kwargs.get("env") or {}
        assert "ANTHROPIC_API_KEY" in env
        # timeout 设了
        assert called_args.kwargs.get("timeout") == 600

        # 7) 读最终报告内容
        r = client.get(f"/reports/{run_id}/content")
        assert r.status_code == 200, r.text
        content_body = r.json()
        assert "宁德时代 业务概况" in content_body["content"]
        assert "主营 mock 业务" in content_body["content"]


def test_generate_handles_subprocess_nonzero(client, tmp_env) -> None:
    """subprocess 退出码非 0 → run 标 failed。"""
    r = client.post("/companies", json={"name": "宁德时代"})
    assert r.status_code == 201

    # 产物不创建（模拟失败）
    fake_proc = _make_fake_proc(returncode=1, stdout="", stderr="some error")
    with patch("app.services.claude_skill_runner.subprocess.run", return_value=fake_proc):
        r = client.post("/reports/generate", json={"company": "宁德时代", "year": 2023})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        time.sleep(0.5)
        final = _wait_run_done(client, run_id, timeout=30.0)
        assert final["status"] == "failed"
        assert "退出码 1" in final["error"]


def test_generate_handles_missing_output(client, tmp_env) -> None:
    """subprocess 成功（returncode 0）但产物缺失 → 标 failed。"""
    r = client.post("/companies", json={"name": "宁德时代"})
    assert r.status_code == 201

    # subprocess 成功但产物不创建
    fake_proc = _make_fake_proc(returncode=0, stdout="ok", stderr="")
    with patch("app.services.claude_skill_runner.subprocess.run", return_value=fake_proc):
        r = client.post("/reports/generate", json={"company": "宁德时代", "year": 2023})
        run_id = r.json()["run_id"]
        time.sleep(0.5)
        final = _wait_run_done(client, run_id, timeout=30.0)
        assert final["status"] == "failed"
        assert "产物缺失" in final["error"]


def test_get_report_content_409_when_not_done(client) -> None:
    r = client.post("/companies", json={"name": "宁德时代"})
    assert r.status_code == 201
    r = client.post("/reports/generate", json={"company": "宁德时代", "year": 2023})
    run_id = r.json()["run_id"]
    # 立即读 content（status=queued）
    r = client.get(f"/reports/{run_id}/content")
    assert r.status_code == 409
    assert "未完成" in r.json()["detail"]


def test_get_report_content_404(client) -> None:
    r = client.get("/reports/9999/content")
    assert r.status_code == 404

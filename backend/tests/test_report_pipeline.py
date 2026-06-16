"""M3 报告生成测试（mock subprocess，不真跑 claude CLI）。"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import List
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


# ============================================================
# Popen mock 工厂
# ============================================================


class _FakePopen:
    """模拟 ``subprocess.Popen``：可配置 stdout lines、returncode、超时。"""

    def __init__(
        self,
        cmd,
        *,
        stdout_lines: List[str] | None = None,
        returncode: int = 0,
        **kwargs,
    ):
        self.cmd = cmd
        self._stdout_lines = list(stdout_lines or [])
        self.returncode = returncode
        self.killed = False

    @property
    def stdout(self):
        return self._stdout_lines  # runner 用 ``for line in proc.stdout`` 迭代

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1


def _make_fake_popen(returncode: int = 0, stdout_lines: List[str] | None = None):
    """构造 Popen mock 工厂；同时返回 captured 列表（让调用方拿 cmd）。"""
    captured: List[dict] = []

    def factory(cmd, **kwargs):
        captured.append({"cmd": cmd, "kwargs": kwargs})
        return _FakePopen(cmd, stdout_lines=stdout_lines, returncode=returncode)

    return factory, captured


# ============================================================
# 路径工具
# ============================================================


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
    # 方案 B（2026-06-16）：stage1 要求双产物（业务概况 + 行业分析），一并 mock 落盘。
    industry_p = out / f"{company}_行业分析.md"
    industry_p.write_text(
        f"# {company} 行业分析（mock）\n\n## 行业\nmock 行业分析。\n",
        encoding="utf-8",
    )
    return p


# ============================================================
# 1. 路由 / 输入校验
# ============================================================


def test_generate_requires_company(client) -> None:
    r = client.post("/reports/generate", json={"company": "不存在的公司"})
    assert r.status_code == 404


# ============================================================
# 2. _build_command 单元测试（适配 years 数组签名）
# ============================================================


def test_claude_skill_runner_builds_command_single_year() -> None:
    """单年份 → prompt 仍可工作（向后兼容）。"""
    import shutil
    from app.services import claude_skill_runner
    from app.config import get_settings
    s = get_settings()
    cmd = claude_skill_runner._build_command(
        "stage1_business_understanding", "宁德时代", [2023],
        [Path(s.REPORT_DATA_PATH), Path(s.DEEP_RESEARCH_PATH)],
    )
    # cmd[0] 是 shutil.which 解析出的可执行路径（Windows 下是 .cmd 全路径）
    assert cmd[0] == shutil.which("claude")
    assert cmd[1] == "-p"
    prompt = cmd[2]
    assert prompt == "/stage1_business_understanding 宁德时代 2023"
    assert "--bare" in cmd
    # 流式输出
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    # add-dir 都加进去了（REPORT_DATA_PATH + DEEP_RESEARCH_PATH，共 2 个）
    add_dir_count = sum(1 for x in cmd if x == "--add-dir")
    assert add_dir_count == 2


def test_claude_skill_runner_builds_command_multi_years() -> None:
    """多年份 → prompt 用逗号分隔。"""
    from app.services import claude_skill_runner
    from app.config import get_settings
    s = get_settings()
    cmd = claude_skill_runner._build_command(
        "stage1_business_understanding", "贵州茅台", [2022, 2023, 2024],
        [Path(s.REPORT_DATA_PATH)],
    )
    prompt = cmd[2]
    assert prompt == "/stage1_business_understanding 贵州茅台 2022,2023,2024"


def test_claude_skill_runner_builds_command_no_years() -> None:
    """空 years → prompt 不包含年份段。"""
    from app.services import claude_skill_runner
    cmd = claude_skill_runner._build_command(
        "stage1_business_understanding", "X", [], [],
    )
    prompt = cmd[2]
    assert prompt == "/stage1_business_understanding X"


def test_claude_skill_runner_rejects_unknown_skill() -> None:
    from app.services import claude_skill_runner
    with pytest.raises(claude_skill_runner.ClaudeSkillError, match="不支持的 skill"):
        claude_skill_runner._build_command("stage99_doesnt_exist", "宁德时代", [], [])


# ============================================================
# 3. 端到端：单年份（向后兼容）
# ============================================================


def test_generate_full_flow_with_subprocess_mock(client, tmp_env) -> None:
    """端到端：建公司 → trigger generate (单 year) → mock Popen 落产物 → 验证。"""
    # 1) 建公司
    r = client.post("/companies", json={"name": "宁德时代"})
    assert r.status_code == 201

    # 2) 准备产物路径
    expected_output = _make_stage1_output(tmp_env, "宁德时代")

    # 3) mock Popen
    factory, captured = _make_fake_popen(returncode=0, stdout_lines=[
        '{"type":"result","subtype":"success","result":"ok"}\n'
    ])
    with patch("app.services.claude_skill_runner.subprocess.Popen", side_effect=factory):
        # 4) trigger generate（老 payload：单 year）
        r = client.post(
            "/reports/generate",
            json={"company": "宁德时代", "year": 2023, "skill": "stage1_business_understanding"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        run_id = body["run_id"]
        assert body["status"] == "queued"
        assert body["skill"] == "stage1_business_understanding"
        # 响应里也包含 years（years=[2023]）
        assert body["years"] == [2023]

        # 5) 等待完成
        time.sleep(0.5)
        final = _wait_run_done(client, run_id, timeout=30.0)
        assert final["status"] == "done", final
        assert final["error"] is None
        assert final["current_stage"] == 1

        # 6) 验证 Popen 被调用，参数正确
        import shutil
        assert len(captured) == 1
        call = captured[0]
        cmd = call["cmd"]
        assert cmd[0] == shutil.which("claude")
        assert cmd[1] == "-p"
        assert cmd[2] == "/stage1_business_understanding 宁德时代 2023"
        assert "--bare" in cmd
        # env 注入了 ANTHROPIC_API_KEY
        env = call["kwargs"].get("env") or {}
        assert "ANTHROPIC_API_KEY" in env

        # 7) 读最终报告内容
        r = client.get(f"/reports/{run_id}/content")
        assert r.status_code == 200, r.text
        content_body = r.json()
        assert "宁德时代 业务概况" in content_body["content"]
        assert "主营 mock 业务" in content_body["content"]


# ============================================================
# 4. 端到端：多年份（新功能）
# ============================================================


def test_generate_full_flow_multi_years(client, tmp_env) -> None:
    """多年份 payload：years=[2022,2023,2024] → prompt 含逗号串 → 产物落盘 → done。"""
    r = client.post("/companies", json={"name": "贵州茅台"})
    assert r.status_code == 201

    _make_stage1_output(tmp_env, "贵州茅台")

    factory, captured = _make_fake_popen(returncode=0)
    with patch("app.services.claude_skill_runner.subprocess.Popen", side_effect=factory):
        r = client.post(
            "/reports/generate",
            json={
                "company": "贵州茅台",
                "years": [2022, 2023, 2024],
                "skill": "stage1_business_understanding",
            },
        )
        assert r.status_code == 202, r.text
        body = r.json()
        run_id = body["run_id"]
        assert body["years"] == [2022, 2023, 2024]

        time.sleep(0.5)
        final = _wait_run_done(client, run_id, timeout=30.0)
        assert final["status"] == "done", final

        # cmd 中含多年份
        assert len(captured) == 1
        prompt = captured[0]["cmd"][2]
        assert prompt == "/stage1_business_understanding 贵州茅台 2022,2023,2024"


# ============================================================
# 5. 失败路径
# ============================================================


def test_generate_handles_subprocess_nonzero(client, tmp_env) -> None:
    """subprocess 退出码非 0 → run 标 failed。"""
    r = client.post("/companies", json={"name": "宁德时代"})
    assert r.status_code == 201

    # 产物不创建（模拟失败）
    factory, _ = _make_fake_popen(returncode=1, stdout_lines=["boom\n"])
    with patch("app.services.claude_skill_runner.subprocess.Popen", side_effect=factory):
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
    factory, _ = _make_fake_popen(returncode=0)
    with patch("app.services.claude_skill_runner.subprocess.Popen", side_effect=factory):
        r = client.post("/reports/generate", json={"company": "宁德时代", "year": 2023})
        run_id = r.json()["run_id"]
        time.sleep(0.5)
        final = _wait_run_done(client, run_id, timeout=30.0)
        assert final["status"] == "failed"
        assert "产物缺失" in final["error"]


# ============================================================
# 6. content 端点
# ============================================================


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

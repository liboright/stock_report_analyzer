"""``claude_skill_runner`` stage2_table_merge 相关单元测试。

策略：
- 不真调 claude CLI（subprocess → LLM → 慢且依赖外部）；用 monkeypatch 替换
  ``subprocess.Popen`` 验证：
  - 命令行构造正确（白名单 / 路径 / --add-dir）
  - 成功路径：返回 SkillRunResult.output_paths = [long, wide]
  - 失败路径：非 0 退出码、产物缺失、超时 → ClaudeSkillError
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

import pytest

from app.config import Settings
from app.services import claude_skill_runner as runner


# ============================================================
# 1. 白名单
# ============================================================


def test_supported_skills_includes_stage2():
    assert "stage2_table_merge" in runner.SUPPORTED_SKILLS
    # stage1 必须保留
    assert "stage1_business_understanding" in runner.SUPPORTED_SKILLS


# ============================================================
# 2. _build_command_table_merge
# ============================================================


def test_build_command_table_merge_basic():
    cmd = runner._build_command_table_merge(
        skill="stage2_table_merge",
        company="贵州茅台",
        group_key="05_五|营业收入",
        years=[2023, 2024, 2025],
        csv_paths=[
            "贵州茅台/md/clean/贵州茅台2023年年报/table/05_五/营业收入.csv",
            "贵州茅台/md/clean/贵州茅台2024年年报/table/05_五/营业收入.csv",
        ],
        add_dirs=[Path("D:/quant/report_data"), Path("D:/quant/deep-research-report")],
    )
    # claude 路径（resolves via shutil.which，无环境可能为空字符串但列表非空）
    assert cmd[0].endswith("claude") or "claude" in cmd[0]
    assert cmd[1] == "-p"
    prompt = cmd[2]
    assert prompt.startswith("/stage2_table_merge")
    assert "贵州茅台" in prompt
    assert "05_五|营业收入" in prompt
    assert "2023,2024,2025" in prompt
    assert "营业收入.csv" in prompt
    # 流式输出 + bare 模式
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "--verbose" in cmd
    assert "--bare" in cmd
    # --add-dir 出现 2 次（REPORT_DATA_PATH + DEEP_RESEARCH_PATH）
    assert cmd.count("--add-dir") == 2


def test_build_command_table_merge_rejects_unknown_skill():
    with pytest.raises(runner.ClaudeSkillError, match="不支持的 skill"):
        runner._build_command_table_merge(
            skill="not_in_whitelist",
            company="X",
            group_key="s|t",
            years=[2024],
            csv_paths=["a.csv"],
            add_dirs=[],
        )


def test_build_command_table_merge_rejects_empty_years():
    with pytest.raises(runner.ClaudeSkillError, match="至少需要 1 个年份"):
        runner._build_command_table_merge(
            skill="stage2_table_merge",
            company="X",
            group_key="s|t",
            years=[],
            csv_paths=["a.csv"],
            add_dirs=[],
        )


def test_build_command_table_merge_rejects_empty_csvs():
    with pytest.raises(runner.ClaudeSkillError, match="至少需要 1 个 CSV"):
        runner._build_command_table_merge(
            skill="stage2_table_merge",
            company="X",
            group_key="s|t",
            years=[2024],
            csv_paths=[],
            add_dirs=[],
        )


# ============================================================
# 3. _expected_table_merge_outputs
# ============================================================


def test_expected_table_merge_outputs_paths():
    s = Settings(REPORT_DATA_PATH=Path("D:/quant/report_data"))
    long_p, wide_p = runner._expected_table_merge_outputs(
        company="贵州茅台", group_key="05_五|营业收入", settings=s
    )
    assert long_p.name == "05_五_营业收入_long.csv"
    assert wide_p.name == "05_五_营业收入_wide.csv"
    assert long_p.parent == wide_p.parent
    # Windows 下 Path 的 str 用反斜杠，用 as_posix 检查
    assert "research_file/table" in long_p.as_posix()


def test_expected_table_merge_outputs_handles_no_pipe():
    s = Settings(REPORT_DATA_PATH=Path("D:/quant/report_data"))
    long_p, wide_p = runner._expected_table_merge_outputs(
        company="X", group_key="only_title", settings=s
    )
    # 没 | 时 stem=""，sanitize 兜底为 "未命名"
    assert long_p.name == "未命名_only_title_long.csv"


# ============================================================
# 4. _sanitize_for_filename
# ============================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("营业收入", "营业收入"),
        ("05_五|营业收入", "05_五_营业收入"),
        ("a/b\\c:d*e?f\"g<h>i|j", "a_b_c_d_e_f_g_h_i_j"),
        ("  空 格  ", "空_格"),
        ("", "未命名"),
        ("///", "未命名"),
    ],
)
def test_sanitize_for_filename(raw, expected):
    assert runner._sanitize_for_filename(raw) == expected


# ============================================================
# 5. Popen mock 工厂（适配新流式实现）
# ============================================================


class _FakePopen:
    """模拟 ``subprocess.Popen``：可配置 stdout lines、returncode、超时。"""

    def __init__(
        self,
        cmd,
        *,
        stdout_lines: List[str] | None = None,
        returncode: int = 0,
        raise_timeout: bool = False,
        **kwargs,
    ):
        self.cmd = cmd
        self._stdout_lines = stdout_lines or []
        self._stdout_iter = iter(self._stdout_lines)
        self.returncode = returncode
        self._raise_timeout = raise_timeout
        self.stdout = self  # runner 期望 proc.stdout 可迭代

    def __iter__(self):
        return self._stdout_iter

    def __next__(self):
        return next(self._stdout_iter)

    def wait(self, timeout=None):
        if self._raise_timeout:
            raise subprocess.TimeoutExpired(cmd=self.cmd, timeout=timeout or 10)
        return self.returncode

    def kill(self):
        self.returncode = -1


def _patch_popen(monkeypatch, **popen_kwargs):
    """monkeypatch ``runner.subprocess.Popen``，返回 (factory, captured_cmds) 元组。"""
    captured: List[list] = []

    def factory(cmd, **kwargs):
        captured.append(cmd)
        return _FakePopen(cmd, **popen_kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", factory)
    return factory, captured


# ============================================================
# 6. run_skill_for_table_merge — 成功路径（mock Popen）
# ============================================================


def _make_outputs(base: Path, company: str, group_key: str):
    """建好 long+wide 两个空文件（模拟 skill 已落盘）。"""
    long_p, wide_p = runner._expected_table_merge_outputs(
        company=company, group_key=group_key,
        settings=Settings(REPORT_DATA_PATH=base),
    )
    long_p.parent.mkdir(parents=True, exist_ok=True)
    long_p.write_text("# meta\n项目\n营收\n", encoding="utf-8-sig")
    wide_p.write_text("# meta\nsubject,金额_2025\n营收,1000\n", encoding="utf-8-sig")
    return long_p, wide_p


def test_run_skill_for_table_merge_success(monkeypatch, tmp_path: Path):
    long_p, wide_p = _make_outputs(tmp_path, "贵州茅台", "05_五|营业收入")

    s = Settings(REPORT_DATA_PATH=tmp_path)
    monkeypatch.setattr(runner, "get_settings", lambda: s)

    # 模拟 claude CLI 成功 + 输出 1 条 result 事件
    ndjson = (
        '{"type":"result","subtype":"success",'
        '"result":"ok","duration_ms":1200}\n'
    )
    _patch_popen(monkeypatch, stdout_lines=[ndjson], returncode=0)

    result = runner.run_skill_for_table_merge(
        skill="stage2_table_merge",
        company="贵州茅台",
        group_key="05_五|营业收入",
        years=[2023, 2024, 2025],
        csv_paths=[
            "贵州茅台/md/clean/贵州茅台2023年年报/table/05_五/营业收入.csv",
            "贵州茅台/md/clean/贵州茅台2024年年报/table/05_五/营业收入.csv",
            "贵州茅台/md/clean/贵州茅台2025年年报/table/05_五/营业收入.csv",
        ],
    )

    assert result.returncode == 0
    assert result.skill == "stage2_table_merge"
    assert result.output_path == long_p
    assert result.output_paths == [long_p, wide_p]
    assert result.company == "贵州茅台"
    assert result.year is None
    assert result.elapsed_seconds >= 0
    # years 透传
    assert result.years == [2023, 2024, 2025]


def test_run_skill_for_table_merge_nonzero_returncode(monkeypatch, tmp_path: Path):
    s = Settings(REPORT_DATA_PATH=tmp_path)
    monkeypatch.setattr(runner, "get_settings", lambda: s)

    _patch_popen(monkeypatch, returncode=1)

    with pytest.raises(runner.ClaudeSkillError, match="退出码 1"):
        runner.run_skill_for_table_merge(
            skill="stage2_table_merge",
            company="X",
            group_key="s|t",
            years=[2024],
            csv_paths=["x.csv"],
        )


def test_run_skill_for_table_merge_missing_outputs(monkeypatch, tmp_path: Path):
    """subprocess 成功但产物文件没落盘 → ClaudeSkillError。"""
    s = Settings(REPORT_DATA_PATH=tmp_path)
    monkeypatch.setattr(runner, "get_settings", lambda: s)

    _patch_popen(monkeypatch, returncode=0)

    with pytest.raises(runner.ClaudeSkillError, match="产物缺失"):
        runner.run_skill_for_table_merge(
            skill="stage2_table_merge",
            company="X",
            group_key="s|t",
            years=[2024],
            csv_paths=["x.csv"],
        )


def test_run_skill_for_table_merge_only_long_missing(monkeypatch, tmp_path: Path):
    """只写 wide 不写 long → 仍报产物缺失。"""
    _, wide_p = _make_outputs(tmp_path, "X", "s|t")
    # 把 wide 留下，long 删掉
    long_p = wide_p.parent / "s_t_long.csv"
    long_p.unlink(missing_ok=True)

    s = Settings(REPORT_DATA_PATH=tmp_path)
    monkeypatch.setattr(runner, "get_settings", lambda: s)

    _patch_popen(monkeypatch, returncode=0)

    with pytest.raises(runner.ClaudeSkillError, match="long=False"):
        runner.run_skill_for_table_merge(
            skill="stage2_table_merge",
            company="X",
            group_key="s|t",
            years=[2024],
            csv_paths=["x.csv"],
        )


def test_run_skill_for_table_merge_timeout(monkeypatch, tmp_path: Path):
    s = Settings(REPORT_DATA_PATH=tmp_path)
    monkeypatch.setattr(runner, "get_settings", lambda: s)

    _patch_popen(monkeypatch, raise_timeout=True)

    with pytest.raises(runner.ClaudeSkillError, match="超时"):
        runner.run_skill_for_table_merge(
            skill="stage2_table_merge",
            company="X",
            group_key="s|t",
            years=[2024],
            csv_paths=["x.csv"],
            timeout_seconds=10,
        )


def test_run_skill_for_table_merge_missing_claude_cli(monkeypatch, tmp_path: Path):
    """PATH 没 claude → ClaudeSkillError。"""
    s = Settings(REPORT_DATA_PATH=tmp_path)
    monkeypatch.setattr(runner, "get_settings", lambda: s)
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)

    with pytest.raises(runner.ClaudeSkillError, match="未找到 claude CLI"):
        runner.run_skill_for_table_merge(
            skill="stage2_table_merge",
            company="X",
            group_key="s|t",
            years=[2024],
            csv_paths=["x.csv"],
        )


# ============================================================
# 7. 旧 run_skill 兼容性（不破坏 stage1）
# ============================================================


def test_run_skill_rejects_stage2_via_old_entry(monkeypatch, tmp_path: Path):
    """旧 run_skill 不支持 stage2（_expected_output 拒绝非 stage1 skill）。

    这是有意为之：stage2 必须走 run_skill_for_table_merge（多产物 + 多参数）。
    """
    s = Settings(REPORT_DATA_PATH=tmp_path)
    monkeypatch.setattr(runner, "get_settings", lambda: s)

    _patch_popen(monkeypatch, returncode=0)

    with pytest.raises(runner.ClaudeSkillError, match="未知 skill"):
        runner.run_skill(
            skill="stage2_table_merge",
            company="贵州茅台",
        )


# ============================================================
# 8. 新增：多年份 stage1 支持
# ============================================================


def test_run_skill_uses_years_csv_in_prompt(monkeypatch, tmp_path: Path):
    """stage1 传多年份时，prompt 应包含逗号分隔的年份串。"""
    s = Settings(REPORT_DATA_PATH=tmp_path)
    monkeypatch.setattr(runner, "get_settings", lambda: s)

    factory, captured = _patch_popen(monkeypatch, returncode=1)

    with pytest.raises(runner.ClaudeSkillError):
        runner.run_skill(
            skill="stage1_business_understanding",
            company="宁德时代",
            years=[2023, 2024, 2025],
        )

    assert len(captured) == 1
    cmd = captured[0]
    prompt = cmd[2]
    assert prompt == "/stage1_business_understanding 宁德时代 2023,2024,2025"


def test_run_skill_falls_back_to_year(monkeypatch, tmp_path: Path):
    """只传 year（无 years）时，prompt 应包含单一年份（向后兼容）。"""
    s = Settings(REPORT_DATA_PATH=tmp_path)
    monkeypatch.setattr(runner, "get_settings", lambda: s)

    factory, captured = _patch_popen(monkeypatch, returncode=1)

    with pytest.raises(runner.ClaudeSkillError):
        runner.run_skill(
            skill="stage1_business_understanding",
            company="X",
            year=2024,
        )

    assert len(captured) == 1
    prompt = captured[0][2]
    assert prompt == "/stage1_business_understanding X 2024"


def test_run_skill_years_in_result(monkeypatch, tmp_path: Path):
    """成功后 SkillRunResult.years 应反映传入的年份列表。"""
    # 建好双产物（业务概况 + 行业分析，方案 B 后 stage1 跑通要求双产物）
    s_settings = Settings(REPORT_DATA_PATH=tmp_path)
    company = "宁德时代"
    out_dir = s_settings.REPORT_DATA_PATH / company / "md" / "research_file"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{company}_业务概况.md").write_text("# 业务概况\n", encoding="utf-8")
    (out_dir / f"{company}_行业分析.md").write_text("# 行业分析\n", encoding="utf-8")

    monkeypatch.setattr(runner, "get_settings", lambda: s_settings)

    ndjson = (
        '{"type":"assistant","message":{"content":['
        '{"type":"thinking","thinking":"分析中..."},'
        '{"type":"tool_use","name":"Read","input":{"file_path":"x"}}'
        ']}}\n'
        '{"type":"result","subtype":"success","result":"ok"}\n'
    )
    _patch_popen(monkeypatch, stdout_lines=[ndjson], returncode=0)

    result = runner.run_skill(
        skill="stage1_business_understanding",
        company=company,
        years=[2022, 2023, 2024],
        run_id=None,  # 不推 SSE
    )

    assert result.years == [2022, 2023, 2024]
    # year 字段（向后兼容）取 years[0]
    assert result.year == 2022


# ============================================================
# 9. 新增：流式 JSON 事件解析
# ============================================================


def test_process_stream_event_thinking(monkeypatch):
    """thinking 事件应被解析成 phase=thinking / kind=thinking 的 payload。"""
    captured: list[dict] = []

    def fake_publish(run_id, message, level="info", stage=None, payload=None):
        # phase 塞在 payload 顶层
        captured.append({
            "run_id": run_id, "phase": (payload or {}).get("phase"),
            "message": message, "level": level, "payload": payload,
        })

    monkeypatch.setattr(runner.progress_bus, "publish", fake_publish)

    line = (
        '{"type":"assistant","message":{"content":['
        '{"type":"thinking","thinking":"我需要分析业务结构"}'
        ']}}\n'
    )
    runner._process_stream_event(42, line)

    assert len(captured) == 1
    ev = captured[0]
    assert ev["run_id"] == 42
    assert ev["phase"] == "thinking"
    assert "分析业务结构" in ev["message"]
    assert ev["payload"]["kind"] == "thinking"
    assert "分析业务结构" in ev["payload"]["full"]


def test_process_stream_event_tool_use_and_result(monkeypatch):
    """tool_use 紧跟 tool_result 应被分别推两条 SSE 事件。"""
    captured: list[dict] = []

    def fake_publish(run_id, message, level="info", stage=None, payload=None):
        captured.append({
            "run_id": run_id, "phase": (payload or {}).get("phase"),
            "message": message, "level": level, "payload": payload,
        })

    monkeypatch.setattr(runner.progress_bus, "publish", fake_publish)

    use_line = (
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"/tmp/x"}}'
        ']}}\n'
    )
    result_line = (
        '{"type":"user","message":{"content":['
        '{"type":"tool_result","tool_use_id":"t1","content":[{"type":"text","text":"file content"}]}'
        ']}}\n'
    )

    runner._process_stream_event(7, use_line)
    runner._process_stream_event(7, result_line)

    assert len(captured) == 2
    assert captured[0]["phase"] == "tool_use"
    assert captured[0]["payload"]["tool_name"] == "Read"
    assert captured[0]["payload"]["input"]["file_path"] == "/tmp/x"
    assert "Read" in captured[0]["message"]

    assert captured[1]["phase"] == "tool_result"
    assert captured[1]["payload"]["tool_use_id"] == "t1"
    assert "file content" in captured[1]["message"]


def test_process_stream_event_invalid_json(monkeypatch):
    """非 JSON 行应被静默忽略，不推 SSE。"""
    captured: list[dict] = []

    def fake_publish(*args, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(runner.progress_bus, "publish", fake_publish)

    runner._process_stream_event(1, "not json\n")
    runner._process_stream_event(1, "{ broken json\n")
    runner._process_stream_event(1, "")

    assert captured == []


def test_process_stream_event_result_error(monkeypatch):
    """subtype=error_max_turns 应被识别为错误事件。"""
    captured: list[dict] = []

    def fake_publish(run_id, message, level="info", stage=None, payload=None):
        captured.append({
            "run_id": run_id, "phase": (payload or {}).get("phase"),
            "message": message, "level": level, "payload": payload,
        })

    monkeypatch.setattr(runner.progress_bus, "publish", fake_publish)

    line = '{"type":"result","subtype":"error_max_turns","result":"turn limit"}\n'
    runner._process_stream_event(99, line)

    assert len(captured) == 1
    ev = captured[0]
    assert ev["phase"] == "result"
    assert ev["level"] == "error"
    assert ev["payload"]["is_error"] is True

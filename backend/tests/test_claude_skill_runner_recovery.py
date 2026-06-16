"""``claude_skill_runner`` 方案 B 兜底解析单元测试（2026-06-16）。

场景：
- SubAgent 长流程里 M3 sandbox 拦截了所有文件写入，agent 把内容贴在
  result.result 的 markdown 代码块里返回。
- ``run_skill`` 末尾检测产物缺失 → 从 ``result.result`` 提取代码块 → 自动落盘。

测试目标：
1. ``_extract_markdown_blocks`` 正向：提取 2 个 ```markdown``` 块
2. ``_extract_markdown_blocks`` 边界：空文本/无代码块/代码块不带 markdown tag
3. ``_dispatch_markdown_blocks`` 正向：按标题分发到业务概况/行业分析
4. ``_dispatch_markdown_blocks`` 边界：缺一个/全部缺失/标题乱序
5. ``run_skill`` 端到端：mock Popen 输出 result 事件含 2 个代码块 → 产物落盘成功
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterator, List
from unittest.mock import MagicMock, patch

import pytest

from app.services import claude_skill_runner as runner


# ============================================================
# 1. _extract_markdown_blocks
# ============================================================


def test_extract_markdown_blocks_basic_two_blocks():
    text = (
        "前置说明文字\n"
        "```markdown\n"
        "# 贵州茅台公司业务概况\n"
        "## 一、基本信息\n"
        "- 公司名：贵州茅台\n"
        "```\n"
        "中间说明\n"
        "```markdown\n"
        "# 贵州茅台 行业分析\n"
        "## 一、政策\n"
        "- 国务院政策\n"
        "```\n"
        "末尾说明"
    )
    blocks = runner._extract_markdown_blocks(text)
    assert len(blocks) == 2
    titles = [t for t, _ in blocks]
    bodies = [b for _, b in blocks]
    assert "业务概况" in titles[0]
    assert "行业分析" in titles[1]
    assert "公司名：贵州茅台" in bodies[0]
    assert "国务院政策" in bodies[1]


def test_extract_markdown_blocks_md_tag_alias():
    """``md`` 标签也应被识别。"""
    text = "```md\n# 标题\n内容\n```"
    blocks = runner._extract_markdown_blocks(text)
    assert len(blocks) == 1
    assert blocks[0][0] == "标题"


def test_extract_markdown_blocks_no_blocks_returns_empty():
    assert runner._extract_markdown_blocks("") == []
    assert runner._extract_markdown_blocks("hello world") == []
    # 无 markdown tag 的代码块（如 ```python）也应忽略
    assert runner._extract_markdown_blocks("```python\nprint('x')\n```") == []


def test_extract_markdown_blocks_multiline_body():
    body = "第一行\n第二行\n第三行\n  - 缩进列表\n  - 项2"
    text = f"```markdown\n# Title\n{body}\n```"
    blocks = runner._extract_markdown_blocks(text)
    assert len(blocks) == 1
    assert blocks[0][1].strip().endswith("项2")


# ============================================================
# 2. _dispatch_markdown_blocks
# ============================================================


def _sample_blocks() -> List[tuple[str, str]]:
    return [
        ("贵州茅台公司业务概况", "## 一、公司基本信息\n- 公司名：贵州茅台\n"),
        ("贵州茅台 行业分析", "## 一、行业政策\n- 国务院政策\n"),
    ]


def test_dispatch_markdown_blocks_basic(tmp_path: Path):
    blocks = _sample_blocks()
    expected = [tmp_path / "贵州茅台_业务概况.md", tmp_path / "贵州茅台_行业分析.md"]
    written = runner._dispatch_markdown_blocks(blocks, expected)
    assert written == expected
    assert expected[0].exists()
    assert expected[1].exists()
    assert "贵州茅台" in expected[0].read_text(encoding="utf-8")
    assert "国务院" in expected[1].read_text(encoding="utf-8")


def test_dispatch_markdown_blocks_creates_parent_dir(tmp_path: Path):
    blocks = _sample_blocks()
    nested_dir = tmp_path / "deep" / "nested" / "dir"
    expected = [nested_dir / "贵州茅台_业务概况.md", nested_dir / "贵州茅台_行业分析.md"]
    # 父目录不存在，dispatch 应自动创建
    assert not nested_dir.exists()
    written = runner._dispatch_markdown_blocks(blocks, expected)
    assert written == expected
    assert nested_dir.is_dir()


def test_dispatch_markdown_blocks_unmatched_path_returns_empty(tmp_path: Path):
    """文件名不含「业务概况」/「行业分析」时跳过（不应该硬塞数据）。"""
    blocks = _sample_blocks()
    unrelated = [tmp_path / "report.md"]
    written = runner._dispatch_markdown_blocks(blocks, unrelated)
    assert written == []


def test_dispatch_markdown_blocks_each_block_used_once(tmp_path: Path):
    """同一代码块不能被两个文件复用。"""
    blocks = _sample_blocks()
    expected = [
        tmp_path / "贵州茅台_业务概况.md",
        tmp_path / "贵州茅台_业务概况_v2.md",  # 重复文件
        tmp_path / "贵州茅台_行业分析.md",
    ]
    written = runner._dispatch_markdown_blocks(blocks, expected)
    # 业务概况只有一个代码块，只落盘一个文件
    business_written = [p for p in written if "业务概况" in p.name]
    assert len(business_written) == 1
    # 行业分析正常落盘
    assert any("行业分析" in p.name for p in written)


# ============================================================
# 3. run_skill 端到端（mock Popen）
# ============================================================


def _make_ndjson_result_event(result_text: str) -> str:
    """构造一条 NDJSON 格式的 result 事件。"""
    ev = {
        "type": "result",
        "subtype": "success",
        "result": result_text,
        "duration_ms": 1000,
    }
    return json.dumps(ev, ensure_ascii=False)


def _make_mock_popen(stdout_lines: List[str], returncode: int = 0) -> MagicMock:
    """构造 mock Popen 对象。"""

    def _iter_lines() -> Iterator[str]:
        for line in stdout_lines:
            yield line

    proc = MagicMock()
    proc.stdout = _iter_lines()
    proc.wait.return_value = None
    proc.returncode = returncode
    proc.kill.return_value = None
    return proc


def test_run_skill_recovers_when_subagent_cannot_write_files(tmp_path: Path, monkeypatch):
    """方案 B 核心场景：产物缺失时从 stdout 兜底解析并落盘。

    模拟：
    - claude CLI 跑成功（returncode=0）
    - stdout 末尾 result.result 含 2 个 markdown 代码块（业务概况 + 行业分析）
    - 文件系统上**没有**产物（SubAgent 写文件被 sandbox 拦截）
    期望：
    - run_skill 检测 missing → 从 result.result 提取 → 自动落盘
    - 不抛 ClaudeSkillError
    - SkillRunResult.output_paths 都存在
    """
    # 用临时目录作为 REPORT_DATA_PATH，避免污染真实数据
    company = "测试公司A"
    expected_business = tmp_path / company / "md" / "research_file" / f"{company}_业务概况.md"
    expected_industry = tmp_path / company / "md" / "research_file" / f"{company}_行业分析.md"

    # SubAgent 输出的 result.result（模拟产物贴在 result 里）
    result_text = (
        "由于 sandbox 拦截，写文件失败。文档如下：\n"
        "```markdown\n"
        f"# {company}公司业务概况\n"
        "## 一、基本信息\n"
        f"- 公司名：{company}\n"
        "```\n"
        "```markdown\n"
        f"# {company} 行业分析\n"
        "## 一、政策\n"
        "- 国务院政策\n"
        "```\n"
    )
    result_event_json = _make_ndjson_result_event(result_text)

    stdout_lines = [
        # 模拟一些 stream-json 事件
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}}),
        result_event_json,
    ]

    mock_popen = _make_mock_popen(stdout_lines, returncode=0)

    # mock settings：把 REPORT_DATA_PATH 指到 tmp_path
    fake_settings = MagicMock()
    fake_settings.REPORT_DATA_PATH = tmp_path
    fake_settings.DEEP_RESEARCH_PATH = tmp_path / "deep-research"
    fake_settings.ANTHROPIC_API_KEY = ""
    fake_settings.ANTHROPIC_MODEL = ""
    monkeypatch.setattr(runner, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_popen)

    # 跑 run_skill（不传 run_id，避开 SSE publish 复杂性）
    result = runner.run_skill(
        skill="stage1_business_understanding",
        company=company,
        years=[2023, 2024, 2025],
        timeout_seconds=60,
        run_id=None,
    )

    # 断言：兜底落盘成功
    assert expected_business.exists(), f"业务概况未落盘: {expected_business}"
    assert expected_industry.exists(), f"行业分析未落盘: {expected_industry}"
    assert "公司名：测试公司A" in expected_business.read_text(encoding="utf-8")
    assert "国务院政策" in expected_industry.read_text(encoding="utf-8")
    assert result.returncode == 0
    assert expected_business in result.output_paths
    assert expected_industry in result.output_paths


def test_run_skill_still_raises_when_no_markdown_blocks_in_result(tmp_path: Path, monkeypatch):
    """result.result 里**没有** markdown 代码块 + 产物确实缺失 → 仍应抛 ClaudeSkillError。"""
    company = "测试公司B"
    # 不构造任何 markdown 代码块
    result_text = "任务失败，无产物"
    result_event_json = _make_ndjson_result_event(result_text)

    stdout_lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        result_event_json,
    ]
    mock_popen = _make_mock_popen(stdout_lines, returncode=0)

    fake_settings = MagicMock()
    fake_settings.REPORT_DATA_PATH = tmp_path
    fake_settings.DEEP_RESEARCH_PATH = tmp_path / "deep-research"
    fake_settings.ANTHROPIC_API_KEY = ""
    fake_settings.ANTHROPIC_MODEL = ""
    monkeypatch.setattr(runner, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_popen)

    with pytest.raises(runner.ClaudeSkillError, match="产物缺失"):
        runner.run_skill(
            skill="stage1_business_understanding",
            company=company,
            years=[2025],
            timeout_seconds=60,
            run_id=None,
        )


def test_run_skill_skips_recovery_when_files_already_exist(tmp_path: Path, monkeypatch):
    """产物已存在时不应触发兜底（节省时间）。"""
    company = "测试公司C"
    out_dir = tmp_path / company / "md" / "research_file"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{company}_业务概况.md").write_text("# 已存在", encoding="utf-8")
    (out_dir / f"{company}_行业分析.md").write_text("# 已存在", encoding="utf-8")

    stdout_lines = [json.dumps({"type": "system", "subtype": "init"})]
    mock_popen = _make_mock_popen(stdout_lines, returncode=0)

    fake_settings = MagicMock()
    fake_settings.REPORT_DATA_PATH = tmp_path
    fake_settings.DEEP_RESEARCH_PATH = tmp_path / "deep-research"
    fake_settings.ANTHROPIC_API_KEY = ""
    fake_settings.ANTHROPIC_MODEL = ""
    monkeypatch.setattr(runner, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_popen)

    result = runner.run_skill(
        skill="stage1_business_understanding",
        company=company,
        years=[2025],
        timeout_seconds=60,
        run_id=None,
    )
    # 产物未被覆盖（还是"# 已存在"）
    assert (out_dir / f"{company}_业务概况.md").read_text(encoding="utf-8") == "# 已存在"
    assert result.returncode == 0

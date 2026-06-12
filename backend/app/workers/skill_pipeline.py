"""年报下载 worker（subprocess 调本机 claude CLI 跑 skill）。

设计要点：
- 不用 LLM API key（用户环境是 M3，没有 Anthropic key）
- subprocess 调本机 `claude` CLI（Claude Code 自带），用 --print 非交互模式
- 注入 playwright MCP（通过 --mcp-config 临时文件），让 sub-agent 能用浏览器工具
- 实时读 stdout，匹配关键行作为 SSE 进度事件
- CLI 退出后扫描 PDF 目录，diff 新文件 → 调 _register_pdf 登记

入口：routers/reports.py 的 POST /reports/download

依赖：系统 PATH 有 `claude` 和 `npx`（node.js 自带）
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from app.config import get_settings
from app.db import session as db_session
from app.models import Company, ReportRun
from app.workers import progress_bus
# 复用 download_pipeline 里的 3 个 helper
from app.workers.download_pipeline import _update_run, _publish_safe, _register_pdf

_log = logging.getLogger(__name__)


# ============= 配置 =============


# 给 claude CLI 的 prompt：CLI 风格（/skill <arg1> <arg2> ...），不是自然语言。
# Claude Code 的 /skill 语法只接受位置参数，不会把 "请调用 /skill ..." 解析为 skill 调用。
PROMPT_TEMPLATE = "/annual-report-search {stock_code} {company_name} {years_str} {output_dir}"


# ============= stdout 解析 =============


# claude CLI --print 输出经过 stream-json / text 模式（不同版本格式不同）
# 我们用宽松匹配，提取含中文关键词的行
PROGRESS_KEYWORDS = (
    "下载", "搜索", "找到", "已下载", "失败", "完成",
    "开始", "正在", "已找到 PDF", "WAF", "打开", "填入",
    "查询", "提交", "识别", "PDF", "上交所", "深交所", "年报",
    "结果", "字节", "格式", "路径", "大小", "公告", "页",
)

# 工具调用标记（claude CLI 在 stdout 里会输出 [Tool: ...] 之类）
TOOL_RE = re.compile(r"\[Tool:\s*(\w+)\]")


def _parse_progress_line(line: str) -> Optional[str]:
    """从 claude CLI 的 stdout 行中提取对用户友好的进度消息。"""
    line = line.rstrip()
    if not line or len(line) > 500:
        return None
    # JSON 事件（stream-json 模式）跳过
    if line.startswith("{") and line.endswith("}"):
        return None
    # 工具调用标记
    m = TOOL_RE.match(line)
    if m:
        return f"[工具] {m.group(1)}"
    # 关键词匹配
    if any(kw in line for kw in PROGRESS_KEYWORDS):
        return line[:200]
    return None


# ============= MCP config 临时文件 =============


def _write_playwright_mcp_config() -> Path:
    """写临时 MCP config 文件（启用 playwright），用 --mcp-config 注入。

    Claude Code 默认从 ~/.claude.json 读 MCP。
    为了不污染全局配置，写到项目 .tmp 目录，用 --mcp-config 显式传入。
    """
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    tmp_dir = project_root / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    config_path = tmp_dir / "mcp-config-skill.json"
    config = {
        "mcpServers": {
            "playwright": {
                "command": "npx",
                "args": ["-y", "@playwright/mcp@latest"],
            }
        }
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


# ============= 核心：subprocess 调 claude CLI =============


def _run_claude_cli(
    run_id: int,
    prompt: str,
    *,
    cwd: str,
    timeout: int = 900,
) -> tuple[int, str]:
    """同步跑 claude CLI（subprocess），实时把进度行推 SSE。

    返回 (returncode, last_50_lines)
    """
    mcp_config = _write_playwright_mcp_config()

    # Windows 上 Popen 不直接解析 .cmd / .bat，必须用绝对路径 + 后缀
    # 优先 shutil.which（解析 PATHEXT），找不到就硬编码 npm 全局路径
    import shutil
    claude_exe = (
        shutil.which("claude")
        or shutil.which("claude.cmd")
        or shutil.which("claude.exe")
        or r"C:\Users\LiBo\AppData\Roaming\npm\claude.cmd"
    )

    cmd = [
        claude_exe,
        "--print",          # 非交互模式，跑一次退出
        "--mcp-config", str(mcp_config),
        "--dangerously-skip-permissions",  # 跳过权限弹窗（非交互必须）
        prompt,
    ]

    _log.info("[run %s] claude CLI 启动: %s", run_id, " ".join(cmd[:5]) + " ...")
    _publish_safe(run_id, "启动 claude CLI 跑 annual-report-search skill…", stage=0)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=cwd,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        raise RuntimeError(
            "找不到 `claude` 命令。请确认 Claude Code 已安装且在 PATH 里（Windows: 'where claude'）"
        )

    # 实时读 stdout（阻塞循环 + 推 SSE）
    assert proc.stdout is not None
    tail: list[str] = []
    try:
        for line in proc.stdout:
            progress = _parse_progress_line(line)
            if progress:
                _publish_safe(run_id, progress, stage=0)
            tail.append(line.rstrip())
            if len(tail) > 200:
                tail.pop(0)
    except Exception as e:
        _log.warning("[run %s] read stdout failed: %s", run_id, e)

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"claude CLI 超时（>{timeout}s）")

    return proc.returncode, "\n".join(tail[-50:])


# ============= Worker 入口 =============


def run_skill_pipeline(run_id: int, company_id: int, years: List[int]) -> None:
    """年报下载 worker 入口（subprocess 调 claude CLI）。"""
    _update_run(run_id, status="running", current_stage=0)
    _publish_safe(run_id, f"开始下载 {len(years)} 份年报（subprocess 调 claude CLI）", stage=0)

    # 1) 查 company
    with db_session.SessionLocal() as s:
        company: Company | None = s.get(Company, company_id)
        if not company:
            _update_run(run_id, status="failed", error=f"公司不存在 id={company_id}")
            return
        company_name = company.name
        stock_code = company.stock_code

    if not stock_code:
        _update_run(
            run_id,
            status="failed",
            error=f"公司 {company_name} 缺 stock_code，请先在「搜索/上传」页补全",
        )
        return

    settings = get_settings()
    # skill 落在 pdf/.staging/（避开 skill 启发式 dedup：若 output_dir=pdf/original/，skill 看到 pdf/根 有同名文件会跳过）
    # worker 跑完后调 upload_pdf 从 .staging/ 复制到 pdf/original/
    pdf_dir: Path = settings.REPORT_DATA_PATH / company_name / "pdf"
    staging_dir: Path = pdf_dir / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # 每次跑清空 staging（避免历次遗留 + 触发 skill 看到"已存在"跳过）
    for old in staging_dir.glob("*.pdf"):
        old.unlink()

    sorted_years = sorted(set(years), reverse=True)
    _publish_safe(run_id, f"目标目录: {staging_dir}", stage=0)

    # 2) 记录下载前的文件列表（用于 diff，只看 staging/）
    before = {p.resolve() for p in staging_dir.glob("*.pdf")}

    # 3) 调 claude CLI（每年一次，串行）
    years_str = " ".join(str(y) for y in sorted_years)
    prompt = PROMPT_TEMPLATE.format(
        stock_code=stock_code,
        company_name=company_name,
        years_str=years_str,
        output_dir=str(staging_dir).replace("\\", "/"),  # 避免 Windows 反斜杠
    )

    project_root = Path(__file__).resolve().parent.parent.parent.parent
    try:
        returncode, tail = _run_claude_cli(
            run_id, prompt,
            cwd=str(project_root),
            timeout=900,  # 15 分钟（3 份 PDF 给足时间）
        )
    except Exception as e:
        _log.exception("[run %s] claude CLI 调用失败", run_id)
        _update_run(run_id, status="failed", error=f"claude CLI 调用失败: {e}")
        return

    if returncode != 0:
        _publish_safe(run_id, f"claude CLI 退出码 {returncode}，tail: {tail[:300]}", stage=0, level="error")
        _update_run(run_id, status="failed", error=f"claude CLI 退出码 {returncode}")
        return

    _publish_safe(run_id, "claude CLI 执行完成，扫描下载结果…", stage=0)

    # 4) diff 新文件（只看 staging/）
    after = {p.resolve() for p in staging_dir.glob("*.pdf")}
    new_files = sorted(after - before, key=lambda p: p.stat().st_mtime)

    if not new_files:
        _update_run(
            run_id,
            status="failed",
            error="claude CLI 完成但未发现新 PDF（可能 skill 执行失败，看上面 tail 日志）",
        )
        return

    # 5) 调 _register_pdf 登记每个文件
    successes: List[str] = []
    failures: List[str] = []

    for i, pdf_path in enumerate(new_files):
        # 从文件名解析年份：{company_name}{year}年年度报告.pdf
        m = re.search(r"(\d{4})年年度报告", pdf_path.name)
        if not m:
            _publish_safe(run_id, f"跳过（文件名不含年份）: {pdf_path.name}", stage=0, level="warn")
            continue
        year = int(m.group(1))

        # 构造一个 DownloadOutcome 兼容 _register_pdf 的签名
        outcome = type("O", (), {
            "year": year,
            "pdf_path": pdf_path,
            "sha256": "",  # _register_pdf 不看这个
            "file_size": pdf_path.stat().st_size,
        })()

        _update_run(run_id, current_stage=i + 1)
        _register_pdf(
            run_id=run_id,
            company_id=company_id,
            company_name=company_name,
            year=year,
            outcome=outcome,
            stage=i + 1,
            successes=successes,
            failures=failures,
        )

    # 6) 汇总
    summary = f"完成：成功 {len(successes)}/{len(sorted_years)}"
    if failures:
        summary += f"，失败 {len(failures)}（{'；'.join(failures)}）"
    _publish_safe(run_id, summary, stage=len(sorted_years), level=("error" if failures and not successes else "info"))

    if successes:
        _update_run(
            run_id,
            status="done",
            current_stage=len(sorted_years),
            final_path=successes[0],
        )
    else:
        _update_run(
            run_id,
            status="failed",
            error=f"所有 PDF 登记失败：{'；'.join(failures)}",
        )

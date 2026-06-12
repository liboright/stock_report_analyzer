"""Claude Code skill runner：通过 subprocess 调 ``claude`` CLI 跑指定 skill。

设计：
- 仅支持 stage1_business_understanding（M3 阶段唯一目标）
- 使用 ``claude -p "/<skill> <args>" --add-dir ...`` non-interactive 模式
- 同步阻塞（subprocess.run），timeout 10 分钟
- ANTHROPIC_API_KEY 从 settings 注入 subprocess env
- 跑完后验证产物文件存在，路径返回
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import get_settings


# 当前 M3 支持的 skill 白名单
SUPPORTED_SKILLS = {
    "stage1_business_understanding": "Stage 1: 业务了解",
}


@dataclass
class SkillRunResult:
    skill: str
    company: str
    year: Optional[int]
    output_path: Path
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float


class ClaudeSkillError(RuntimeError):
    """skill 执行失败（subprocess 错误、超时、产物缺失）"""


def _build_command(
    skill: str,
    company: str,
    year: Optional[int],
    add_dirs: list[Path],
) -> list[str]:
    """构造 ``claude -p "/<skill> <company> [year]" --add-dir ... --bare`` 命令。

    注意：Windows 下 claude 是 .cmd 脚本（位于 npm 全局目录），``shutil.which("claude")``
    返回的路径可能带 ``.CMD`` 后缀，``subprocess.run`` 直接传 ``"claude"`` 会找不到
    （WinError 2）。这里把 which 返回的全路径作为可执行入口。
    """
    if skill not in SUPPORTED_SKILLS:
        raise ClaudeSkillError(f"不支持的 skill: {skill}（仅支持 {list(SUPPORTED_SKILLS.keys())}）")
    claude_path = shutil.which("claude")
    if not claude_path:
        raise ClaudeSkillError("未找到 claude CLI（请先 `npm install -g @anthropic-ai/claude-code`）")

    # prompt 部分：'/stage1_business_understanding 宁德时代 2023'
    parts = [f"/{skill}", company]
    if year is not None:
        parts.append(str(year))
    prompt = " ".join(parts)

    cmd: list[str] = [claude_path, "-p", prompt, "--bare"]
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    return cmd


def _build_subprocess_env() -> dict[str, str]:
    """从 settings 注入 ANTHROPIC_API_KEY；保留当前 PATH/系统变量。"""
    s = get_settings()
    env = os.environ.copy()
    if s.ANTHROPIC_API_KEY and s.ANTHROPIC_API_KEY != "sk-ant-placeholder-replace-me":
        env["ANTHROPIC_API_KEY"] = s.ANTHROPIC_API_KEY
    if s.ANTHROPIC_MODEL:
        env["ANTHROPIC_MODEL"] = s.ANTHROPIC_MODEL
    return env


def _expected_output(skill: str, company: str, settings) -> Path:
    """计算 skill 产物路径。stage1 -> md/research_file/{公司}_业务概况.md"""
    if skill == "stage1_business_understanding":
        return settings.REPORT_DATA_PATH / company / "md" / "research_file" / f"{company}_业务概况.md"
    raise ClaudeSkillError(f"未知 skill: {skill}")


def run_skill(
    skill: str,
    company: str,
    year: Optional[int] = None,
    timeout_seconds: int = 600,
) -> SkillRunResult:
    """同步跑 claude skill，返回结果。

    Raises:
        ClaudeSkillError: CLI 不存在、subprocess 非 0 退出、超时、产物缺失。
    """
    import time
    s = get_settings()
    settings = s  # 兼容下面 expected_output 用法

    add_dirs = [s.REPORT_DATA_PATH, s.DEEP_RESEARCH_PATH]
    cmd = _build_command(skill, company, year, add_dirs)
    env = _build_subprocess_env()

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(s.REPORT_DATA_PATH),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeSkillError(
            f"claude skill {skill} 超时（>{timeout_seconds}s）。stderr: {e.stderr or ''}"
        ) from e
    elapsed = time.time() - start

    output_path = _expected_output(skill, company, s)

    if proc.returncode != 0:
        raise ClaudeSkillError(
            f"claude skill {skill} 退出码 {proc.returncode}。stderr: {proc.stderr[-1000:]}"
        )

    if not output_path.exists():
        raise ClaudeSkillError(
            f"claude skill {skill} 跑完但产物缺失: {output_path}"
        )

    return SkillRunResult(
        skill=skill,
        company=company,
        year=year,
        output_path=output_path,
        returncode=proc.returncode,
        stdout=proc.stdout[-2000:],  # 截尾
        stderr=proc.stderr[-2000:],
        elapsed_seconds=elapsed,
    )

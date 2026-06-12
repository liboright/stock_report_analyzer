"""财务报告 MD 复制到 by_section/10_第十节_财务报告.md。

按 docs/artifacts.md 规范：
- 源：`{公司}/md/raw/财务报告/{公司}{年份}年年度报告/{公司}{年份}年年度报告_财务报告.md`
- 目标：`{公司}/md/clean/{公司}{年份}年年报/by_section/10_第十节_财务报告.md`

设计：
- 单文件复制（`shutil.copy2` 保留 mtime，便于增量比较）
- 幂等：目标已存在且 force=False 时直接返回
- 不做内容改写（财务报告 MD 不参与标注）
"""
from __future__ import annotations

import shutil
from pathlib import Path

from app.config import get_settings


def _by_section_dir(company: str, year: int) -> Path:
    settings = get_settings()
    return (
        settings.REPORT_DATA_PATH
        / company
        / "md"
        / "clean"
        / f"{company}{year}年年报"
        / "by_section"
    )


def _finance_target(company: str, year: int) -> Path:
    return _by_section_dir(company, year) / "10_第十节_财务报告.md"


def copy_finance_md_to_by_section(
    company: str,
    year: int,
    *,
    force: bool = False,
) -> Path:
    """把财务报告 MD 复制到 by_section/10_第十节_财务报告.md。

    Args:
        company: 公司名（中文，与 DB company.name 一致）
        year: 年份（int）
        force: 已存在时是否覆盖（默认 False，幂等）

    Returns:
        目标文件绝对路径。

    Raises:
        FileNotFoundError: 源财务 MD 不存在
    """
    settings = get_settings()
    src = (
        settings.REPORT_DATA_PATH
        / company
        / "md"
        / "raw"
        / "财务报告"
        / f"{company}{year}年年度报告"
        / f"{company}{year}年年度报告_财务报告.md"
    )
    if not src.is_file():
        raise FileNotFoundError(f"财务报告 MD 不存在: {src}")

    out_dir = _by_section_dir(company, year)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = _finance_target(company, year)
    if dst.is_file() and not force:
        return dst  # 幂等
    shutil.copy2(src, dst)
    return dst

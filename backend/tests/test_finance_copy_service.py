"""finance_copy_service 单元测试。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.services.finance_copy_service import copy_finance_md_to_by_section


def _write_finance_md(rep_data: Path, company: str, year: int, text: str = "# 财务报告\n内容") -> Path:
    """手工写财务 MD 到约定位置。"""
    src_dir = rep_data / company / "md" / "raw" / "财务报告" / f"{company}{year}年年度报告"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / f"{company}{year}年年度报告_财务报告.md"
    src.write_text(text, encoding="utf-8")
    return src


def test_copy_creates_target_file(tmp_env: Path) -> None:
    """happy path：源存在 → 目标文件落盘。"""
    from app.config import get_settings

    settings = get_settings()
    _write_finance_md(settings.REPORT_DATA_PATH, "宁德时代", 2023, "财务报告内容")
    target = copy_finance_md_to_by_section("宁德时代", 2023)
    assert target.is_file()
    assert target.name == "10_第十节_财务报告.md"
    # 父目录是 by_section/
    assert target.parent.name == "by_section"
    assert "宁德时代2023年年报" in str(target)
    # 内容一致
    assert target.read_text(encoding="utf-8") == "财务报告内容"


def test_copy_idempotent(tmp_env: Path) -> None:
    """幂等：第二次跑不覆盖，mtime 不变。"""
    from app.config import get_settings

    settings = get_settings()
    _write_finance_md(settings.REPORT_DATA_PATH, "宁德时代", 2023, "v1")
    target = copy_finance_md_to_by_section("宁德时代", 2023)
    mtime1 = target.stat().st_mtime_ns
    time.sleep(0.01)
    # 不变内容
    target2 = copy_finance_md_to_by_section("宁德时代", 2023)
    mtime2 = target2.stat().st_mtime_ns
    assert target == target2
    assert mtime1 == mtime2  # 幂等：不重写
    # 但 target 内容仍为 v1
    assert target.read_text(encoding="utf-8") == "v1"


def test_copy_force_overwrites(tmp_env: Path) -> None:
    """force=True 强制覆盖。"""
    from app.config import get_settings

    settings = get_settings()
    _write_finance_md(settings.REPORT_DATA_PATH, "宁德时代", 2023, "v1")
    copy_finance_md_to_by_section("宁德时代", 2023)
    # 改写源 MD 模拟「源更新」
    src = _write_finance_md(settings.REPORT_DATA_PATH, "宁德时代", 2023, "v2-new")
    target = copy_finance_md_to_by_section("宁德时代", 2023, force=True)
    assert target.read_text(encoding="utf-8") == "v2-new"


def test_copy_raises_if_source_missing(tmp_env: Path) -> None:
    """源 MD 不存在 → FileNotFoundError。"""
    with pytest.raises(FileNotFoundError) as exc_info:
        copy_finance_md_to_by_section("宁德时代", 2023)
    assert "财务报告 MD 不存在" in str(exc_info.value)

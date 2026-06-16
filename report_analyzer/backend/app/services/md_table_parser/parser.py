"""md_table_parser 子包顶层入口。

本模块对外暴露唯一一个公开函数 extract_tables_from_md，
串起：
    1. str/Path 归一化
    2. 文件存在性 / 读 IO 错误兜底
    3. 报告期年份推断（从父目录 / 文件名兜底）
    4. 委托给 table_extractor.extract_tables_from_md_text 完成实际解析
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Union

from .exceptions import MdFileNotFoundError, MdReadError
from .models import TableInfo
from .table_extractor import extract_tables_from_md_text
from .title_unit_locator import extract_year_from_md_path

_log = logging.getLogger(__name__)

PathLike = Union[str, Path]


def extract_tables_from_md(md_path: PathLike) -> List[TableInfo]:
    """从年报 markdown 文件中抽取所有结构化表格。

    Args:
        md_path: .md 文件路径（str 或 Path）。

    Returns:
        List[TableInfo]: 按 <table> 在文本中出现顺序排列；
            无表则返回空列表（不抛 NoTableFoundError）。

    Raises:
        MdFileNotFoundError: 路径不存在。
        MdReadError: 读文件失败（编码 / 权限 / IO 异常）。
        ReportYearInferenceError: 无法从父目录或文件名推断报告期年份。
    """
    path = Path(md_path)

    if not path.exists() or not path.is_file():
        _log.error("md 文件不存在: %s", path)
        raise MdFileNotFoundError(f"md 文件不存在: {path}")

    # 报告期年份推断（在读文件之前，提前失败以便错误信息更聚焦）
    report_year = extract_year_from_md_path(path)

    # 读文件
    try:
        md_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        _log.error("md 文件非 UTF-8 编码: %s (%s)", path, exc)
        raise MdReadError(f"md 文件编码非 UTF-8: {path} ({exc})") from exc
    except OSError as exc:
        _log.error("读 md 文件失败: %s (%s)", path, exc)
        raise MdReadError(f"读 md 文件失败: {path} ({exc})") from exc

    _log.debug("开始解析 %s (report_year=%d)", path, report_year)
    return extract_tables_from_md_text(
        md_text=md_text,
        report_year=report_year,
        source_path=path,
    )

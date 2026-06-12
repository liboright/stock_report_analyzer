"""md_table_parser 子包异常定义。

继承关系：
    MdTableError(Exception)
        ├── MdFileNotFoundError(MdTableError, FileNotFoundError)
        ├── MdReadError(MdTableError, IOError)
        ├── ReportYearInferenceError(MdTableError, ValueError)
        └── NoTableFoundError(MdTableError)
"""

from __future__ import annotations

from pathlib import Path
from typing import List


class MdTableError(Exception):
    """本子包所有异常的基类。"""


class MdFileNotFoundError(MdTableError, FileNotFoundError):
    """传入的 .md 路径不存在。"""


class MdReadError(MdTableError, IOError):
    """.md 读文件失败（编码 / 权限等 IO 异常）。"""


class ReportYearInferenceError(MdTableError, ValueError):
    """无法从父目录名或文件名推断报告期年份。"""

    def __init__(self, path: Path, candidates: List[str]) -> None:
        self.path = path
        self.candidates = candidates
        super().__init__(
            f"无法从 {path} 的父目录或文件名推断报告期年份，候选段: {candidates}"
        )


class NoTableFoundError(MdTableError):
    """md 中没有任何 <table> 块。

    默认上层会降级为 warning + 返回空列表，不抛出此异常；
    保留此类型供严格模式调用方使用。
    """

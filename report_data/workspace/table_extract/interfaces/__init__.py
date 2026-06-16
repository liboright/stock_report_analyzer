"""
表格提取模块接口定义
导出主要公开接口
"""

from .core import TableExtractor
from .parser import TableParser, ParsedTable
from .classifier import TableClassifier
from .merger import TableMerger, MergedData
from .models import (
    TableType,
    TableCell,
    CellPosition,
    TableInfo,
    ColumnMapping,
    TableParseError,
    ColumnAlignError,
)

__all__ = [
    # 核心类
    "TableExtractor",
    "TableParser",
    "TableClassifier",
    "TableMerger",
    # 数据结构
    "TableType",
    "TableCell",
    "CellPosition",
    "ParsedTable",
    "TableInfo",
    "MergedData",
    "ColumnMapping",
    # 异常
    "TableParseError",
    "ColumnAlignError",
]
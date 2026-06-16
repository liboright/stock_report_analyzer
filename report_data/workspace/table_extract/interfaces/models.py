"""
数据结构定义
表格解析和合并过程中使用的数据结构
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class TableType(Enum):
    """
    表格类型枚举

    对应 8 类输出 CSV：
    - REVENUE_COMPOSITION: 营业收入构成
    - EXPENSE_ANALYSIS: 费用分析
    - CASH_FLOW: 现金流
    - RD_INVESTMENT: 研发投入
    - PRODUCTION_CAPACITY: 产能产量
    - PRODUCTION_SALES: 产销情况
    - CUSTOMER_SUPPLIER: 客户供应商
    - TECH_PARAMS: 技术参数
    - OTHER: 其他（不输出）
    """
    REVENUE_COMPOSITION = "营业收入构成"
    EXPENSE_ANALYSIS = "费用分析"
    CASH_FLOW = "现金流"
    RD_INVESTMENT = "研发投入"
    PRODUCTION_CAPACITY = "产能产量"
    PRODUCTION_SALES = "产销情况"
    CUSTOMER_SUPPLIER = "客户供应商"
    TECH_PARAMS = "技术参数"
    OTHER = "其他"


@dataclass
class CellPosition:
    """
    单元格在网格中的位置

    用于 rowspan 追踪：
    - 当一个单元格有 rowspan 时，需要将其内容复制到后续行
    - CellPosition 记录了单元格的起始位置和跨越范围
    """
    row: int          # 起始行（0-indexed）
    col: int          # 起始列（0-indexed）
    rowspan: int = 1  # 跨越行数
    colspan: int = 1  # 跨越列数


@dataclass
class TableCell:
    """
    表格单元格

    包含单元格值和位置信息，用于构建二维网格
    """
    value: str
    row: int = 0                      # 起始行
    col: int = 0                      # 起始列
    rowspan: int = 1                  # 跨越行数
    colspan: int = 1                  # 跨越列数
    is_header: bool = False           # 是否为表头单元格


@dataclass
class ParsedTable:
    """
    解析后的完整表格

    包含二维数据网格，其中 None 表示被 rowspan/colspan 合并的单元格位置
    """
    headers: List[List[str]] = field(default_factory=list)  # 多级表头行
    data_grid: List[List[Optional[str]]] = field(default_factory=list)  # 二维数据网格
    row_count: int = 0
    col_count: int = 0
    raw_html: str = ""

    def get_cell(self, row: int, col: int) -> Optional[str]:
        """
        获取指定位置的单元格值

        Args:
            row: 行索引
            col: 列索引

        Returns:
            Optional[str]: 单元格值，None 表示被合并覆盖
        """
        if 0 <= row < self.row_count and 0 <= col < self.col_count:
            return self.data_grid[row][col]
        return None

    def get_row(self, row: int) -> List[Optional[str]]:
        """获取指定行所有单元格"""
        if 0 <= row < self.row_count:
            return self.data_grid[row]
        return []


@dataclass
class TableInfo:
    """
    单个表格的完整信息

    从一个 md 文件中提取的一个表格的元数据和数据
    """
    year: str                           # 年份（如 "2025"）
    title: str                          # 表格标题
    section: str                        # 所属小节
    unit: str                            # 单位（如 "千元"、"万元"）
    table_type: TableType               # 表格类型
    headers: List[List[str]] = field(default_factory=list)  # 表头行
    rows: List[List[str]] = field(default_factory=list)     # 数据行（展平后）
    raw_html: str = ""                  # 原始 HTML


@dataclass
class ColumnMapping:
    """
    列对齐映射

    用于描述不同年份表格之间的列对应关系
    """
    source_col: int       # 源列索引
    target_col: int       # 目标列索引
    semantic: str = ""    # 列语义（如 "金额"、"占比"、"同比"）


@dataclass
class HeaderStructure:
    """
    表头结构信息（用于多级表头）

    描述表头的层级结构和每列的语义
    """
    main_headers: List[str] = field(default_factory=list)   # 主表头（如年份）
    sub_headers: List[str] = field(default_factory=list)    # 子表头（如金额、占比）
    span: int = 1                                            # 子表头跨越列数
    item_col_index: int = 0                                  # 项目列的索引


@dataclass
class MergedData:
    """
    合并后的表格数据

    包含多年份、多列的合并结果，用于输出 CSV
    """
    table_type: TableType
    item_col: str = "项目"                                    # 项目列的列名
    years: List[str] = field(default_factory=list)            # 年份列表（降序）
    headers: List[List[str]] = field(default_factory=list)    # 多级表头
    data: List[List[str]] = field(default_factory=list)      # 数据行
    column_mappings: List[ColumnMapping] = field(default_factory=list)  # 列映射
    header_structure: Optional[HeaderStructure] = None        # 表头结构


class TableParseError(Exception):
    """表格解析异常"""
    pass


class ColumnAlignError(Exception):
    """列对齐异常（多年份列数不一致时）"""
    pass


class ClassificationError(Exception):
    """表格分类异常"""
    pass
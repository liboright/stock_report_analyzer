"""md_table_parser 数据结构定义（纯 dataclass，无业务逻辑）。

包含：
    HeaderColumn   - 表头每列的元信息（年份/同比标记 + 列角色）
    UnitInfo       - 单位信息（统一单位行 + 列名括号单位）
    YearMapping    - 报告期年份及其相对年份映射
    TableLocation  - 单个 <table> 块在 md 文本中的位置和上下文（内部中间 DTO）
    TableInfo      - 最终对外输出 DTO
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class HeaderColumn:
    """展平表头中一个逻辑列的元信息。"""

    index: int
    """在展平后 headers 中的列索引（0-based）。"""

    raw: str
    """原始表头文本（未做年份改写）。"""

    normalized: str
    """已做年份标准化后的文本；若 is_yoy=True 则保持与 raw 一致。"""

    is_year: bool
    """是否为年份列（显式 YYYY年 或 被映射的相对年份列）。"""

    is_yoy: bool
    """是否为"同比 / 变化 / 增减"类列（不改写文本，只做元数据标记）。"""

    year_value: Optional[int]
    """解析出的具体年份 YYYY；yoy 列或非年份列为 None。"""

    column_role: str
    """列角色: "item" | "year" | "yoy" | "amount" | "ratio" | "other"。"""


@dataclass
class UnitInfo:
    """单位信息。

    单位可能来自两处：
    1. 表格上方独占一行 "单位：千元" → raw_lines/primary
    2. 列名括号 "销售额（千元）" → from_column_brackets[col_index] = "千元"
    """

    raw_lines: List[str] = field(default_factory=list)
    """表格上方所有 "单位：..." 行的原始内容（可能多条）。"""

    primary: Optional[str] = None
    """主单位（取 raw_lines[0]）。"""

    from_column_brackets: Dict[int, str] = field(default_factory=dict)
    """列索引 → 该列名末尾括号里提取出的单位。"""


@dataclass
class YearMapping:
    """报告期年份及其相对年份映射。"""

    report_year: int
    """从 md 文件所在父目录推断出的报告期年份。"""

    current_year: int
    """"本年度/本期/本报告期" 映射到的年份（= report_year）。"""

    previous_year: int
    """"上年度/上期/上年同期" 映射到的年份（= report_year - 1）。"""

    year_before_previous: int
    """"前年/上上年" 映射到的年份（= report_year - 2）。"""

    applied_count: int = 0
    """累计替换处数（用于调试 / 日志）。"""


@dataclass
class TableLocation:
    """单个 <table> 块在 md 文本中的位置和上下文。

    这是内部中间 DTO，由 title_unit_locator.find_table_locations 产出，
    经 table_extractor.convert_parsed_table 消费后并入 TableInfo。
    """

    start: int
    """<table 标签在 md 文本中的字符起始偏移。"""

    end: int
    """</table> 之后的字符偏移。"""

    raw_html: str
    """完整的 <table>...</table> 字符串。"""

    title: Optional[str] = None
    """表格上方最近的 Markdown 标题（已剥常见前置序号）。"""

    title_level: Optional[int] = None
    """标题级别 1-6（# 个数）。"""

    unit: Optional[UnitInfo] = None
    """从"上一个标题 ~ <table>"之间提取的单位信息。"""

    preceding_text: str = ""
    """标题到 <table> 之间的非空文字（用于正文年份标准化计数）。"""


@dataclass
class TableInfo:
    """最终对外输出的 DTO。"""

    source_path: Path
    """源 md 文件的绝对路径。"""

    table_index: int
    """在该 md 文件中第几个 <table>（0-based）。"""

    report_year: int
    """报告期年份。"""

    title: Optional[str] = None
    title_level: Optional[int] = None

    unit: Optional[UnitInfo] = None

    headers: List[List[str]] = field(default_factory=list)
    """多级表头（年份列名已做标准化改写）。"""

    data_grid: List[List[str]] = field(default_factory=list)
    """二维数据网格：None 已替换为 ""、折行已合并、文本已清洗。"""

    row_count: int = 0
    col_count: int = 0

    header_columns: List[HeaderColumn] = field(default_factory=list)
    """展平后每列的元信息。长度 = col_count。"""

    year_mapping: Optional[YearMapping] = None

    raw_html: str = ""
    raw_offset: int = 0
    """在原 md 文本中的字符偏移（= TableLocation.start）。"""

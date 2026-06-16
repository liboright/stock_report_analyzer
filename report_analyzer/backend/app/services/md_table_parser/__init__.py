"""年报 Markdown 中 HTML 表格解析子包。

职责边界：
- 输入：年报 markdown 文件路径（含 <table>...</table> HTML 块）
- 输出：List[TableInfo] 结构化对象列表
- 不做：8 类表格分类、多年合并、CSV 落盘、FastAPI 路由

解析流程（由 extract_tables_from_md 串起）：
    1. title_unit_locator  → 找 <table> 位置 + 上方 Markdown 标题 + "单位：xxx" 行
    2. year_normalizer     → 报告期年份 → 相对年份（本期/上期/前年）映射
    3. text_cleaner        → LaTeX 残留 / 方框字符 / 折行合并
    4. table_extractor     → 委托外部 table_parser 解析 rowspan/colspan + 装配 TableInfo
    5. parser              → 顶层入口：归一化 Path、IO/年份错误兜底

外部依赖：
    D:/quant/deep-research-report/shared/tools/table_parser.py
    （已通过 app.config.inject_external_paths() 注入 sys.path）
"""
from .exceptions import (
    MdFileNotFoundError,
    MdReadError,
    MdTableError,
    NoTableFoundError,
    ReportYearInferenceError,
)
from .models import (
    HeaderColumn,
    TableInfo,
    TableLocation,
    UnitInfo,
    YearMapping,
)
from .parser import extract_tables_from_md
from .table_extractor import convert_parsed_table, extract_tables_from_md_text
from .text_cleaner import clean_cell, clean_text, merge_continuation_rows
from .title_unit_locator import (
    extract_title_for_offset,
    extract_unit_for_offset,
    extract_year_from_md_path,
    find_table_locations,
)
from .year_normalizer import (
    build_year_mapping,
    detect_yoy_column,
    extract_explicit_year,
    normalize_year_in_cell,
    normalize_year_in_text,
)

__all__ = [
    # 顶层入口
    "extract_tables_from_md",
    # 装配层
    "extract_tables_from_md_text",
    "convert_parsed_table",
    # 定位层
    "find_table_locations",
    "extract_title_for_offset",
    "extract_unit_for_offset",
    "extract_year_from_md_path",
    # 年份标准化
    "build_year_mapping",
    "detect_yoy_column",
    "extract_explicit_year",
    "normalize_year_in_cell",
    "normalize_year_in_text",
    # 文本清洗
    "clean_cell",
    "clean_text",
    "merge_continuation_rows",
    # DTO
    "HeaderColumn",
    "TableInfo",
    "TableLocation",
    "UnitInfo",
    "YearMapping",
    # 异常
    "MdTableError",
    "MdFileNotFoundError",
    "MdReadError",
    "ReportYearInferenceError",
    "NoTableFoundError",
]

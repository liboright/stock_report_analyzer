"""解析产物文件树 schema。

GET /companies/{name}/reports/{year}/files 的返回结构。
路径都是相对 ``REPORT_DATA_PATH`` 的 POSIX 形式，前端可直接拼到
``/api/static/md/<path>`` 拿到内容。
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel


class SubsectionFile(BaseModel):
    title: str
    path: str  # 相对 REPORT_BASE_PATH 的 posix 路径


class ChapterFile(BaseModel):
    section_num: str  # "01".."10"；无第X节标题时为顺序编号
    title: str
    path: str
    subsections: List[SubsectionFile] = []  # v1 始终空（章节文件无 H2）


class Section3File(BaseModel):
    title: str
    path: str


# 'business' = 公司业务概况族（含增量版）
# 'industry' = 行业分析
# 其它（理论上不应有）兜底为 'unknown'
ResearchKind = Literal["business", "industry", "unknown"]


class ResearchFile(BaseModel):
    title: str
    path: str
    kind: ResearchKind = "unknown"


class TableCsvFile(BaseModel):
    category: str  # 8 类之一 或 "其他"
    name: str  # csv 文件名（不含目录）
    path: str  # 相对 REPORT_DATA_PATH 的 posix 路径


class MergedTableFile(BaseModel):
    """阶段 3.x 跨年合并产物的一个 group（_long + _wide 一对，可能缺一）。"""

    group_key: str  # 文件前缀，如 "001_营业收入"
    sanitized_title: str  # 去掉 _long/_wide 后缀（== group_key）
    long_csv: Optional[str] = None  # rel path；不存在时 None
    wide_csv: Optional[str] = None


class FileTreeResponse(BaseModel):
    chapters: List[ChapterFile] = []
    section3: List[Section3File] = []
    research: List[ResearchFile] = []
    tables: List[TableCsvFile] = []
    merged_tables: List[MergedTableFile] = []

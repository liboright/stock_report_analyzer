"""解析产物文件树 schema。

GET /companies/{name}/reports/{year}/files 的返回结构。
路径都是相对 ``REPORT_DATA_PATH`` 的 POSIX 形式，前端可直接拼到
``/api/static/md/<path>`` 拿到内容。
"""
from __future__ import annotations

from typing import List

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


class ResearchFile(BaseModel):
    title: str
    path: str


class TableCsvFile(BaseModel):
    category: str  # 8 类之一 或 "其他"
    name: str  # csv 文件名（不含目录）
    path: str  # 相对 REPORT_DATA_PATH 的 posix 路径


class FileTreeResponse(BaseModel):
    chapters: List[ChapterFile] = []
    section3: List[Section3File] = []
    research: List[ResearchFile] = []
    tables: List[TableCsvFile] = []

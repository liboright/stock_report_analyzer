"""阶段 2.5 表格抽取 → CSV 落盘的请求/响应模型。"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class TablesExtractRequest(BaseModel):
    """POST /companies/{name}/tables/extract 无 body，仅 query `year`，本模型留作未来扩展。"""
    year: int = Field(..., ge=1990, le=2100)


class SectionSummary(BaseModel):
    """单个源 md 章节下抽到的表格数。"""
    section: str  # 源 md stem，例如 "05_五、报告期内主要经营情况"
    count: int


class TablesExtractResponse(BaseModel):
    company: str
    year: int
    total: int
    sections: List[SectionSummary] = Field(default_factory=list)
    csv_paths: List[str] = Field(default_factory=list)  # 全部产物路径（相对 REPORT_DATA_PATH）
    duration_ms: int
    extract_tables_status: str  # 'done' / 'failed' / 'empty' (无 md 输入)
    message: str = ""

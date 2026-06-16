"""AnnualReport 相关 Pydantic schemas。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AnnualReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    year: int
    pdf_path: str
    pdf_sha256: Optional[str] = None
    source: Optional[str] = None
    parse_status: Optional[str] = None
    md_path: Optional[str] = None
    parsed_at: Optional[datetime] = None
    created_at: datetime
    # 切分（财务报告 / 非财务）
    split_status: Optional[str] = None
    finance_pdf_path: Optional[str] = None
    other_pdf_path: Optional[str] = None
    # 切分后双 PDF 解析（业务报告 + 财务报告 各自独立 MD）
    parse_split_status: Optional[str] = None
    business_md_path: Optional[str] = None
    finance_md_path: Optional[str] = None
    # 阶段 2.5 表格抽取
    extract_tables_status: Optional[str] = None
    tables_extracted_at: Optional[datetime] = None
    tables_dir_path: Optional[str] = None


class AnnualReportUploadResponse(BaseModel):
    """上传完成后返回：可能是新增或去重命中。"""
    report: AnnualReportRead
    deduplicated: bool = False
    message: str = ""

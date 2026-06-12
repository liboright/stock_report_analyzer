"""Company 相关 Pydantic schemas。"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="公司中文名")
    stock_code: Optional[str] = Field(None, max_length=16)
    industry: Optional[str] = Field(None, max_length=64)


class CompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    stock_code: Optional[str] = None
    industry: Optional[str] = None
    created_at: datetime


class CompanyDetail(CompanyRead):
    """公司详情：含年报列表与报告运行列表。"""
    annual_reports: List["AnnualReportRead"] = []
    report_runs: List["ReportRunRead"] = []


# 解决前向引用
from app.schemas.annual_report import AnnualReportRead  # noqa: E402
from app.schemas.report import ReportRunRead  # noqa: E402

CompanyDetail.model_rebuild()

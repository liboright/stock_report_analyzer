"""Report 相关 Pydantic schemas。"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReportGenerateRequest(BaseModel):
    year: Optional[int] = Field(
        None, description="指定年份；None 表示使用该公司可用所有年份"
    )
    template: str = Field(default="investment_report", max_length=64)
    stages: Optional[List[int]] = Field(
        None, description="指定要跑的 stage 列表；None 表示 0~4 全跑"
    )


class ReportRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    year: Optional[int] = None
    template: str
    status: str
    current_stage: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    final_path: Optional[str] = None
    error: Optional[str] = None


class ReportRunDetail(ReportRunRead):
    events: List["TaskEventRead"] = []


class ReportContent(BaseModel):
    run_id: int
    path: str
    content: str


# 前向引用
from app.schemas.task import TaskEventRead  # noqa: E402

ReportRunDetail.model_rebuild()

"""Task / TaskEvent 相关 Pydantic schemas。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class TaskEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    stage: Optional[int] = None
    level: str
    message: str
    payload_json: Optional[str] = None
    created_at: datetime


class TaskStatus(BaseModel):
    """任务快照（前端轮询）。"""
    run_id: int
    status: str
    current_stage: Optional[int] = None
    progress_percent: Optional[int] = Field(
        None, ge=0, le=100, description="粗粒度进度，仅作 UI 提示"
    )
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    final_path: Optional[str] = None
    error: Optional[str] = None
    last_event: Optional[TaskEventRead] = None

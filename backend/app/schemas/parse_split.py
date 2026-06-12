"""「切分+解析」组合端点响应模型。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ParseSplitTriggerResponse(BaseModel):
    """POST /companies/{name}/parse-split 立即返回（202）。

    真正执行结果通过 GET /tasks/{run_id}/stream 订阅 SSE 获取。
    """
    run_id: int
    company: str
    year: int
    status: str                # queued
    use_mock: bool
    business_pdf: str          # 业务 PDF 相对路径（来自 other_pdf_path，"非财务"那份）
    finance_pdf: str           # 财务 PDF 相对路径
    annotation_status: Optional[str] = None  # '' / 'annotated' / 'failed'（业务 MD 标注状态）
    message: str

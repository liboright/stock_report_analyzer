"""PDF 切分相关 Pydantic schemas。"""
from __future__ import annotations

from pydantic import BaseModel


class SplitPDFResponse(BaseModel):
    company: str
    year: int
    finance_pdf: str          # 相对 RAW_BASE_PATH
    other_pdf: str            # 相对 RAW_BASE_PATH
    finance_start_page: int   # 0-based
    total_pages: int
    title_text: str           # 识别到的一级标题文本
    message: str = "PDF 切分完成"

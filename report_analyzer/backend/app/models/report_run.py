"""报告生成运行记录。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class ReportRun(Base):
    __tablename__ = "report_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # NULL=跨年
    template: Mapped[str] = mapped_column(String(64), default="investment_report", nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False, index=True)
    current_stage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    final_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="report_runs")  # noqa: F821
    events: Mapped[list["TaskEvent"]] = relationship(  # noqa: F821
        back_populates="run", cascade="all, delete-orphan", order_by="TaskEvent.id"
    )

    def __repr__(self) -> str:
        return f"<ReportRun id={self.id} company_id={self.company_id} status={self.status}>"

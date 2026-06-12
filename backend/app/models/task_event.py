"""任务事件（用于 SSE 推送与审计）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class TaskEvent(Base):
    __tablename__ = "task_event"
    __table_args__ = (Index("idx_task_event_run", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("report_run.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    run: Mapped["ReportRun"] = relationship(back_populates="events")  # noqa: F821

    def __repr__(self) -> str:
        return f"<TaskEvent id={self.id} run_id={self.run_id} level={self.level}>"

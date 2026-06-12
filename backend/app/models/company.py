"""公司表。"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Company(Base):
    __tablename__ = "company"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    stock_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    industry: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    annual_reports: Mapped[List["AnnualReport"]] = relationship(  # noqa: F821
        back_populates="company", cascade="all, delete-orphan"
    )
    report_runs: Mapped[List["ReportRun"]] = relationship(  # noqa: F821
        back_populates="company", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r} code={self.stock_code!r}>"

"""年报 PDF 元信息表。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class AnnualReport(Base):
    __tablename__ = "annual_report"
    __table_args__ = (UniqueConstraint("company_id", "year", name="uq_company_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)

    # 物理路径（相对 REPORT_DATA_PATH，按 docs/artifacts.md 规范）
    pdf_path: Mapped[str] = mapped_column(String(512), nullable=False)
    pdf_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # cninfo/manual_upload

    # 解析状态
    parse_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    md_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    # 切分状态（按"第X节 财务报告"切成两份：财务/非财务）
    split_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    finance_pdf_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    other_pdf_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # 切分后双 PDF 解析状态（业务报告 + 财务报告 各保留独立 MD）
    # parse_split_status: queued / business_done / done / failed
    parse_split_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    business_md_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    finance_md_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # 阶段 2.5 表格抽取状态（md → CSV 落盘）
    # extract_tables_status: pending / done / failed
    extract_tables_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    tables_extracted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    tables_dir_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # 业务 MD 标题标注状态（解析流水线 Step 2.2.5，仅对业务 MD 生效）
    # annotation_status: '' / 'annotated' / 'failed'
    # 财务 MD 不参与标注，因此 annotation_status 仅描述业务 MD。
    # 旧数据 annotation_status=NULL 表示未标注，下次跑 /parse-split 会自动补。
    annotation_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    annotated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="annual_reports")  # noqa: F821

    def __repr__(self) -> str:
        return f"<AnnualReport id={self.id} company_id={self.company_id} year={self.year}>"

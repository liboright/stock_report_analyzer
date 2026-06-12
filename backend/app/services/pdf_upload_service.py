"""PDF 上传 service：multipart 落盘 + SHA-256 去重 + 写 annual_report 表。"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AnnualReport, Company


@dataclass
class UploadOutcome:
    report: AnnualReport
    deduplicated: bool
    message: str


def _safe_filename(name: str) -> str:
    """清理 Windows 非法字符。"""
    bad = '<>:"/\\|?*'
    for c in bad:
        name = name.replace(c, "_")
    return name.strip()


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_pdf(
    db: Session,
    company: Company,
    year: int,
    src_path: Path,
    original_filename: str,
) -> UploadOutcome:
    """把已落盘（FastAPI 临时）的 PDF 搬到 {公司}/pdf/original/ 下，登记 annual_report。"""
    settings = get_settings()
    pdf_dir: Path = settings.REPORT_DATA_PATH / company.name / "pdf" / "original"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(original_filename)
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"

    # 计算 SHA-256（去重判断）
    digest = _sha256_of(src_path)

    # 查重：同公司同年同 SHA
    existing = (
        db.query(AnnualReport)
        .filter(
            AnnualReport.company_id == company.id,
            AnnualReport.year == year,
            AnnualReport.pdf_sha256 == digest,
        )
        .first()
    )
    if existing:
        return UploadOutcome(
            report=existing,
            deduplicated=True,
            message=f"PDF 内容重复（SHA-256 一致），未重新落盘。",
        )

    dest = pdf_dir / safe_name
    # 同名冲突：先读 dest 的 SHA-256 比对
    #   - 一致 → 视为同一文件，跳过复制
    #   - 不一致 → 加 hash 后缀避免覆盖
    if dest.exists():
        if _sha256_of(dest) == digest:
            # 内容完全一致：跳过复制，复用现有 dest
            pass
        else:
            stem = dest.stem
            dest = pdf_dir / f"{stem}_{digest[:8]}.pdf"
            shutil.copy2(src_path, dest)
    else:
        shutil.copy2(src_path, dest)
    rel_path = str(dest.relative_to(settings.REPORT_DATA_PATH))

    # 同公司同年是否已有记录？有则更新（视为换源）；无则插入
    record = (
        db.query(AnnualReport)
        .filter(AnnualReport.company_id == company.id, AnnualReport.year == year)
        .first()
    )
    if record:
        record.pdf_path = rel_path
        record.pdf_sha256 = digest
        record.source = "manual_upload"
        record.parse_status = record.parse_status or "pending"
    else:
        record = AnnualReport(
            company_id=company.id,
            year=year,
            pdf_path=rel_path,
            pdf_sha256=digest,
            source="manual_upload",
            parse_status="pending",
        )
        db.add(record)
    db.commit()
    db.refresh(record)

    return UploadOutcome(
        report=record,
        deduplicated=False,
        message=f"已保存到 {rel_path}",
    )

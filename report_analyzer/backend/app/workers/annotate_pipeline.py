"""业务 MD 标题标注 worker：BackgroundTask 入口。

从 parse_split_pipeline 中剥离的独立步骤。
对当前 (company, year) 的业务报告 MD（``raw/业务报告/{公司}{年份}年年度报告/..._业务报告.md``）
跑 ContextAwareHeadingAnnotator，写回 ``annotation_status='annotated'`` + ``annotated_at``。

调用入口：routers/companies.py 的 POST /companies/{name}/annotate
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.db import session as db_session
from app.models import AnnualReport, Company, ReportRun
from app.services.heading_annotate_service import ContextAwareHeadingAnnotator
from app.workers import progress_bus

_log = logging.getLogger(__name__)


def _update_run(run_id: int, **fields) -> None:
    with db_session.SessionLocal() as s:
        run = s.get(ReportRun, run_id)
        if not run:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        if fields.get("status") in {"done", "failed"} and not run.finished_at:
            run.finished_at = datetime.utcnow()
        s.commit()


def _resolve(rel: str) -> Path:
    return get_settings().REPORT_DATA_PATH / rel


def run_annotate_pipeline(run_id: int, company_id: int, year: int) -> None:
    """对 (company, year) 的业务 MD 执行标题标注。"""
    _update_run(run_id, status="running", current_stage=1)
    progress_bus.publish(run_id, f"开始标注 {year} 年业务 MD", stage=1)

    with db_session.SessionLocal() as s:
        company: Company | None = s.get(Company, company_id)
        if not company:
            _update_run(run_id, status="failed", error=f"公司不存在 id={company_id}")
            progress_bus.publish(
                run_id, f"公司不存在 id={company_id}", stage=1, level="error"
            )
            return
        company_name = company.name

    with db_session.SessionLocal() as s:
        report: AnnualReport | None = (
            s.query(AnnualReport)
            .filter(
                AnnualReport.company_id == company_id,
                AnnualReport.year == year,
            )
            .first()
        )
    if not report:
        _update_run(run_id, status="failed", error=f"{year} 年年报未上传")
        progress_bus.publish(run_id, f"年报未上传: {year}", stage=1, level="error")
        return
    if not report.business_md_path:
        _update_run(
            run_id, status="failed", error="业务报告 MD 未生成，请先 POST /parse-split"
        )
        progress_bus.publish(
            run_id, "业务报告 MD 未生成，请先 POST /parse-split", stage=1, level="error"
        )
        return

    md_path = _resolve(report.business_md_path)
    if not md_path.is_file():
        _update_run(run_id, status="failed", error=f"业务 MD 不存在: {md_path}")
        progress_bus.publish(
            run_id, f"业务 MD 不存在: {md_path}", stage=1, level="error"
        )
        return

    try:
        n_annotated = ContextAwareHeadingAnnotator().annotate_business_md(md_path)
    except Exception as e:
        _log.exception("annotate: 标注失败")
        _update_run(
            run_id,
            status="failed",
            current_stage=1,
            error=f"标注失败: {e}"[:1000],
        )
        progress_bus.publish(run_id, f"标注失败: {e}", stage=1, level="error")
        # 落 annotation_status='failed' 便于排查
        with db_session.SessionLocal() as s:
            ar = s.get(AnnualReport, report.id)
            if ar:
                ar.annotation_status = "failed"
                s.commit()
        return

    with db_session.SessionLocal() as s:
        ar = s.get(AnnualReport, report.id)
        if ar:
            ar.annotation_status = "annotated"
            ar.annotated_at = datetime.utcnow()
            s.commit()

    _update_run(
        run_id,
        status="done",
        current_stage=1,
        final_path=report.business_md_path,
    )
    progress_bus.publish(
        run_id,
        f"业务 MD 标注完成: 改写 {n_annotated} 行",
        stage=1,
        payload={"annotated_lines": n_annotated, "md_path": report.business_md_path},
    )

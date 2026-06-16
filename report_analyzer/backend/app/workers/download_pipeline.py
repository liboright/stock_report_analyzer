"""年报下载 worker：BackgroundTask 入口。

调用入口：routers/reports.py 的 POST /reports/download

流程：
  对每个 year：
    1. progress_bus.publish "开始下载 {company} {year} 年报"
    2. asyncio.run(download_one_year) → 落盘到 raw/{company}/pdf/
    3. pdf_upload_service.upload_pdf() → 算 SHA-256 + 写 annual_report 表
    4. progress_bus.publish "已下载 ... (X MB)"
  全部完成 → _update_run(status="done", final_path=...)
  任意年失败 → publish error 事件 + 继续下一年（不让单年失败影响整体）
  worker 异常 → status="failed", error=堆栈
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import List

from app.db import session as db_session
from app.models import Company, ReportRun
from app.services import annual_report_downloader, pdf_upload_service
from app.services.pdf_upload_service import UploadOutcome
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


def _publish_safe(run_id: int, msg: str, **kwargs) -> None:
    """progress_bus.publish 偶尔会抛（DB 锁等），吞掉不让 worker 崩溃。"""
    try:
        progress_bus.publish(run_id, msg, **kwargs)
    except Exception as e:
        _log.warning("progress_bus.publish 失败: %s", e)


def _register_pdf(
    *,
    run_id: int,
    company_id: int,
    company_name: str,
    year: int,
    outcome,
    stage: int,
    successes: List[str],
    failures: List[str],
) -> None:
    """把下载好的 PDF 注册到 annual_report 表 + 推 SSE 事件。"""
    try:
        with db_session.SessionLocal() as s:
            company_obj = s.get(Company, company_id)
            upload_outcome: UploadOutcome = pdf_upload_service.upload_pdf(
                db=s,
                company=company_obj,
                year=year,
                src_path=outcome.pdf_path,
                original_filename=f"{company_name}{year}年年度报告.pdf",
            )
            rel_path = upload_outcome.report.pdf_path
            if upload_outcome.deduplicated:
                _publish_safe(
                    run_id,
                    f"{year} 年报已存在（SHA-256 一致），未重新落盘",
                    stage=stage,
                )
            else:
                _publish_safe(
                    run_id,
                    f"{year} 年报下载完成：{outcome.pdf_path.name}（{outcome.file_size/1024/1024:.1f} MB）",
                    stage=stage,
                    payload={"pdf_path": rel_path, "size": outcome.file_size, "sha256": outcome.sha256},
                )
            successes.append(rel_path)
    except Exception as e:
        _log.exception("upload_pdf register failed year=%s", year)
        _publish_safe(
            run_id,
            f"{year} 年报下载成功但登记失败：{e}",
            stage=stage,
            level="error",
        )
        failures.append(f"{year}: register failed: {e}")


def run_download_pipeline(run_id: int, company_id: int, years: List[int]) -> None:
    """BackgroundTask 入口。years 倒序（最新优先）。"""
    _update_run(run_id, status="running", current_stage=0)
    _publish_safe(run_id, f"开始下载 {len(years)} 份年报", stage=0)

    with db_session.SessionLocal() as s:
        company: Company | None = s.get(Company, company_id)
        if not company:
            _update_run(run_id, status="failed", error=f"公司不存在 id={company_id}")
            return
        company_name = company.name
        stock_code = company.stock_code

    if not stock_code:
        _update_run(
            run_id,
            status="failed",
            error=f"公司 {company_name} 缺 stock_code，请先在「搜索/上传」页补全",
        )
        _publish_safe(run_id, "公司缺 stock_code，无法下载", stage=0, level="error")
        return

    from app.config import get_settings
    settings = get_settings()
    dest_dir: Path = settings.REPORT_DATA_PATH / company_name / "pdf" / "original"
    dest_dir.mkdir(parents=True, exist_ok=True)

    sorted_years = sorted(set(years), reverse=True)
    successes: List[str] = []
    failures: List[str] = []

    # SSE 走「复用浏览器」批量入口，避免每年新建浏览器触发 WAF
    is_sse = stock_code.startswith(("600", "601", "603", "605", "688"))

    if is_sse and len(sorted_years) > 0:
        # 一次性把多年都下完（一个浏览器会话）；失败时回退逐个
        _update_run(run_id, current_stage=0)
        _publish_safe(run_id, f"[SSE] 复用浏览器批量下载 {len(sorted_years)} 年", stage=0)

        def on_prog(msg: str) -> None:
            _publish_safe(run_id, msg, stage=0)

        try:
            outcomes = annual_report_downloader.download_sse_years_sync(
                stock_code=stock_code,
                years=sorted_years,
                dest_dir=dest_dir,
                company_name=company_name,
                on_progress=on_prog,
            )
        except Exception as e:
            _log.exception("SSE batch download failed, fall back to per-year")
            outcomes = []
            _publish_safe(
                run_id,
                f"批量下载失败，回退逐年重试：{e}",
                stage=0,
                level="error",
            )

        # 处理批量结果
        for i, outcome in enumerate(outcomes):
            year = outcome.year
            stage = i + 1
            _update_run(run_id, current_stage=stage)
            _register_pdf(
                run_id=run_id,
                company_id=company_id,
                company_name=company_name,
                year=year,
                outcome=outcome,
                stage=stage,
                successes=successes,
                failures=failures,
            )

        # 批量失败的年份：逐个回退重试（用单 year 入口）
        missing_years = [y for y in sorted_years if y not in {o.year for o in outcomes}]
        for year in missing_years:
            stage = len(outcomes) + 1
            _update_run(run_id, current_stage=stage)
            _publish_safe(run_id, f"回退重试 {year} 年报", stage=stage)
            try:
                outcome = annual_report_downloader.download_one_year_sync(
                    stock_code=stock_code,
                    year=year,
                    dest_dir=dest_dir,
                    company_name=company_name,
                    on_progress=lambda m: _publish_safe(run_id, m, stage=stage),
                )
                _register_pdf(
                    run_id=run_id,
                    company_id=company_id,
                    company_name=company_name,
                    year=year,
                    outcome=outcome,
                    stage=stage,
                    successes=successes,
                    failures=failures,
                )
            except Exception as e:
                _log.exception("fallback year=%s failed", year)
                _publish_safe(run_id, f"{year} 年报下载失败：{e}", stage=stage, level="error")
                failures.append(f"{year}: {e}")
    else:
        # SZSE / 单 year：原 per-year 逻辑
        for i, year in enumerate(sorted_years):
            stage = i + 1
            _update_run(run_id, current_stage=stage)
            _publish_safe(run_id, f"开始下载 {company_name} {year} 年报", stage=stage)

            def on_prog(msg: str) -> None:
                _publish_safe(run_id, msg, stage=stage)

            try:
                outcome = annual_report_downloader.download_one_year_sync(
                    stock_code=stock_code,
                    year=year,
                    dest_dir=dest_dir,
                    company_name=company_name,
                    on_progress=on_prog,
                )
            except Exception as e:
                _log.exception("download failed year=%s", year)
                _publish_safe(run_id, f"{year} 年报下载失败：{e}", stage=stage, level="error")
                failures.append(f"{year}: {e}")
                continue

            _register_pdf(
                run_id=run_id,
                company_id=company_id,
                company_name=company_name,
                year=year,
                outcome=outcome,
                stage=stage,
                successes=successes,
                failures=failures,
            )

    # 汇总
    summary = f"下载完成：成功 {len(successes)}/{len(sorted_years)}"
    if failures:
        summary += f"，失败 {len(failures)}（{'；'.join(failures)}）"
    _publish_safe(run_id, summary, stage=len(sorted_years), level=("error" if failures else "info"))

    if successes:
        # 取最新成功的 PDF 路径作 final_path（让前端可跳转查看）
        with db_session.SessionLocal() as s:
            run = s.get(ReportRun, run_id)
            if run and run.status != "failed":
                _update_run(
                    run_id,
                    status="done",
                    current_stage=len(sorted_years),
                    final_path=successes[0],
                )
    else:
        # 全部失败
        _update_run(
            run_id,
            status="failed",
            error=f"所有年份均失败：{'；'.join(failures)}",
        )

"""章节切分 + 第三节 H2 拆分 + 财务 MD 复制 worker：BackgroundTask 入口。

复用 ``chapter_split_service``、``section3_split_service``、``finance_copy_service``，分别落：
  - ``md/clean/{公司}{年份}年年报/by_section/*.md`` — 阶段 2.3
  - ``md/clean/{公司}{年份}年年报/by_section/10_第十节_财务报告.md`` — 阶段 2.3.5（新增）
  - ``md/clean/{公司}{年份}年年报/管理层讨论/*.md`` — 阶段 2.4

调用入口：routers/companies.py 的 POST /companies/{name}/chapters

设计约束：
- 必须用 `from app.db import session as db_session`，再用 `db_session.SessionLocal()`。
  直接 `from app.db.session import SessionLocal` 会在 import 时绑定旧 engine，
  conftest fixture 重置 engine 后 worker 拿不到新 SessionLocal。
- 业务报告 MD 必须已生成（``business_md_path`` 非空），由 router 校验。
- 财务报告 MD 必须已生成（``finance_md_path`` 非空），由 worker 校验后做 Step 1.5 复制。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.db import session as db_session
from app.models import AnnualReport, Company, ReportRun
from app.services import chapter_split_service, section3_split_service
from app.services.finance_copy_service import copy_finance_md_to_by_section
from app.workers import progress_bus

_log = logging.getLogger(__name__)


def _update_run(run_id: int, **fields) -> None:
    """通用 update：终态写 `finished_at`。"""
    with db_session.SessionLocal() as s:
        run = s.get(ReportRun, run_id)
        if not run:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        if fields.get("status") in {"done", "failed"} and not run.finished_at:
            run.finished_at = datetime.utcnow()
        s.commit()


def _resolve_path(rel_path: str) -> Path:
    """把相对 REPORT_DATA_PATH 的路径解析为绝对路径。"""
    return get_settings().REPORT_DATA_PATH / rel_path


def run_chapters_pipeline(run_id: int, company_id: int, year: int) -> None:
    """BackgroundTask 入口：串 3 步（章节切分 + 财务 MD 复制 + 第三节 H2 拆分）。"""
    _update_run(run_id, status="running", current_stage=1)
    progress_bus.publish(run_id, f"开始章节切分 {year} 年报", stage=1)

    # 1) 取公司名
    with db_session.SessionLocal() as s:
        company: Company | None = s.get(Company, company_id)
        if not company:
            _update_run(run_id, status="failed", error=f"公司不存在 id={company_id}")
            progress_bus.publish(run_id, f"公司不存在 id={company_id}", stage=1, level="error")
            return
        company_name = company.name

    # 2) 取 AnnualReport
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
        progress_bus.publish(run_id, f"年报未上传: {year} 年", stage=1, level="error")
        return
    if not report.business_md_path:
        _update_run(run_id, status="failed", error="业务报告 MD 未生成")
        progress_bus.publish(
            run_id, "业务报告 MD 未生成，请先 POST /parse-split", stage=1, level="error"
        )
        return

    # ---- Step 1: 章节切分（业务报告 MD → by_section/）----
    _update_run(run_id, current_stage=1)
    progress_bus.publish(run_id, "Step 1/3: 章节切分（by_section/）", stage=1)

    business_md_path = _resolve_path(report.business_md_path)
    if not business_md_path.is_file():
        _update_run(run_id, status="failed", error=f"业务报告 MD 不存在: {business_md_path}")
        progress_bus.publish(
            run_id,
            f"业务报告 MD 不存在: {business_md_path}",
            stage=1,
            level="error",
        )
        return

    try:
        md_text = business_md_path.read_text(encoding="utf-8")
        chapters = chapter_split_service.split_markdown_by_sections(
            md_text=md_text,
            company=company_name,
            year=year,
        )
    except Exception as e:
        _log.exception("chapters: 章节切分失败")
        _update_run(run_id, status="failed", error=f"章节切分失败: {e}"[:1000], current_stage=1)
        progress_bus.publish(run_id, f"章节切分失败: {e}", stage=1, level="error")
        return

    progress_bus.publish(
        run_id,
        f"已切分 {len(chapters)} 个章节到 by_section/",
        stage=1,
        payload={"chapters": [c.path.name for c in chapters]},
    )

    # ---- Step 1.5: 财务 MD 复制到 by_section/10_第十节_财务报告.md ----
    progress_bus.publish(
        run_id,
        "Step 1.5/3: 财务报告 MD 复制到 by_section/10_第十节_财务报告.md",
        stage=1,
        payload={"substage": "finance_copy"},
    )
    if not report.finance_md_path:
        _update_run(
            run_id,
            status="failed",
            current_stage=1,
            error="财务报告 MD 未生成，请先 POST /parse-split",
        )
        progress_bus.publish(
            run_id, "财务报告 MD 未生成，请先 POST /parse-split", stage=1, level="error"
        )
        return
    try:
        finance_target = copy_finance_md_to_by_section(company_name, year)
    except FileNotFoundError as e:
        _update_run(run_id, status="failed", current_stage=1, error=str(e)[:1000])
        progress_bus.publish(run_id, str(e), stage=1, level="error")
        return
    except Exception as e:
        _log.exception("chapters: finance copy failed")
        _update_run(
            run_id,
            status="failed",
            current_stage=1,
            error=f"财务 MD 复制失败: {e}"[:1000],
        )
        progress_bus.publish(
            run_id, f"财务 MD 复制失败: {e}", stage=1, level="error"
        )
        return
    settings = get_settings()
    base = settings.REPORT_DATA_PATH
    try:
        finance_rel = str(finance_target.resolve().relative_to(base)).replace("\\", "/")
    except ValueError:
        finance_rel = str(finance_target)
    progress_bus.publish(
        run_id,
        f"财务报告 MD 已复制: {finance_rel}",
        stage=1,
        payload={"path": finance_rel},
    )

    # ---- Step 2: 第三节 H2 拆分（by_section/03_*.md → 管理层讨论/）----
    _update_run(run_id, current_stage=2)
    progress_bus.publish(run_id, "Step 2/3: 第三节 H2 拆分（管理层讨论/）", stage=2)

    try:
        section3_files = section3_split_service.split_section3(company_name, year)
    except Exception as e:
        _log.exception("chapters: section3 H2 拆分失败")
        _update_run(run_id, status="failed", error=f"section3 拆分失败: {e}"[:1000], current_stage=2)
        progress_bus.publish(run_id, f"section3 拆分失败: {e}", stage=2, level="error")
        return

    progress_bus.publish(
        run_id,
        f"已拆分第三节 {len(section3_files)} 个 H2 到 管理层讨论/",
        stage=2,
        payload={"files": [f.name for f in section3_files]},
    )

    # 全部成功（base 在 Step 1.5 已算过）
    final_rel = ""
    if chapters:
        try:
            final_rel = str(chapters[0].path.resolve().relative_to(base)).replace("\\", "/")
        except ValueError:
            final_rel = str(chapters[0].path)
    _update_run(
        run_id,
        status="done",
        current_stage=2,
        final_path=final_rel,
    )
    progress_bus.publish(run_id, "章节切分+财务复制全部完成", stage=2, level="info")

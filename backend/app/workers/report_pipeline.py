"""报告生成 worker：BackgroundTask 调 stage1 skill（subprocess 跑 claude CLI）。

调用入口：routers/reports.py 的 POST /companies/{name}/reports/generate
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.db import session as db_session
from app.models import Company, ReportRun
from app.services import claude_skill_runner
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


def run_report_pipeline(
    run_id: int,
    company_id: int,
    year: Optional[int],
    skill: str = "stage1_business_understanding",
) -> None:
    """BackgroundTask 入口：调 stage1 → 落盘 → 标 done。"""
    _update_run(run_id, status="running", current_stage=1)
    progress_bus.publish(run_id, f"开始调 {skill} skill", stage=0)

    with db_session.SessionLocal() as s:
        company: Company | None = s.get(Company, company_id)
        if not company:
            _update_run(run_id, status="failed", error=f"公司不存在 id={company_id}")
            return
        company_name = company.name

    try:
        progress_bus.publish(
            run_id,
            f"Step 1/1: 调用 claude skill {skill} (company={company_name}, year={year})",
            stage=1,
        )
        result = claude_skill_runner.run_skill(skill, company_name, year)
        progress_bus.publish(
            run_id,
            f"Skill 完成（{result.elapsed_seconds:.1f}s），产物: {result.output_path.name}",
            stage=1,
            payload={"output": str(result.output_path), "elapsed": result.elapsed_seconds},
        )
    except claude_skill_runner.ClaudeSkillError as e:
        _log.exception("skill runner failed")
        progress_bus.publish(run_id, f"Skill 失败: {e}", stage=1, level="error")
        _update_run(run_id, status="failed", error=str(e))
        return
    except Exception as e:
        _log.exception("unexpected pipeline error")
        progress_bus.publish(run_id, f"未预期错误: {e}", stage=1, level="error")
        _update_run(run_id, status="failed", error=str(e))
        return

    # 成功
    try:
        rel = str(result.output_path.relative_to(Path(__file__).resolve().parents[2]))
    except ValueError:
        rel = str(result.output_path)
    _update_run(
        run_id,
        status="done",
        current_stage=1,
        final_path=rel,
    )
    progress_bus.publish(run_id, "报告生成完成", stage=1)

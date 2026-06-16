"""阶段 3.x 跨年度表格合并 worker：BackgroundTask 入口。

调用入口：routers/companies.py 的 `POST /companies/{name}/tables/merge`

流程：
  1. status='running', current_stage=1 (scan)
  2. 调 ``table_merge_service.scan_and_dispatch`` → 拿到强/弱/unmergeable 分组
  3. current_stage=2 (strong)：逐组发 SSE 进度
  4. current_stage=3 (skill)：弱组 → 逐组调
     ``claude_skill_runner.run_skill_for_table_merge`` 真跑 stage2 skill（subprocess）
     - 成功：long+wide CSV 落盘、SSE phase=skill_done
     - 失败：单组失败不影响其他组，SSE phase=skill_failed（run 仍 done）
  5. current_stage=4 (done)：落 sidecar ``research_file/table/.merge_run_{run_id}.json``，
     路径写到 ``ReportRun.final_path``；前端从 SSE last_event.payload 拿汇总
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from app.config import get_settings
from app.db import session as db_session
from app.models import Company, ReportRun
from app.services import claude_skill_runner, table_merge_service
from app.services.claude_skill_runner import ClaudeSkillError, SkillRunResult
from app.services.table_merge_service import (
    GroupReport,
    SkillTaskSpec,
)
from app.workers import progress_bus

_log = logging.getLogger(__name__)


# ============================================================
# DB helpers
# ============================================================


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
    try:
        progress_bus.publish(run_id, msg, **kwargs)
    except Exception as e:  # noqa: BLE001
        _log.warning("progress_bus.publish 失败: %s", e)


def _resolve_company(company_id: int) -> Optional[Company]:
    with db_session.SessionLocal() as s:
        return s.get(Company, company_id)


# ============================================================
# Sidecar JSON
# ============================================================


def _write_sidecar(out_dir: Path, run_id: int, payload: dict) -> Path:
    """把 run 汇总写到 research_file/table/.merge_run_{run_id}.json。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f".merge_run_{run_id}.json"
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return p


# ============================================================
# 弱组 → skill 真实调用
# ============================================================


def _run_weak_skill(
    run_id: int,
    company_name: str,
    task: SkillTaskSpec,
    skill_name: str,
) -> tuple[SkillTaskSpec, Optional[Path], Optional[Path], Optional[str]]:
    """对单组 weak 调 stage2 skill，返回 (task, long_path, wide_path, error_msg)。

    error_msg 非空 = skill 失败。
    """
    _publish_safe(
        run_id,
        (
            f"[skill start] {task.source_md_stem}/{task.sanitized_title} → "
            f"years={task.years}"
        ),
        stage=3,
        payload={
            "phase": "skill_running",
            "group_key": task.group_key,
            "source_md_stem": task.source_md_stem,
            "sanitized_title": task.sanitized_title,
            "years": task.years,
        },
    )
    try:
        result = claude_skill_runner.run_skill_for_table_merge(
            skill=skill_name,
            company=company_name,
            group_key=task.group_key,
            years=list(task.years),
            csv_paths=list(task.csv_paths),
            timeout_seconds=600,
        )
    except ClaudeSkillError as e:
        _publish_safe(
            run_id,
            f"[skill failed] {task.source_md_stem}/{task.sanitized_title} → {e}",
            stage=3,
            level="error",
            payload={
                "phase": "skill_failed",
                "group_key": task.group_key,
                "error": str(e),
            },
        )
        return task, None, None, str(e)

    long_p, wide_p = result.output_paths[0], result.output_paths[1]
    rel_long = _to_rel(long_p)
    rel_wide = _to_rel(wide_p)
    _publish_safe(
        run_id,
        (
            f"[skill done] {task.source_md_stem}/{task.sanitized_title} → "
            f"{rel_long}, {rel_wide} ({result.elapsed_seconds:.1f}s)"
        ),
        stage=3,
        payload={
            "phase": "skill_done",
            "group_key": task.group_key,
            "source_md_stem": task.source_md_stem,
            "sanitized_title": task.sanitized_title,
            "years": task.years,
            "long_csv": rel_long,
            "wide_csv": rel_wide,
            "elapsed_seconds": result.elapsed_seconds,
        },
    )
    return task, long_p, wide_p, None


def _to_rel(p: Path) -> str:
    base = get_settings().REPORT_DATA_PATH
    try:
        return str(p.relative_to(base)).replace("\\", "/")
    except ValueError:
        return p.as_posix()


# ============================================================
# Worker 入口
# ============================================================


def run_table_merge_pipeline(
    run_id: int,
    company_id: int,
    years: Optional[Sequence[int]] = None,
    *,
    force: bool = False,
    scope: str = "all",
    skill_name: str = "stage2_table_merge",
    skill_timeout_seconds: int = 600,
) -> None:
    """跨年度表格合并 worker。

    Args:
        run_id: ReportRun.id（前端 SSE 订阅 key）
        company_id: Company.id
        years: 要合并的年份列表；None=扫该公司所有有 table 目录的年份
        force: True 时强路径合并前清空 research_file/table/
        scope: 'all' / '8core'（本期都按 'all' 走，预留 8 大类筛选用）
        skill_name: 弱组兜底 skill 名（默认 stage2_table_merge）
        skill_timeout_seconds: 单组 skill 的 subprocess timeout
    """
    _update_run(run_id, status="running", current_stage=1, started_at=datetime.utcnow())
    _publish_safe(
        run_id,
        f"开始跨年合并：years={list(years) if years else 'all'}, force={force}, scope={scope}",
        stage=1,
    )

    # 1) 公司校验
    company = _resolve_company(company_id)
    if not company:
        _update_run(run_id, status="failed", error=f"公司不存在 id={company_id}")
        _publish_safe(run_id, f"公司不存在 id={company_id}", stage=1, level="error")
        return
    company_name = company.name

    settings = get_settings()

    # 2) 调 service：scan + 分组 + 评估 + 强路径合并
    try:
        result = table_merge_service.scan_and_dispatch(
            company=company_name,
            years=list(years) if years else None,
            settings=settings,
            force=force,
        )
    except table_merge_service.TableMergeError as e:
        _update_run(run_id, status="failed", error=f"scan 失败: {e}")
        _publish_safe(run_id, f"scan 失败: {e}", stage=1, level="error")
        return
    except Exception as e:  # noqa: BLE001
        _log.exception("table_merge worker: scan 异常")
        _update_run(run_id, status="failed", error=f"scan 异常: {e}"[:1000])
        _publish_safe(run_id, f"scan 异常: {e}", stage=1, level="error")
        return

    if result.status == "empty":
        _publish_safe(
            run_id,
            f"无可合并表：{result.message or '该公司没有任何已抽表年份'}",
            stage=1,
            level="warn",
        )
        _update_run(
            run_id,
            status="done",
            current_stage=4,
            error=result.message or "empty",
        )
        return

    _publish_safe(
        run_id,
        (
            f"scan 完成: total_csvs={result.total_csvs}, groups={result.total_groups} "
            f"(strong={result.strong_count}, weak={result.weak_count}, "
            f"unmergeable={result.unmergeable_count})"
        ),
        stage=1,
        payload={
            "phase": "scan",
            "total_csvs": result.total_csvs,
            "total_groups": result.total_groups,
            "strong_count": result.strong_count,
            "weak_count": result.weak_count,
            "unmergeable_count": result.unmergeable_count,
        },
    )

    # 3) 强路径：service 已落盘；这里只推 SSE 进度
    _update_run(run_id, current_stage=2)
    for grp in result.groups:
        if grp.status != "strong":
            continue
        _publish_safe(
            run_id,
            (
                f"[strong] {grp.source_md_stem}/{grp.sanitized_title} → "
                f"{grp.years} (col_sim={grp.column_similarity:.2f}, "
                f"row_jac={grp.row_jaccard:.2f})"
            ),
            stage=2,
            payload={
                "phase": "strong",
                "group_key": grp.group_key,
                "source_md_stem": grp.source_md_stem,
                "sanitized_title": grp.sanitized_title,
                "years": grp.years,
                "long_csv": grp.long_csv,
                "wide_csv": grp.wide_csv,
            },
        )

    # 4) 弱路径：逐组真跑 stage2 skill（subprocess 调本机 claude CLI）
    # 跑完后原地回写 group_report（补 long_csv/wide_csv），不重跑 service
    skill_failures: List[str] = []
    _update_run(run_id, current_stage=3)
    for grp in result.groups:
        if grp.status != "weak":
            continue
        # 从 result.skill_tasks 找对应 spec
        task = next(
            (t for t in result.skill_tasks if t.group_key == grp.group_key), None
        )
        if task is None:
            # service 没产出 task spec（理论上不应发生），跳过
            _publish_safe(
                run_id,
                f"[weak] {grp.group_key} → 缺 skill_tasks spec，跳过",
                stage=3,
                level="warn",
                payload={"phase": "skill_skipped", "group_key": grp.group_key},
            )
            continue
        _task, long_p, wide_p, err = _run_weak_skill(
            run_id, company_name, task, skill_name
        )
        if err is not None:
            skill_failures.append(f"{grp.group_key}: {err[:80]}")
            # 保留 service 原 reason（如"列相似度 X"），追加 skill 失败摘要
            grp.reason = f"{grp.reason} | skill failed: {err[:60]}"
            # pending_skill 保留 True（标记为"待用户重试"）
            continue
        # 回写 group_report：标记 skill 已跑、补 long/wide 路径、关掉 pending_skill
        grp.long_csv = _to_rel(long_p) if long_p else None
        grp.wide_csv = _to_rel(wide_p) if wide_p else None
        grp.pending_skill = False
        grp.reason = f"skill 对齐完成（{skill_name}）"

    # 5) 落 sidecar + 终态
    sidecar_payload = {
        "run_id": run_id,
        "company": company_name,
        "years": result.years,
        "total_csvs": result.total_csvs,
        "total_groups": result.total_groups,
        "strong_count": result.strong_count,
        "weak_count": result.weak_count,
        "unmergeable_count": result.unmergeable_count,
        "duration_ms": result.duration_ms,
        "status": "done",
        "groups": [_group_report_to_dict(g) for g in result.groups],
        "skill_tasks": [_skill_task_to_dict(t) for t in result.skill_tasks],
        "skill_failures": skill_failures,
        "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    sidecar_path = _write_sidecar(
        table_merge_service.merged_table_dir(company_name, settings),
        run_id,
        sidecar_payload,
    )
    rel_sidecar = str(
        sidecar_path.relative_to(settings.REPORT_DATA_PATH)
    ).replace("\\", "/")

    summary_msg = (
        f"合并完成: strong={result.strong_count}, weak={result.weak_count}, "
        f"unmergeable={result.unmergeable_count}, skill_failures={len(skill_failures)}, "
        f"duration={result.duration_ms}ms"
    )
    _publish_safe(
        run_id,
        summary_msg,
        stage=4,
        level=("error" if skill_failures and not result.strong_count else "info"),
        payload={
            "phase": "done",
            "sidecar": rel_sidecar,
            "strong_count": result.strong_count,
            "weak_count": result.weak_count,
            "unmergeable_count": result.unmergeable_count,
            "skill_failures": skill_failures,
            "groups": [_group_report_to_dict(g) for g in result.groups],
        },
    )

    _update_run(
        run_id,
        status="done",
        current_stage=4,
        final_path=rel_sidecar,
        error=("; ".join(skill_failures))[:1000] if skill_failures else None,
    )


def _group_report_to_dict(g: GroupReport) -> dict:
    return {
        "group_key": g.group_key,
        "source_md_stem": g.source_md_stem,
        "sanitized_title": g.sanitized_title,
        "status": g.status,
        "years": g.years,
        "column_similarity": g.column_similarity,
        "row_jaccard": g.row_jaccard,
        "long_csv": g.long_csv,
        "wide_csv": g.wide_csv,
        "pending_skill": g.pending_skill,
        "reason": g.reason,
    }


def _skill_task_to_dict(t: SkillTaskSpec) -> dict:
    return {
        "group_key": t.group_key,
        "source_md_stem": t.source_md_stem,
        "sanitized_title": t.sanitized_title,
        "years": t.years,
        "csv_paths": t.csv_paths,
    }

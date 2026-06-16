"""「切分 + 解析」组合 worker：BackgroundTask 入口。

自 2026-06 起：一家公司 N 份待解析 PDF（**所有未完成年份 × 业务/财务**）
**单次 MinerU 批**提交，1 个 batch_id 轮询，N 个 zip 一次性回拉。
进度通过 `progress_bus.publish` 写入 `task_event` 表，前端 SSE 订阅
`GET /tasks/{run_id}/stream`。

调用入口：routers/companies.py 的 POST /companies/{name}/parse-split

断点续跑 / 重启安全：
- 用 `output_md_path.exists()` 兜底：跳过 MD 已落盘的文件（无需查 parse_split_status）
- 进程被 kill 后重提，已落盘的 MD 不重解析；未落盘的进 batch
- 不依赖 `parse_split_status` 字段（DB 状态字段可能与磁盘不一致，仍能正确 skip）

设计约束：
- 必须用 `from app.db import session as db_session`，再用 `db_session.SessionLocal()`。
  直接 `from app.db.session import SessionLocal` 会在 import 时绑定旧 engine，
  conftest fixture 重置 engine 后 worker 拿不到新 SessionLocal。
- `parse_pdfs_to_md_batch` 内部走 `BatchMinerUClient`（单次 API + 单次轮询）。
- 业务 MD 标题归一由 ContextAwareHeadingAnnotator 接手（Stage 1.5）。
- 财务 MD 不标注，原样保留。
- 两份 MD 落点：`raw/业务报告/{公司}{年份}年年度报告/...` 和 `raw/财务报告/{公司}{年份}年年度报告/...`。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import get_settings
from app.db import session as db_session
from app.models import AnnualReport, Company, ReportRun
from app.services import pdf_parse_service
from app.workers import progress_bus

_log = logging.getLogger(__name__)


def _rel_posix(p: Path, base: Path) -> str:
    """p 相对 base 的 POSIX 路径字符串。"""
    try:
        return str(p.resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


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


def _update_report(report_id: int, **fields) -> None:
    """更新 AnnualReport 的 split 解析字段。"""
    with db_session.SessionLocal() as s:
        r = s.get(AnnualReport, report_id)
        if not r:
            return
        for k, v in fields.items():
            setattr(r, k, v)
        s.commit()


def _md_paths(company: str, year: int) -> tuple[Path, Path]:
    """返回 (business_md, finance_md) 落盘绝对路径（按 docs/artifacts.md §1 阶段 2.2）。

      - 业务: REPORT_DATA_PATH/{公司}/md/raw/业务报告/{公司}{年份}年年度报告/..._业务报告.md
      - 财务: REPORT_DATA_PATH/{公司}/md/raw/财务报告/{公司}{年份}年年度报告/..._财务报告.md
    """
    settings = get_settings()
    business_dir = (
        settings.REPORT_DATA_PATH
        / company
        / "md"
        / "raw"
        / "业务报告"
        / f"{company}{year}年年度报告"
    )
    finance_dir = (
        settings.REPORT_DATA_PATH
        / company
        / "md"
        / "raw"
        / "财务报告"
        / f"{company}{year}年年度报告"
    )
    return (
        business_dir / f"{company}{year}年年度报告_业务报告.md",
        finance_dir / f"{company}{year}年年度报告_财务报告.md",
    )


def _resolve_split_pdf(rel_path: str) -> Path:
    """解析切分产物 PDF 的实际位置。"""
    settings = get_settings()
    new = settings.REPORT_DATA_PATH / rel_path
    if new.exists():
        return new
    raise FileNotFoundError(f"PDF 不存在: {rel_path} ({new})")


def _collect_incomplete_items(
    company_id: int,
    trigger_year: int,
    include_other_years: bool,
) -> List[Tuple[int, str, Path, Path, str, int]]:
    """扫该公司所有未完成 (year, kind)，返回 items 列表。

    Returns:
        list of (year, kind, pdf_path, md_out, data_id, report_id)
    """
    with db_session.SessionLocal() as s:
        reports = (
            s.query(AnnualReport)
            .filter(
                AnnualReport.company_id == company_id,
                AnnualReport.split_status == "done",
            )
            .order_by(AnnualReport.year.desc())
            .all()
        )
        # 同时取 company_name（用第一个 report 的 company）
        co = s.get(Company, company_id)
        if not co:
            return []
        company_name = co.name

    items: List[Tuple[int, str, Path, Path, str, int]] = []
    for ar in reports:
        year = ar.year
        if not include_other_years and year != trigger_year:
            continue
        business_md, finance_md = _md_paths(company_name, year)
        # 业务报告：MD 落盘缺失才进 batch
        if ar.other_pdf_path and not business_md.exists():
            items.append(
                (year, "business", _resolve_split_pdf(ar.other_pdf_path),
                 business_md, f"{year}_business", ar.id)
            )
        # 财务报告：MD 落盘缺失才进 batch
        if ar.finance_pdf_path and not finance_md.exists():
            items.append(
                (year, "finance", _resolve_split_pdf(ar.finance_pdf_path),
                 finance_md, f"{year}_finance", ar.id)
            )
    return items


def _publish_item(
    run_id: int,
    msg: str,
    *,
    year: int,
    kind: str,
    stage: Optional[int] = None,
    level: str = "info",
    payload: Optional[dict] = None,
) -> None:
    """发事件，payload 自动塞 file={year}_{kind}。"""
    pl = {"file": f"{year}_{kind}"}
    if payload:
        pl.update(payload)
    progress_bus.publish(run_id, msg, stage=stage, level=level, payload=pl)


def run_parse_split_pipeline(
    run_id: int,
    company_id: int,
    year: int,
    use_mock: bool = False,
    *,
    include_other_years: bool = True,
) -> None:
    """BackgroundTask 入口：单公司 × 跨年批量解析。

    Args:
        run_id: ReportRun.id
        company_id: Company.id
        year: 触发年（用于 ReportRun.year 标记 + final_path 选择）
        use_mock: True → 走 _mock_md_content（不调 MinerU）
        include_other_years: True → 一次 batch 收该公司所有未完成 (year, kind)；
                            False → 只收 year 触发年的（向后兼容旧行为）
    """
    settings = get_settings()

    # 立即推 running
    _update_run(run_id, status="running", current_stage=1)
    progress_bus.publish(
        run_id,
        f"开始切分+解析 {year} 年报（mock={use_mock}, include_other_years={include_other_years}）",
        stage=1,
    )

    # 1) 拿公司名
    with db_session.SessionLocal() as s:
        company: Company | None = s.get(Company, company_id)
        if not company:
            _update_run(run_id, status="failed", error=f"公司不存在 id={company_id}")
            progress_bus.publish(
                run_id, f"公司不存在 id={company_id}", stage=1, level="error"
            )
            return
        company_name = company.name

    # 2) 扫待解析 items（含 disk-existence 兜底）
    items = _collect_incomplete_items(company_id, year, include_other_years)
    if not items:
        _update_run(
            run_id,
            status="done",
            current_stage=2,
            final_path=None,
        )
        progress_bus.publish(
            run_id,
            f"无待解析文件（{year} 年报已全部完成），跳过",
            stage=1,
        )
        return

    # 3) 预检 trigger 年的 split 状态（如果 items 不含 trigger 年但用户触发，要报错）
    if not any(y == year for y, *_rest in items):
        # 用户触发了某年但没扫到 → 可能是 split 没做
        with db_session.SessionLocal() as s:
            ar = (
                s.query(AnnualReport)
                .filter(AnnualReport.company_id == company_id, AnnualReport.year == year)
                .first()
            )
        if not ar or ar.split_status != "done":
            _update_run(
                run_id,
                status="failed",
                error=f"{year} 年年报未切分（split_status={getattr(ar, 'split_status', None)}），请先 POST /split-pdf",
            )
            progress_bus.publish(
                run_id,
                f"{year} 年年报未切分，请先 POST /split-pdf",
                stage=1,
                level="error",
            )
            return

    # 4) 推"开始 batch"事件 + per-file 加入 batch
    file_summary = ",".join(f"{y}_{k}" for y, k, *_ in items)
    progress_bus.publish(
        run_id,
        f"已加入 MinerU batch: {len(items)} 份 PDF（{file_summary}）",
        stage=1,
        payload={"file_count": len(items), "files": [
            {"year": y, "kind": k, "pdf": str(pdf)} for y, k, pdf, _md, _did, _rid in items
        ]},
    )

    # 5) 一次 batch 提交
    try:
        pdf_parse_service.parse_pdfs_to_md_batch(
            [(pdf, md_out, did) for _y, _k, pdf, md_out, did, _rid in items],
            use_mock=use_mock,
        )
    except Exception as e:
        _log.exception("parse_split: batch failed (some MDs may already be on disk)")
        # 无论失败与否，磁盘上已存在的 MD 都算成功；剩余的标记为失败
        _mark_completed_from_disk(run_id, items, company_name)
        # 收集仍缺 MD 的 items 列表，用于错误信息
        with db_session.SessionLocal() as s:
            missing: list[str] = []
            for y, k, _pdf, md_out, _did, _rid in items:
                if not md_out.exists():
                    missing.append(f"{y}_{k}")
        err = f"批量解析失败: {e}（已落盘 {len(items) - len(missing)}/{len(items)}）"
        _update_run(
            run_id,
            status="failed",
            error=err[:1000],
            current_stage=1,
        )
        progress_bus.publish(
            run_id,
            f"批量解析失败: {e}（已落盘 {len(items) - len(missing)}/{len(items)}）",
            stage=1,
            level="error",
            payload={"missing": missing},
        )
        return

    # 6) 全部成功 → 推 per-file 落盘事件 + 更新 DB
    data_base = settings.REPORT_DATA_PATH
    for y, k, _pdf, md_out, did, rid in items:
        if not md_out.exists():
            # 兜底：理论上 batch 成功后必存在
            _publish_item(
                run_id,
                f"MD 缺失（异常）: {did}",
                year=y, kind=k, stage=1, level="error",
            )
            continue
        rel = _rel_posix(md_out, data_base)
        if k == "business":
            _update_report(rid, business_md_path=rel, parse_split_status="business_done")
        else:  # finance
            _update_report(rid, finance_md_path=rel, parse_split_status="done")
        _publish_item(
            run_id,
            f"MD 已落盘: {rel}",
            year=y, kind=k, stage=1,
            payload={"md_path": rel},
        )

    # 7) 全部成功
    trigger_business_md, _ = _md_paths(company_name, year)
    final_path = _rel_posix(trigger_business_md, data_base) if trigger_business_md.exists() else None
    _update_run(
        run_id,
        status="done",
        current_stage=2,
        final_path=final_path,
    )
    progress_bus.publish(
        run_id,
        f"切分+解析全部完成（{len(items)} 份 PDF）；标注已剥离为独立步骤，请触发 POST /annotate",
        stage=2,
        level="info",
    )


def _mark_completed_from_disk(
    run_id: int,
    items: List[Tuple[int, str, Path, Path, str, int]],
    company_name: str,
) -> None:
    """辅助：batch 失败时，按磁盘 MD 存在性逐个落 DB（保持部分成功语义）。"""
    settings = get_settings()
    data_base = settings.REPORT_DATA_PATH
    for y, k, _pdf, md_out, _did, rid in items:
        if not md_out.exists():
            continue
        rel = _rel_posix(md_out, data_base)
        if k == "business":
            _update_report(rid, business_md_path=rel, parse_split_status="business_done")
            _publish_item(
                run_id,
                f"MD 已落盘（partial）: {rel}",
                year=y, kind=k, stage=1,
                payload={"md_path": rel, "partial": True},
            )
        else:
            _update_report(rid, finance_md_path=rel, parse_split_status="done")
            _publish_item(
                run_id,
                f"MD 已落盘（partial）: {rel}",
                year=y, kind=k, stage=1,
                payload={"md_path": rel, "partial": True},
            )

"""任务状态 & SSE 推送（M1 占位：仅返回快照 + 哑 SSE）。"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.deps import get_session
from app.models import ReportRun, TaskEvent
from app.schemas.task import TaskEventRead, TaskStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{run_id}", response_model=TaskStatus)
def get_task_status(run_id: int, db: Session = Depends(get_session)) -> TaskStatus:
    run = db.query(ReportRun).filter(ReportRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
    last = (
        db.query(TaskEvent)
        .filter(TaskEvent.run_id == run_id)
        .order_by(TaskEvent.id.desc())
        .first()
    )
    progress = None
    if run.current_stage is not None:
        # 0..4 -> 0..100
        progress = min(100, max(0, int((run.current_stage + 1) * 20)))
    return TaskStatus(
        run_id=run.id,
        status=run.status,
        current_stage=run.current_stage,
        progress_percent=progress,
        started_at=run.started_at,
        finished_at=run.finished_at,
        final_path=run.final_path,
        error=run.error,
        last_event=TaskEventRead.model_validate(last) if last else None,
    )


@router.get("/{run_id}/stream")
async def stream_task_events(run_id: int, request: Request, db: Session = Depends(get_session)):
    """SSE 推送 task_event 增量。M1 占位：先推送已存在的事件，60s 后心跳保活。

    真实长任务在阶段 3（skill_runner_service）里通过 progress_bus 推新事件。
    """
    run = db.query(ReportRun).filter(ReportRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")

    last_id_sent = 0

    async def event_gen() -> AsyncIterator[dict]:
        nonlocal last_id_sent
        try:
            # 先把已存在的事件一次推完
            with db as _:
                pass
            events = (
                db.query(TaskEvent)
                .filter(TaskEvent.run_id == run_id)
                .order_by(TaskEvent.id)
                .all()
            )
            for ev in events:
                last_id_sent = ev.id
                yield {
                    "event": "task_event",
                    "id": str(ev.id),
                    "data": json.dumps(
                        {
                            "id": ev.id,
                            "stage": ev.stage,
                            "level": ev.level,
                            "message": ev.message,
                            "created_at": ev.created_at.isoformat() if ev.created_at else None,
                        },
                        ensure_ascii=False,
                    ),
                }
            # 心跳保活 + 轮询新事件（M1 阶段 1s 一次）
            while True:
                if await request.is_disconnected():
                    break
                await asyncio.sleep(1.0)
                # 关键：worker 在独立 connection 提交了 ReportRun.status=done / 新 TaskEvent 后，
                # 这个长生命周期 Session 已经在第一次 query 时开了 BEGIN，处于 snapshot 隔离，
                # 仅 expire_all() 还不够（缓存清掉了，但 SELECT 仍在旧事务里看不到外部写入），
                # 必须 rollback() 结束当前事务，下次 query 才会开新事务、看到最新数据。
                db.rollback()
                new_events = (
                    db.query(TaskEvent)
                    .filter(TaskEvent.run_id == run_id, TaskEvent.id > last_id_sent)
                    .order_by(TaskEvent.id)
                    .all()
                )
                for ev in new_events:
                    last_id_sent = ev.id
                    yield {
                        "event": "task_event",
                        "id": str(ev.id),
                        "data": json.dumps(
                            {
                                "id": ev.id,
                                "stage": ev.stage,
                                "level": ev.level,
                                "message": ev.message,
                                "created_at": ev.created_at.isoformat() if ev.created_at else None,
                            },
                            ensure_ascii=False,
                        ),
                    }
                # 终态后多发几次心跳让客户端收到 done，再关闭
                run_now = db.query(ReportRun).filter(ReportRun.id == run_id).first()
                if run_now and run_now.status in {"done", "failed"}:
                    yield {
                        "event": "task_done",
                        "data": json.dumps({"run_id": run_id, "status": run_now.status}),
                    }
                    break
                yield {"event": "ping", "data": "{}"}
        except asyncio.CancelledError:
            raise

    return EventSourceResponse(event_gen())

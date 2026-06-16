"""进度事件总线：把 task_event 写到 SQLite + 推给所有内存订阅者（SSE 用）。

设计：
- 同步 publish：DB 写完即返回（保证 SSE 拿到的事件已落库可查）
- 订阅者维护在模块级 set 中；SSE 路由通过 subscribe/unsubscribe 接入
- 消息体：dict {run_id, stage, level, message, payload, event_id}
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional, Set

from app.db.session import SessionLocal
from app.models import TaskEvent


@dataclass
class ProgressEvent:
    id: int
    run_id: int
    stage: Optional[int]
    level: str
    message: str
    payload: Optional[Dict[str, Any]] = None


_subscribers: Set["asyncio.Queue[ProgressEvent]"] = set()
_loop: Optional[asyncio.AbstractEventLoop] = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """lifespan 启动时把主事件循环记下来，跨线程 publish 时调度到主循环推消息。"""
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue[ProgressEvent]:
    q: asyncio.Queue[ProgressEvent] = asyncio.Queue()
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue[ProgressEvent]) -> None:
    _subscribers.discard(q)


def publish(
    run_id: int,
    message: str,
    level: str = "info",
    stage: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    """同步写 DB 并通知订阅者。返回 event_id。"""
    payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
    with SessionLocal() as s:
        ev = TaskEvent(
            run_id=run_id,
            stage=stage,
            level=level,
            message=message,
            payload_json=payload_json,
        )
        s.add(ev)
        s.commit()
        s.refresh(ev)
        event = ProgressEvent(
            id=ev.id,
            run_id=run_id,
            stage=stage,
            level=level,
            message=message,
            payload=payload,
        )

    _notify(event)
    return event.id


def _notify(event: ProgressEvent) -> None:
    """把事件推到所有订阅者队列。兼容跨线程调用。"""
    if not _subscribers:
        return
    for q in list(_subscribers):
        try:
            if _loop and not _loop.is_closed():
                _loop.call_soon_threadsafe(q.put_nowait, event)
            else:
                q.put_nowait(event)
        except Exception:
            pass


async def stream_for(run_id: int) -> AsyncIterator[ProgressEvent]:
    """async generator：只 yield 指定 run_id 的事件，调用方负责 cancel 与 unsubscribe。"""
    q = subscribe()
    try:
        while True:
            ev = await q.get()
            if ev.run_id == run_id:
                yield ev
                if ev.level in {"error"}:
                    break
    finally:
        unsubscribe(q)

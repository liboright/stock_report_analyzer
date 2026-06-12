import { useEffect, useRef, useState } from "react";

/** SSE 订阅 /api/tasks/{runId}/stream 的 React hook。
 *
 * 后端 SSE 事件：
 *   - event: "task_event", data: { id, stage, level, message, created_at }
 *   - event: "task_done",  data: { run_id, status }
 *   - event: "ping"（心跳，忽略）
 *
 * 详见 backend/app/routers/tasks.py
 */
export interface TaskStreamEvent {
  id: number;
  stage: number | null;
  level: string | null;
  message: string;
  created_at: string | null;
}

export function useTaskStream(runId: number | null) {
  const [events, setEvents] = useState<TaskStreamEvent[]>([]);
  const [status, setStatus] = useState<string>("running");
  const [isDone, setIsDone] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // 每次 runId 变化都重置 state（链式推进时下一个 run 才能再次触发 isDone false→true）
    setEvents([]);
    setStatus("running");
    setIsDone(false);
    if (runId == null) return;
    // EventSource 不支持自定义 header / 鉴权；这里靠浏览器 cookie
    const es = new EventSource(`/api/tasks/${runId}/stream`);
    esRef.current = es;

    es.addEventListener("task_event", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as TaskStreamEvent;
        setEvents((prev) => [...prev, data]);
      } catch {
        // ignore malformed
      }
    });

    es.addEventListener("task_done", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as {
          run_id: number;
          status: string;
        };
        setStatus(data.status);
        setIsDone(true);
      } finally {
        es.close();
        esRef.current = null;
      }
    });

    es.addEventListener("ping", () => {
      // 心跳，忽略
    });

    es.onerror = () => {
      // 浏览器默认会在断线时重连；这里不主动 close。
      // 终端状态由 task_done 事件通知。
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [runId]);

  return { events, status, isDone };
}

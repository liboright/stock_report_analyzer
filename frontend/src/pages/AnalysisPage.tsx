import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  List,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import CompanyYearSelector from "../components/CompanyYearSelector";
import { useCompanyYear } from "../hooks/useCompanyYear";
import { reportsApi } from "../api/reports";
import type { ReportContent } from "../types/api";

interface StreamEvent {
  id: number;
  level?: string | null;
  stage?: number | null;
  message: string;
  created_at: string;
}

export default function AnalysisPage() {
  const { company, year } = useCompanyYear();
  const [runId, setRunId] = useState<number | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [done, setDone] = useState<"running" | "done" | "failed" | null>(null);
  const [content, setContent] = useState<ReportContent | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, []);

  // 完成后拉一次最终内容
  useEffect(() => {
    if (done !== "done" || !runId) return;
    setLoadingContent(true);
    reportsApi
      .getContent(runId)
      .then(setContent)
      .catch(() => {
        /* 409 / 500 已被拦截器处理 */
      })
      .finally(() => setLoadingContent(false));
  }, [done, runId]);

  const start = async () => {
    if (!company) {
      message.warning("请先选择公司");
      return;
    }
    setEvents([]);
    setContent(null);
    setDone("running");
    try {
      const r = await reportsApi.generate({
        company,
        year,
        skill: "stage1_business_understanding",
      });
      setRunId(r.run_id);
      esRef.current?.close();
      const es = new EventSource(`/api/tasks/${r.run_id}/stream`);
      esRef.current = es;
      es.addEventListener("task_event", (ev) => {
        try {
          const payload = JSON.parse((ev as MessageEvent).data) as StreamEvent;
          setEvents((prev) => [...prev, payload].slice(-50));
        } catch {
          /* ignore */
        }
      });
      es.addEventListener("task_done", (ev) => {
        try {
          const payload = JSON.parse((ev as MessageEvent).data) as {
            status: string;
          };
          setDone(payload.status === "done" ? "done" : "failed");
        } catch {
          setDone("done");
        }
        es.close();
      });
      es.onerror = () => {
        message.warning("SSE 连接中断");
        es.close();
      };
    } catch {
      setDone(null);
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <CompanyYearSelector />

      <Card title="生成业务概况 (stage1_business_understanding)">
        <Space>
          <Button
            type="primary"
            onClick={start}
            disabled={!company}
            loading={done === "running"}
          >
            开始生成 {company ? `${company} ${year}` : ""}
          </Button>
          {runId && (
            <Typography.Text type="secondary">Run #{runId}</Typography.Text>
          )}
        </Space>
      </Card>

      {runId && (
        <Card title={`实时进度（最近 ${events.length} 条）`}>
          {events.length === 0 ? (
            <Spin />
          ) : (
            <List
              size="small"
              dataSource={events}
              renderItem={(ev) => (
                <List.Item>
                  <Space>
                    {ev.stage != null && <Tag color="blue">stage {ev.stage}</Tag>}
                    <Tag
                      color={
                        ev.level === "error"
                          ? "red"
                          : ev.level === "warning"
                            ? "orange"
                            : "default"
                      }
                    >
                      {ev.level ?? "info"}
                    </Tag>
                    <span>{ev.message}</span>
                  </Space>
                </List.Item>
              )}
            />
          )}
        </Card>
      )}

      {(done === "done" || content) && (
        <Card title="报告内容">
          {loadingContent ? (
            <Spin />
          ) : content ? (
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                maxHeight: 600,
                overflow: "auto",
                background: "#fafafa",
                padding: 12,
                borderRadius: 4,
                margin: 0,
              }}
            >
              {content.content}
            </pre>
          ) : (
            <Alert type="info" message="暂无内容" />
          )}
        </Card>
      )}

      {done === "failed" && (
        <Alert type="error" showIcon message="生成失败，查看上方事件流" />
      )}
    </Space>
  );
}

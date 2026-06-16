import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  List,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import CompanyYearSelector from "../components/CompanyYearSelector";
import { AnalysisFileTree } from "../components/FileTree";
import { useCompanyYear } from "../hooks/useCompanyYear";
import { useCompany } from "../hooks/useCompanies";
import { companiesApi } from "../api/companies";
import { reportsApi } from "../api/reports";
import type { ReportContent } from "../types/api";
import type { TableMergeGroupSummary } from "../api/companies";

// ============================================================
// SSE 事件类型
// ============================================================

type EventLevel = "info" | "warning" | "warn" | "error" | string;

type EventPhase =
  | "start"
  | "system"
  | "thinking"
  | "text"
  | "tool_use"
  | "tool_result"
  | "result"
  | "raw"
  | "stderr"
  | "timeout"
  | "exit_error"
  | "output_missing"
  | "done"
  | "scan"
  | "strong"
  | "skill_running"
  | "skill_done"
  | "skill_failed"
  | "skill_skipped"
  | "invoke"
  | "output"
  | string;

interface EventPayload {
  phase?: EventPhase;
  kind?: string;
  tool_name?: string;
  tool_use_id?: string;
  is_error?: boolean;
  full?: string;
  input?: Record<string, unknown>;
  content?: unknown;
  years?: number[];
  [k: string]: unknown;
}

interface StreamEvent {
  id: number;
  level?: EventLevel | null;
  stage?: number | null;
  message: string;
  created_at: string;
  payload?: EventPayload | null;
}

interface MergeSummaryPayload {
  phase?: string;
  strong_count?: number;
  weak_count?: number;
  unmergeable_count?: number;
  skill_failures?: string[];
  sidecar?: string;
  groups?: TableMergeGroupSummary[];
}

export default function AnalysisPage() {
  const { company, years, year } = useCompanyYear();
  const sortedYears = useMemo(() => Array.from(new Set(years)).sort((a, b) => b - a), [years]);
  const effectiveYears = sortedYears.length > 0 ? sortedYears : year ? [year] : [];

  // 阶段 1：业务概况（多年份）
  const [runId, setRunId] = useState<number | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [done, setDone] = useState<"running" | "done" | "failed" | null>(null);
  const [content, setContent] = useState<ReportContent | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const eventsScrollRef = useRef<HTMLDivElement | null>(null);

  // 阶段 3.x：跨年表格合并
  const { data: companyDetail } = useCompany(company || undefined);
  const availableYears =
    companyDetail?.annual_reports?.map((r) => r.year).sort((a, b) => b - a) ?? [];
  const [stage2RunId, setStage2RunId] = useState<number | null>(null);
  const [stage2Events, setStage2Events] = useState<StreamEvent[]>([]);
  const [stage2Done, setStage2Done] = useState<
    "running" | "done" | "failed" | null
  >(null);
  const [stage2Summary, setStage2Summary] = useState<MergeSummaryPayload | null>(
    null,
  );
  const [stage2LoadingSummary, setStage2LoadingSummary] = useState(false);
  const [stage2Scope, setStage2Scope] = useState<"all" | "8core">("all");
  const [stage2Force, setStage2Force] = useState(false);
  const [stage2SelectedYears, setStage2SelectedYears] = useState<number[]>([]);
  const stage2EsRef = useRef<EventSource | null>(null);

  useEffect(() => {
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, []);

  useEffect(() => {
    return () => {
      stage2EsRef.current?.close();
      stage2EsRef.current = null;
    };
  }, []);

  // 阶段 3.x：公司 / 顶层 years 变化时，默认勾选所有可用年份
  useEffect(() => {
    if (availableYears.length === 0) return;
    const stillValid =
      stage2SelectedYears.length > 0 &&
      stage2SelectedYears.every((y) => availableYears.includes(y));
    if (!stillValid) {
      setStage2SelectedYears(availableYears);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company, availableYears.join(",")]);

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

  // 进度面板：事件流自动滚到底
  useEffect(() => {
    if (eventsScrollRef.current) {
      eventsScrollRef.current.scrollTop = eventsScrollRef.current.scrollHeight;
    }
  }, [events.length]);

  const start = async () => {
    if (!company) {
      message.warning("请先选择公司");
      return;
    }
    if (effectiveYears.length === 0) {
      message.warning("请至少选择一个年份（顶部年份多选）");
      return;
    }
    setEvents([]);
    setContent(null);
    setDone("running");
    try {
      const r = await reportsApi.generate({
        company,
        years: effectiveYears,  // 关键：传多选年份数组
        skill: "stage1_business_understanding",
      });
      setRunId(r.run_id);
      esRef.current?.close();
      const es = new EventSource(`/api/tasks/${r.run_id}/stream`);
      esRef.current = es;
      es.addEventListener("task_event", (ev) => {
        try {
          const payload = JSON.parse((ev as MessageEvent).data) as StreamEvent;
          setEvents((prev) => [...prev, payload].slice(-200));
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

  // 阶段 3.x：完成后拉一次 last_event.payload_json 拿分组汇总
  useEffect(() => {
    if (stage2Done !== "done" || !stage2RunId) return;
    setStage2LoadingSummary(true);
    companiesApi
      .getMergeSummary(stage2RunId)
      .then((p) => setStage2Summary(p))
      .catch(() => {
        /* 已被拦截器处理 */
      })
      .finally(() => setStage2LoadingSummary(false));
  }, [stage2Done, stage2RunId]);

  const startStage2 = async () => {
    if (!company) {
      message.warning("请先选择公司");
      return;
    }
    if (stage2SelectedYears.length < 1) {
      message.warning("请至少选择一个年份");
      return;
    }
    setStage2Events([]);
    setStage2Summary(null);
    setStage2Done("running");
    try {
      const r = await companiesApi.mergeTables(company, {
        years: stage2SelectedYears,
        scope: stage2Scope,
        force: stage2Force,
      });
      setStage2RunId(r.run_id);
      stage2EsRef.current?.close();
      const es = new EventSource(`/api/tasks/${r.run_id}/stream`);
      stage2EsRef.current = es;
      es.addEventListener("task_event", (ev) => {
        try {
          const payload = JSON.parse((ev as MessageEvent).data) as StreamEvent;
          setStage2Events((prev) => [...prev, payload].slice(-200));
        } catch {
          /* ignore */
        }
      });
      es.addEventListener("task_done", (ev) => {
        try {
          const payload = JSON.parse((ev as MessageEvent).data) as {
            status: string;
          };
          setStage2Done(payload.status === "done" ? "done" : "failed");
        } catch {
          setStage2Done("done");
        }
        es.close();
      });
      es.onerror = () => {
        message.warning("SSE 连接中断");
        es.close();
      };
    } catch {
      setStage2Done(null);
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <CompanyYearSelector />

      <Card title="生成业务概况 (stage1_business_understanding)">
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          <Space wrap align="center">
            <Typography.Text strong>目标年份</Typography.Text>
            <Tooltip
              title={
                effectiveYears.length === 0
                  ? "顶部未选年份，请先在顶部多选年份"
                  : `已选 ${effectiveYears.length} 个年份，会一次性喂给 skill`
              }
            >
              <Tag color={effectiveYears.length > 0 ? "blue" : "default"}>
                {effectiveYears.length > 0
                  ? effectiveYears.join(" / ")
                  : "(未选)"}
              </Tag>
            </Tooltip>
            <Button
              type="primary"
              onClick={start}
              disabled={!company || effectiveYears.length === 0}
              loading={done === "running"}
            >
              开始生成
              {company
                ? ` ${company}（${effectiveYears.length} 年）`
                : ""}
            </Button>
            {runId && (
              <Typography.Text type="secondary">Run #{runId}</Typography.Text>
            )}
          </Space>
          {effectiveYears.length === 0 && company && (
            <Alert
              type="info"
              showIcon
              message="该公司在顶部年份多选里没有可用年份，请到「获取」页补录年报"
            />
          )}
        </Space>
      </Card>

      {runId && (
        <Card
          title={
            <Space>
              <span>实时进度（最近 {events.length} 条）</span>
              {done === "running" && <Spin size="small" />}
              <ProgressSummary events={events} />
            </Space>
          }
          extra={
            events.length > 0 && (
              <Button
                size="small"
                onClick={() => {
                  const lines = events.map(formatEventForCopy).join("\n");
                  navigator.clipboard?.writeText(lines).catch(() => {});
                  message.success("已复制到剪贴板");
                }}
              >
                复制全部
              </Button>
            )
          }
        >
          {events.length === 0 ? (
            <Spin />
          ) : (
            <div
              ref={eventsScrollRef}
              style={{ maxHeight: 520, overflowY: "auto" }}
            >
              <List
                size="small"
                dataSource={events}
                renderItem={(ev) => <EventRow ev={ev} />}
              />
            </div>
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

      {/* ============================================================
          阶段 3.x：跨年表格合并 (stage2_table_merge)
          ============================================================ */}
      <Card title="跨年表格合并 (stage2_table_merge)">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space size="middle" wrap align="center">
            <Typography.Text strong>合并年份</Typography.Text>
            <Select
              mode="multiple"
              placeholder="选择要合并的年份"
              style={{ minWidth: 260 }}
              value={stage2SelectedYears}
              onChange={(v) => setStage2SelectedYears(v)}
              options={availableYears.map((y) => ({
                value: y,
                label: String(y),
              }))}
              maxTagCount={4}
            />
            <Typography.Text strong>范围</Typography.Text>
            <Select
              value={stage2Scope}
              onChange={(v) => setStage2Scope(v)}
              style={{ minWidth: 140 }}
              options={[
                { value: "all", label: "全部" },
                { value: "8core", label: "8 大类核心表" },
              ]}
            />
            <Checkbox
              checked={stage2Force}
              onChange={(e) => setStage2Force(e.target.checked)}
            >
              强制重跑（清空旧产物）
            </Checkbox>
            <Button
              type="primary"
              onClick={startStage2}
              disabled={!company || stage2SelectedYears.length === 0}
              loading={stage2Done === "running"}
            >
              开始合并{" "}
              {company
                ? `${company} ${stage2SelectedYears.join("/")}`
                : ""}
            </Button>
            {stage2RunId && (
              <Typography.Text type="secondary">Run #{stage2RunId}</Typography.Text>
            )}
          </Space>

          {availableYears.length === 0 && company && (
            <Alert
              type="info"
              showIcon
              message="该公司暂无年报记录，请先到「获取」标签录入"
            />
          )}
        </Space>
      </Card>

      {stage2RunId && (
        <Card
          title={
            <Space>
              <span>合并进度（最近 {stage2Events.length} 条）</span>
              {stage2Done === "running" && <Spin size="small" />}
            </Space>
          }
        >
          {stage2Events.length === 0 ? (
            <Spin />
          ) : (
            <List
              size="small"
              dataSource={stage2Events}
              renderItem={(ev) => <EventRow ev={ev} />}
            />
          )}
        </Card>
      )}

      {(stage2Done === "done" || stage2Summary) && (
        <Card title="合并分组报告" extra={mergeSummaryExtra(stage2Summary)}>
          {stage2LoadingSummary ? (
            <Spin />
          ) : stage2Summary ? (
            <Table<TableMergeGroupSummary>
              rowKey="group_key"
              size="small"
              pagination={false}
              scroll={{ x: 1100 }}
              dataSource={stage2Summary.groups ?? []}
              columns={mergeGroupColumns}
            />
          ) : (
            <Alert type="info" message="暂无分组数据" />
          )}
        </Card>
      )}

      {stage2Done === "failed" && (
        <Alert type="error" showIcon message="合并失败，查看上方事件流" />
      )}

      {/* 文件树：分析阶段生成的文件（公司级：研究报告 / 合并表格）。 */}
      <Card title="生成的文件（分析产物：研究报告 / 合并表格）">
        {!company ? (
          <Alert type="info" showIcon message="请先选择公司" />
        ) : (
          <AnalysisFileTree
            key={company}
            company={company}
            year={availableYears[0] ?? null}
          />
        )}
      </Card>
    </Space>
  );
}

// ============================================================
// 事件渲染：按 phase 分色 + 折叠详情
// ============================================================

const PHASE_TAG: Record<string, { color: string; label: string }> = {
  start: { color: "blue", label: "start" },
  system: { color: "default", label: "cli" },
  thinking: { color: "purple", label: "think" },
  text: { color: "green", label: "text" },
  tool_use: { color: "geekblue", label: "tool" },
  tool_result: { color: "cyan", label: "result" },
  result: { color: "gold", label: "done-cli" },
  raw: { color: "default", label: "raw" },
  stderr: { color: "orange", label: "stderr" },
  timeout: { color: "red", label: "timeout" },
  exit_error: { color: "red", label: "exit-err" },
  output_missing: { color: "red", label: "no-output" },
  done: { color: "green", label: "done" },
  scan: { color: "blue", label: "scan" },
  strong: { color: "green", label: "strong" },
  skill_running: { color: "blue", label: "skill" },
  skill_done: { color: "green", label: "skill-ok" },
  skill_failed: { color: "red", label: "skill-fail" },
  skill_skipped: { color: "orange", label: "skip" },
  invoke: { color: "blue", label: "invoke" },
  output: { color: "green", label: "output" },
};

function EventRow({ ev }: { ev: StreamEvent }) {
  const phase = ev.payload?.phase ?? "";
  const tag = PHASE_TAG[phase as string];
  const levelColor =
    ev.level === "error"
      ? "red"
      : ev.level === "warning" || ev.level === "warn"
        ? "orange"
        : "default";

  // 折叠区：thinking / tool_use / tool_result 才有详情
  const hasDetail =
    phase === "thinking" ||
    phase === "text" ||
    phase === "tool_use" ||
    phase === "tool_result" ||
    phase === "result" ||
    phase === "raw";

  return (
    <List.Item style={{ padding: "6px 0" }}>
      <Space direction="vertical" size={2} style={{ width: "100%" }}>
        <Space size={4} wrap>
          {ev.stage != null && <Tag color="blue">stage {ev.stage}</Tag>}
          {tag ? (
            <Tag color={tag.color}>{tag.label}</Tag>
          ) : (
            <Tag color={levelColor}>{ev.level ?? "info"}</Tag>
          )}
          {tag && ev.level && ev.level !== "info" && (
            <Tag color={levelColor}>{ev.level}</Tag>
          )}
          <span style={{ fontSize: 13 }}>{ev.message}</span>
        </Space>
        {hasDetail && (
          <EventDetail ev={ev} />
        )}
      </Space>
    </List.Item>
  );
}

function EventDetail({ ev }: { ev: StreamEvent }) {
  const phase = ev.payload?.phase;
  const full = ev.payload?.full as string | undefined;
  const input = ev.payload?.input as Record<string, unknown> | undefined;
  const content = ev.payload?.content;
  const toolName = ev.payload?.tool_name as string | undefined;
  const subtype = (ev.payload?.subtype as string | undefined) ?? "";
  const isError = !!ev.payload?.is_error;

  if (phase === "thinking" && full) {
    return (
      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "t",
            label: <Typography.Text type="secondary">展开思考</Typography.Text>,
            children: (
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  background: "#f9f0ff",
                  border: "1px solid #d3adf7",
                  padding: 8,
                  borderRadius: 4,
                  fontSize: 12,
                  margin: 0,
                  maxHeight: 360,
                  overflow: "auto",
                }}
              >
                {full}
              </pre>
            ),
          },
        ]}
      />
    );
  }

  if (phase === "text" && full) {
    return (
      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "t",
            label: <Typography.Text type="secondary">展开文本</Typography.Text>,
            children: (
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  background: "#f6ffed",
                  border: "1px solid #b7eb8f",
                  padding: 8,
                  borderRadius: 4,
                  fontSize: 12,
                  margin: 0,
                  maxHeight: 360,
                  overflow: "auto",
                }}
              >
                {full}
              </pre>
            ),
          },
        ]}
      />
    );
  }

  if (phase === "tool_use") {
    return (
      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "i",
            label: (
              <Typography.Text type="secondary">
                工具参数 {toolName ? `(${toolName})` : ""}
              </Typography.Text>
            ),
            children: (
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  background: "#e6f4ff",
                  border: "1px solid #91caff",
                  padding: 8,
                  borderRadius: 4,
                  fontSize: 12,
                  margin: 0,
                  maxHeight: 360,
                  overflow: "auto",
                }}
              >
                {JSON.stringify(input ?? {}, null, 2)}
              </pre>
            ),
          },
        ]}
      />
    );
  }

  if (phase === "tool_result") {
    return (
      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "r",
            label: (
              <Typography.Text type={isError ? "danger" : "secondary"}>
                工具结果{isError ? "（错误）" : ""}
              </Typography.Text>
            ),
            children: (
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  background: isError ? "#fff1f0" : "#e6fffb",
                  border: `1px solid ${isError ? "#ffa39e" : "#87e8de"}`,
                  padding: 8,
                  borderRadius: 4,
                  fontSize: 12,
                  margin: 0,
                  maxHeight: 360,
                  overflow: "auto",
                }}
              >
                {typeof content === "string"
                  ? content
                  : JSON.stringify(content, null, 2)}
              </pre>
            ),
          },
        ]}
      />
    );
  }

  if (phase === "result" && full !== undefined) {
    return (
      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "r",
            label: (
              <Typography.Text type="secondary">
                CLI 终态 {subtype ? `(${subtype})` : ""}
              </Typography.Text>
            ),
            children: (
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  background: "#fffbe6",
                  border: "1px solid #ffe58f",
                  padding: 8,
                  borderRadius: 4,
                  fontSize: 12,
                  margin: 0,
                  maxHeight: 360,
                  overflow: "auto",
                }}
              >
                {full}
              </pre>
            ),
          },
        ]}
      />
    );
  }

  return null;
}

function ProgressSummary({ events }: { events: StreamEvent[] }) {
  const last20 = events.slice(-20);
  const counts = {
    think: last20.filter((e) => e.payload?.phase === "thinking").length,
    tool: last20.filter((e) => e.payload?.phase === "tool_use").length,
    result: last20.filter((e) => e.payload?.phase === "tool_result").length,
    err: last20.filter((e) => e.level === "error").length,
  };
  return (
    <Space size={4}>
      {counts.think > 0 && <Tag color="purple">思考 {counts.think}</Tag>}
      {counts.tool > 0 && <Tag color="geekblue">工具 {counts.tool}</Tag>}
      {counts.result > 0 && <Tag color="cyan">结果 {counts.result}</Tag>}
      {counts.err > 0 && <Tag color="red">错误 {counts.err}</Tag>}
    </Space>
  );
}

function formatEventForCopy(ev: StreamEvent): string {
  const phase = ev.payload?.phase ?? "";
  return `[${ev.created_at}] [${ev.level ?? "info"}] [${phase}] ${ev.message}`;
}

// ============================================================
// 阶段 3.x：合并分组表列定义 + 汇总 chip
// ============================================================

const mergeGroupColumns: ColumnsType<TableMergeGroupSummary> = [
  {
    title: "状态",
    dataIndex: "status",
    width: 100,
    render: (s: TableMergeGroupSummary["status"], row) => {
      const color =
        s === "strong" ? "green" : s === "weak" ? "orange" : "red";
      const label =
        s === "strong" ? "强" : s === "weak" ? "弱" : "不可合";
      return (
        <Space size={4}>
          <Tag color={color}>{label}</Tag>
          {row.pending_skill && (
            <Tooltip title="该组弱匹配，stage2 skill 未成功落盘（待重试）">
              <Tag color="gold">pending</Tag>
            </Tooltip>
          )}
        </Space>
      );
    },
  },
  {
    title: "源 MD 段",
    dataIndex: "source_md_stem",
    width: 110,
  },
  {
    title: "表名",
    dataIndex: "sanitized_title",
    ellipsis: true,
  },
  {
    title: "年份",
    dataIndex: "years",
    width: 110,
    render: (ys: number[]) => (ys || []).join("/"),
  },
  {
    title: "列相似度",
    dataIndex: "column_similarity",
    width: 90,
    render: (v: number) => (v == null ? "-" : v.toFixed(2)),
  },
  {
    title: "行 Jaccard",
    dataIndex: "row_jaccard",
    width: 100,
    render: (v: number) => (v == null ? "-" : v.toFixed(2)),
  },
  {
    title: "长表",
    dataIndex: "long_csv",
    width: 100,
    render: (p: string | null) =>
      p ? <Typography.Text code>{relName(p, "_long.csv")}</Typography.Text> : "-",
  },
  {
    title: "宽表",
    dataIndex: "wide_csv",
    width: 100,
    render: (p: string | null) =>
      p ? <Typography.Text code>{relName(p, "_wide.csv")}</Typography.Text> : "-",
  },
  {
    title: "备注",
    dataIndex: "reason",
    ellipsis: true,
    render: (r: string) => (
      <Tooltip title={r}>
        <span style={{ color: "#888" }}>{r || "-"}</span>
      </Tooltip>
    ),
  },
];

function relName(p: string, _suffix: string): string {
  // 把 "公司/md/research_file/table/05_五_营业收入_long.csv" 截短为 "05_五_营业收入_long.csv"
  return p.split("/").pop() ?? p;
}

function mergeSummaryExtra(s: MergeSummaryPayload | null) {
  if (!s) return null;
  return (
    <Space size={4} wrap>
      <Tag color="green">强 {s.strong_count ?? 0}</Tag>
      <Tag color="orange">弱 {s.weak_count ?? 0}</Tag>
      <Tag color="red">不可合 {s.unmergeable_count ?? 0}</Tag>
      {(s.skill_failures?.length ?? 0) > 0 && (
        <Tooltip title={s.skill_failures!.join("\n")}>
          <Tag color="red">skill 失败 {s.skill_failures!.length}</Tag>
        </Tooltip>
      )}
      {s.sidecar && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          sidecar: {s.sidecar}
        </Typography.Text>
      )}
    </Space>
  );
}

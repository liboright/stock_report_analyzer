import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  List,
  Space,
  Switch,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { useQueryClient } from "@tanstack/react-query";
import CompanyYearSelector from "../components/CompanyYearSelector";
import { useCompanyYear } from "../hooks/useCompanyYear";
import { useTaskStream } from "../hooks/useTaskStream";
import { companiesApi } from "../api/companies";
import type { TaskStreamEvent } from "../hooks/useTaskStream";
import { ParseYearFileTree } from "../components/FileTree";

type Step = 0 | 1 | 2 | 3 | 4 | 5;
const STEP_NAMES: Record<Step, string> = {
  0: "",
  1: "拆分 PDF",
  2: "解析双 PDF",
  3: "业务 MD 标注",
  4: "章节切分 + 财务复制",
  5: "抽取表格",
};

interface ChainTask {
  year: number;
  step: Step;
  force?: boolean;
}

export default function ParsePage() {
  const { company, year, years } = useCompanyYear();
  const qc = useQueryClient();

  const [useMock, setUseMock] = useState(false);
  const [step, setStep] = useState<Step>(0);
  const [currentYear, setCurrentYear] = useState<number | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [events, setEvents] = useState<
    Array<TaskStreamEvent & { stepName: string; yearTag?: number }>
  >([]);
  const [done, setDone] = useState<"running" | "done" | "failed" | null>(null);

  // 文件树当前激活的 Tab year（独立 state，不污染全局 URL year）
  const tabYears =
    years.length > 0 ? [...years].sort((a, b) => b - a) : year ? [year] : [];
  const [activeTabYear, setActiveTabYear] = useState<string>(
    String(tabYears[0] ?? year),
  );
  // 当 years 变化（切公司/复选框变动）时，若当前 activeTabYear 不在新列表里则重置
  useEffect(() => {
    if (tabYears.length === 0) return;
    if (!tabYears.map(String).includes(activeTabYear)) {
      setActiveTabYear(String(tabYears[0]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabYears.join(",")]);

  // 当前 run 的 SSE 流
  const { events: liveEvents, isDone: sseDone, status: sseStatus } =
    useTaskStream(runId);

  // 把 SSE 事件按当前 step / year 标签累加
  useEffect(() => {
    if (liveEvents.length === 0) return;
    setEvents((prev) => {
      const next = [...prev];
      for (const e of liveEvents) {
        if (next.find((x) => x.id === e.id)) continue;
        next.push({
          ...e,
          stepName: STEP_NAMES[step],
          yearTag: currentYear ?? undefined,
        });
      }
      return next.slice(-300);
    });
  }, [liveEvents, step, currentYear]);

  // 队列：一键模式按 (year, step) 顺序消化
  // - 单步模式 chainRef = null
  // - 一键模式 chainRef = { tasks, idx }
  const chainRef = useRef<{ tasks: ChainTask[]; idx: number } | null>(null);

  // 推进队列：advance 当前 task 后跑下一个
  const advance = () => {
    const ctx = chainRef.current;
    if (!ctx) return;
    const nextIdx = ctx.idx + 1;
    if (nextIdx >= ctx.tasks.length) {
      // 全部完成
      setDone("done");
      setStep(0);
      setCurrentYear(null);
      qc.invalidateQueries({ queryKey: ["files", company] });
      chainRef.current = null;
      return;
    }
    chainRef.current = { ...ctx, idx: nextIdx };
    const t = ctx.tasks[nextIdx];
    void runStep(t.step, t.year, true, t.force ?? false);
  };

  // SSE 完成 → 一键模式 advance；单步模式收尾
  useEffect(() => {
    if (!sseDone) return;

    if (!chainRef.current) {
      if (sseStatus === "done") {
        setDone("done");
        setStep(0);
        setCurrentYear(null);
        qc.invalidateQueries({ queryKey: ["files", company] });
      } else {
        setDone("failed");
      }
      return;
    }

    if (sseStatus !== "done") {
      const cur = chainRef.current.tasks[chainRef.current.idx];
      setDone("failed");
      message.error(
        `${cur.year} 年 ${STEP_NAMES[cur.step]} 失败，中止一键解析`,
      );
      chainRef.current = null;
      setStep(0);
      setCurrentYear(null);
      return;
    }
    advance();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sseDone, sseStatus]);

  /**
   * 跑单个步骤；isChained=true 时由队列驱动（force=false 保留断点续跑），
   * isChained=false 时是单年单步即时调用（force=true 强制重跑，仅旧路径用，目前已被
   * runStepMulti 取代）。
   *
   * Step 2 异步：setRunId 后等 SSE done 触发 advance；
   * 同步步骤（1/5）：完成后必须手动 advance（队列模式）。
   */
  const runStep = async (
    s: Step,
    y: number,
    isChained: boolean,
    chainForce = false,
  ) => {
    if (!company) {
      message.warning("请先选择公司");
      return;
    }
    setStep(s);
    setCurrentYear(y);
    if (!isChained) {
      setEvents([]);
      setDone("running");
    }
    const force = isChained ? chainForce : true;
    try {
      if (s === 1) {
        const r = await companiesApi.splitPdf(company, y, force);
        message.success(
          `${y} 年 PDF 切分完成：业务 ${r.other_pdf.split("/").pop()} / 财务 ${r.finance_pdf.split("/").pop()}`,
        );
        if (isChained) {
          advance();
        } else {
          setDone("done");
          setCurrentYear(null);
          qc.invalidateQueries({ queryKey: ["files", company] });
        }
        return;
      }
      if (s === 2) {
        // Step 2 在一键模式默认让后端批收所有未完成年份（include_other_years=true）
        const r = await companiesApi.parseSplit(company, y, useMock, force);
        setRunId(r.run_id);
        return;
      }
      if (s === 3) {
        const r = await companiesApi.annotate(company, y, force);
        setRunId(r.run_id);
        return;
      }
      if (s === 4) {
        const r = await companiesApi.triggerChapters(company, y, force);
        setRunId(r.run_id);
        return;
      }
      if (s === 5) {
        const r = await companiesApi.extractTables(company, y, force);
        message.success(`${y} 年表格抽取完成：${r.total} 张表`);
        if (isChained) {
          advance();
        } else {
          setDone("done");
          setCurrentYear(null);
          qc.invalidateQueries({ queryKey: ["files", company] });
        }
        return;
      }
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? String(err);
      message.error(`${y} 年 Step ${s} 失败: ${detail}`);
      setDone("failed");
      if (isChained) chainRef.current = null;
      setStep(0);
      setCurrentYear(null);
    }
  };

  /**
   * 单步按钮：对「复选框中选中的所有年份」按降序循环跑同一步骤 s。
   * - Step 2 只入队 1 次（include_other_years=true 后端批收所有未完成年）。
   * - 其他步骤逐年入队。
   * 复用一键模式的队列基础设施（chainRef + advance）。
   */
  const runStepMulti = (s: Step) => {
    if (!company) {
      message.warning("请先选择公司");
      return;
    }
    const targetYears = years.length > 0 ? years : [year];
    if (targetYears.length === 0) {
      message.warning("没有可处理的年份");
      return;
    }
    const sortedYears = [...targetYears].sort((a, b) => b - a);
    // 单步按钮 = 显式重跑（force=true），但 Step 2 特殊：
    // 后端 force=true 时强制 include_other_years=false（只跑触发年），
    // 这与「多年批解析」诉求矛盾。Step 2 单步固定 force=false，让后端
    // 自动 include_other_years=true 一次性批收所有未完成年。
    // 副作用：已落盘的年（即使用户想重跑）不会被重新解析，需要先手动清 MD。
    const tasks: ChainTask[] =
      s === 2
        ? [{ year: sortedYears[0], step: 2, force: false }]
        : sortedYears.map((y) => ({ year: y, step: s, force: true }));

    setEvents([]);
    setDone("running");
    chainRef.current = { tasks, idx: 0 };
    const t = tasks[0];
    void runStep(t.step, t.year, true, t.force ?? false);
  };

  /**
   * 一键解析：按 years（降序）展平成 (year, step) 队列。
   * - Step 2 只在第一个 year 入队 1 次（include_other_years=true 后端会一次批收所有未完成年）。
   * - 其他 4 步对每个 year 各入队 1 次。
   */
  const parseAll = async () => {
    if (!company) {
      message.warning("请先选择公司");
      return;
    }
    const targetYears = years.length > 0 ? years : [year];
    if (targetYears.length === 0) {
      message.warning("没有可解析的年份");
      return;
    }
    const sortedYears = [...targetYears].sort((a, b) => b - a);
    const tasks: ChainTask[] = [];
    sortedYears.forEach((y, idx) => {
      tasks.push({ year: y, step: 1, force: false });
      if (idx === 0) {
        // Step 2 全公司批一次
        tasks.push({ year: y, step: 2, force: false });
      }
      tasks.push({ year: y, step: 3, force: false });
      tasks.push({ year: y, step: 4, force: false });
      tasks.push({ year: y, step: 5, force: false });
    });

    setEvents([]);
    setDone("running");
    chainRef.current = { tasks, idx: 0 };
    const t = tasks[0];
    await runStep(t.step, t.year, true, t.force ?? false);
  };

  const splitDisabled = !company || done === "running";
  const parseDisabled = !company || done === "running";
  const annotateDisabled = !company || done === "running";
  const chaptersDisabled = !company || done === "running";
  const tablesDisabled = !company || done === "running";

  // 一键解析按钮文案（提示选中了几个年份）
  const effectiveYears = years.length > 0 ? years : year ? [year] : [];
  const sortedYears = [...effectiveYears].sort((a, b) => b - a);
  const parseAllLabel = company
    ? effectiveYears.length > 1
      ? `一键解析 ${company} (${effectiveYears.length} 个年份: ${sortedYears.join(", ")})`
      : `一键解析 ${company} ${effectiveYears[0] ?? year}`
    : "一键解析";

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <CompanyYearSelector />

      <Card title="触发解析">
        <Space size="middle" direction="vertical" style={{ width: "100%" }}>
          <Space wrap>
            <Space>
              <Typography.Text>使用 mock</Typography.Text>
              <Switch checked={useMock} onChange={setUseMock} />
            </Space>
            <Tooltip title="一键执行：按选中的所有年份循环跑 split → annotate → chapters → tables；Step2 (parse-split) 由后端自动批收所有未完成年份，仅触发 1 次">
              <Button
                type="primary"
                onClick={parseAll}
                disabled={!company}
                loading={done === "running"}
              >
                {parseAllLabel}
              </Button>
            </Tooltip>
          </Space>
          <Space wrap>
            <Typography.Text type="secondary" style={{ marginRight: 8 }}>
              单步操作作用于复选框中选中的所有年份（{effectiveYears.length} 个：
              {sortedYears.join(", ")}）：
            </Typography.Text>
            <Button
              onClick={() => runStepMulti(1)}
              disabled={splitDisabled}
              loading={done === "running" && step === 1}
            >
              1. 拆分 PDF
            </Button>
            <Button
              onClick={() => runStepMulti(2)}
              disabled={parseDisabled}
              loading={done === "running" && step === 2}
            >
              2. 解析双 PDF
            </Button>
            <Button
              onClick={() => runStepMulti(3)}
              disabled={annotateDisabled}
              loading={done === "running" && step === 3}
            >
              3. 业务 MD 标注
            </Button>
            <Button
              onClick={() => runStepMulti(4)}
              disabled={chaptersDisabled}
              loading={done === "running" && step === 4}
            >
              4. 章节切分 + 财务复制
            </Button>
            <Button
              onClick={() => runStepMulti(5)}
              disabled={tablesDisabled}
              loading={done === "running" && step === 5}
            >
              5. 抽取表格
            </Button>
            {runId && (
              <Typography.Text type="secondary">Run #{runId}</Typography.Text>
            )}
            {currentYear && done === "running" && (
              <Tag color="processing">正在处理 {currentYear}</Tag>
            )}
          </Space>
        </Space>
      </Card>

      <Card title="文件树（按年份切换 Tab，解析产物：章节 / 第三节 / 抽取表格）">
        {!company ? (
          <Alert type="info" showIcon message="请先选择公司" />
        ) : tabYears.length === 0 ? (
          <Empty description="该公司暂无年报" />
        ) : (
          <Tabs
            activeKey={activeTabYear}
            onChange={setActiveTabYear}
            items={tabYears.map((y) => ({
              key: String(y),
              label: `${y} 年`,
              children: (
                <ParseYearFileTree
                  key={`${company}-${y}`}
                  company={company}
                  year={y}
                />
              ),
            }))}
          />
        )}
      </Card>

      {events.length > 0 && (
        <Card title={`实时进度（最近 ${events.length} 条）`}>
          <List
            size="small"
            dataSource={events}
            renderItem={(ev) => (
              <List.Item>
                <Space wrap>
                  {ev.yearTag && <Tag color="cyan">{ev.yearTag}</Tag>}
                  {ev.stepName && <Tag color="purple">{ev.stepName}</Tag>}
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
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {ev.created_at
                      ? new Date(ev.created_at).toLocaleTimeString("zh-CN")
                      : ""}
                  </Typography.Text>
                </Space>
              </List.Item>
            )}
          />
          {done === "done" && (
            <Alert
              type="success"
              showIcon
              style={{ marginTop: 12 }}
              message="解析完成，文件树已刷新"
            />
          )}
          {done === "failed" && (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              message="解析失败，查看上方事件流"
            />
          )}
        </Card>
      )}
    </Space>
  );
}

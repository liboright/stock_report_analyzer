import { api } from "./client";
import type { Company, AnnualReport, ReportRun } from "../types/api";

interface CompanyDetail {
  id: number;
  name: string;
  stock_code?: string | null;
  industry?: string | null;
  created_at: string;
  annual_reports: AnnualReport[];
  report_runs: ReportRun[];
}

/** 阶段 2.1：PDF 切分（同步） */
export interface SplitPDFResult {
  company: string;
  year: number;
  finance_pdf: string;
  other_pdf: string;
  finance_start_page: number;
  total_pages: number;
  title_text: string;
}

/** 阶段 2.2：切分+解析+标注组合端点响应（异步 202） */
export interface ParseSplitTriggerResult {
  run_id: number;
  company: string;
  year: number;
  status: string;
  use_mock: boolean;
  business_pdf: string;
  finance_pdf: string;
  annotation_status: string | null;
  message: string;
}

/** 阶段 2.3：章节切分 + 财务复制（异步 202） */
export interface ChaptersTriggerResult {
  run_id: number;
  company: string;
  year: number;
  status: string;
  annotation_status: string | null;
  message: string;
}

/** 阶段 2.2.5：业务 MD 标注（从 parse-split 拆出，异步 202） */
export interface AnnotateTriggerResult {
  run_id: number;
  company: string;
  year: number;
  status: string;
  annotation_status: string | null;
  message: string;
}

/** 阶段 2.5：表格抽取（同步） */
export interface TablesExtractResult {
  company: string;
  year: number;
  total: number;
  sections: Array<{
    section: string;
    count: number;
  }>;
  csv_paths: string[];
  duration_ms: number;
  extract_tables_status: string;
  message: string;
}

/** 阶段 3.x：跨年度表格合并请求 */
export interface TablesMergeRequest {
  /** 要合并的年份；null/undefined = 该公司所有已抽表年份。 */
  years?: number[] | null;
  scope: "all" | "8core";
  force: boolean;
}

/** 阶段 3.x：合并分组摘要（强/弱/unmergeable）。 */
export interface TableMergeGroupSummary {
  group_key: string;
  source_md_stem: string;
  sanitized_title: string;
  status: "strong" | "weak" | "unmergeable";
  years: number[];
  column_similarity: number;
  row_jaccard: number;
  /** 相对 REPORT_DATA_PATH 的 POSIX 路径；未生成时为 null。 */
  long_csv: string | null;
  wide_csv: string | null;
  pending_skill: boolean;
  reason: string;
}

/** 阶段 3.x：跨年度表格合并响应（端点 202 入队）。 */
export interface TablesMergeResult {
  company: string;
  years: number[];
  run_id: number | null;
  total_csvs: number;
  total_groups: number;
  strong_count: number;
  weak_count: number;
  unmergeable_count: number;
  groups: TableMergeGroupSummary[];
  duration_ms: number;
  status: "queued" | "done" | "failed";
  message: string;
}

export const companiesApi = {
  list: () => api.get<Company[]>("/companies").then((r) => r.data),
  get: (name: string) =>
    api.get<CompanyDetail>(`/companies/${encodeURIComponent(name)}`).then((r) => r.data),
  create: (name: string) =>
    api.post<Company>("/companies", { name }).then((r) => r.data),
  // ---- 解析流水线 5 步 ----
  // force=true：强制重跑（单步按钮再点击场景）。
  //   step1: 切分本来就覆盖写，force 仅风格统一
  //   step2: 删当前年份两份 MD + 强制 include_other_years=false
  //   step3: annotate 重置 annotation_status 后再跑（原地改写业务 MD）
  //   step4: 清 by_section/ 和 管理层讨论/{year}/
  //   step5: 清 table/ 目录（CSV append 模式必须清）
  splitPdf: (name: string, year: number, force = false) =>
    api
      .post<SplitPDFResult>(
        `/companies/${encodeURIComponent(name)}/split-pdf`,
        null,
        { params: { year, force } },
      )
      .then((r) => r.data),
  parseSplit: (name: string, year: number, useMock: boolean, force = false) =>
    api
      .post<ParseSplitTriggerResult>(
        `/companies/${encodeURIComponent(name)}/parse-split`,
        null,
        { params: { year, use_mock: useMock, force } },
      )
      .then((r) => r.data),
  annotate: (name: string, year: number, force = false) =>
    api
      .post<AnnotateTriggerResult>(
        `/companies/${encodeURIComponent(name)}/annotate`,
        null,
        { params: { year, force } },
      )
      .then((r) => r.data),
  triggerChapters: (name: string, year: number, force = false) =>
    api
      .post<ChaptersTriggerResult>(
        `/companies/${encodeURIComponent(name)}/chapters`,
        null,
        { params: { year, force } },
      )
      .then((r) => r.data),
  extractTables: (name: string, year: number, force = false) =>
    api
      .post<TablesExtractResult>(
        `/companies/${encodeURIComponent(name)}/tables/extract`,
        null,
        { params: { year, force } },
      )
      .then((r) => r.data),
  /** 阶段 3.x：跨年度表格合并（异步 202 → SSE 进度）。 */
  mergeTables: (name: string, body: TablesMergeRequest) =>
    api
      .post<TablesMergeResult>(
        `/companies/${encodeURIComponent(name)}/tables/merge`,
        body,
      )
      .then((r) => r.data),
  /** 阶段 3.x：跑完后从 ReportRun.last_event.payload_json 拉分组汇总。 */
  getMergeSummary: async (runId: number) => {
    const r = await api.get<{
      run_id: number;
      status: string;
      last_event?: { payload_json?: string | null } | null;
    }>(`/tasks/${runId}`);
    const json = r.data.last_event?.payload_json;
    if (!json) return null;
    try {
      return JSON.parse(json) as {
        phase?: string;
        strong_count?: number;
        weak_count?: number;
        unmergeable_count?: number;
        skill_failures?: string[];
        sidecar?: string;
        groups?: TableMergeGroupSummary[];
      };
    } catch {
      return null;
    }
  },
};

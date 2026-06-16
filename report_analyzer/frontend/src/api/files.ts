import axios from "axios";
import { api } from "./client";

export interface SubsectionFile {
  title: string;
  path: string;
}

export interface ChapterFile {
  section_num: string;
  title: string;
  path: string;
  subsections: SubsectionFile[];
}

export interface Section3File {
  title: string;
  path: string;
}

/** 研究报告类型：'business' = 公司业务概况族（含增量版）；'industry' = 行业分析。 */
export type ResearchKind = "business" | "industry" | "unknown";

export interface ResearchFile {
  title: string;
  path: string;
  kind: ResearchKind;
}

export interface TableCsvFile {
  category: string; // 8 类之一 或 "其他" / "未分类"
  name: string;
  path: string;
}

/** 阶段 3.x 跨年合并产物的一个 group：_long + _wide 一对（可能缺一）。 */
export interface MergedTableFile {
  /** 文件前缀，如 "001_营业收入" */
  group_key: string;
  /** == group_key（保留字段，方便后续从 sidecar 丰富） */
  sanitized_title: string;
  /** 相对 REPORT_DATA_PATH 的 POSIX 路径；不存在时 null */
  long_csv: string | null;
  wide_csv: string | null;
}

export interface FileTree {
  chapters: ChapterFile[];
  section3: Section3File[];
  research: ResearchFile[];
  tables: TableCsvFile[];
  merged_tables: MergedTableFile[];
}

export const filesApi = {
  list: (company: string, year: number) =>
    api
      .get<FileTree>(
        `/companies/${encodeURIComponent(company)}/reports/${year}/files`,
      )
      .then((r) => r.data),
};

/** 把后端返回的 posix 相对路径拼成前端可请求的 URL。中文段独立 encode。 */
export function getFileUrl(relPath: string): string {
  return (
    "/api/static/md/" +
    relPath
      .split("/")
      .map((seg) => encodeURIComponent(seg))
      .join("/")
  );
}

/** csv / 其它非 md 资源走 /api/static/raw/，前缀与 getFileUrl 对称。 */
export function getRawFileUrl(relPath: string): string {
  return (
    "/api/static/raw/" +
    relPath
      .split("/")
      .map((seg) => encodeURIComponent(seg))
      .join("/")
  );
}

/**
 * 给 axios 用的相对路径：不含 /api 前缀（apiQuiet.baseURL="/api" 会自动拼）。
 * 给浏览器 <a> / window.open 用，请用 getFileUrl / getRawFileUrl（要带 /api）。
 */
function getStaticAxiosPath(relPath: string, kind: "md" | "raw"): string {
  const prefix = kind === "md" ? "/static/md/" : "/static/raw/";
  return prefix + relPath.split("/").map(encodeURIComponent).join("/");
}

// 静默 axios 实例：与 api 共用 /api 前缀与超时，但不挂全局 message.error 拦截器。
// 文件预览的 404 等错误由 PreviewDrawer 内部 Alert 展示，避免重复 toast。
const apiQuiet = axios.create({
  baseURL: "/api",
  timeout: 60_000,
});

/**
 * 拉取文件原始文本（utf-8 保留）。用 responseType: "text" + transformResponse 跳过
 * axios 默认的 JSON.parse，避免把纯文本误解析。
 */
export async function fetchText(
  relPath: string,
  kind: "md" | "raw",
): Promise<string> {
  const url = getStaticAxiosPath(relPath, kind);
  const r = await apiQuiet.get<string>(url, {
    responseType: "text",
    transformResponse: [(d) => d],
  });
  return r.data;
}

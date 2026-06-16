import { api } from "./client";
import type { ReportRun, ReportContent } from "../types/api";

export interface DownloadResponse {
  run_id: number;
  company: string;
  years: number[];
  status: string;
  message: string;
}

export const reportsApi = {
  listByCompany: (name: string) =>
    api.get<ReportRun[]>(`/reports/by-company/${encodeURIComponent(name)}`).then((r) => r.data),
  get: (runId: number) => api.get<ReportRun>(`/reports/${runId}`).then((r) => r.data),
  getContent: (runId: number) =>
    api.get<ReportContent>(`/reports/${runId}/content`).then((r) => r.data),
  /**
   * 触发报告生成（异步）。
   * - years 优先于 year（不传 year 时只用 years）
   * - 都不传时后端 fallback 到公司最新可用年份
   */
  generate: (params: {
    company: string;
    year?: number;
    years?: number[];
    skill?: string;
  }) =>
    api
      .post<{
        run_id: number;
        status: string;
        skill: string;
        years: number[];
        message: string;
      }>("/reports/generate", params)
      .then((r) => r.data),
  download: (params: { company: string; years: number[] }) =>
    api
      .post<DownloadResponse>("/reports/download", params)
      .then((r) => r.data),
};

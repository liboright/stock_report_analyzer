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
  generate: (params: { company: string; year?: number; skill?: string }) =>
    api
      .post<{ run_id: number; status: string; skill: string; message: string }>(
        "/reports/generate",
        params,
      )
      .then((r) => r.data),
  download: (params: { company: string; years: number[] }) =>
    api
      .post<DownloadResponse>("/reports/download", params)
      .then((r) => r.data),
};

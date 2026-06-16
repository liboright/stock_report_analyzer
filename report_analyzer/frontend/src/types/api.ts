// 与 backend Pydantic schema 对齐

export interface Company {
  id: number;
  name: string;
  stock_code?: string | null;
  industry?: string | null;
  created_at: string;
}

export interface AnnualReport {
  id: number;
  company_id: number;
  year: number;
  pdf_path: string;
  pdf_sha256?: string | null;
  source?: string | null;
  parse_status: "pending" | "parsing" | "done" | "failed";
  md_path?: string | null;
  parsed_at?: string | null;
  error?: string | null;
  // 切分（财务报告 / 非财务）
  split_status?: "pending" | "splitting" | "done" | "failed" | null;
  finance_pdf_path?: string | null;
  other_pdf_path?: string | null;
  // 切分后双 PDF 解析（业务报告 + 财务报告 各自独立 MD）
  parse_split_status?: "queued" | "business_done" | "done" | "failed" | null;
  business_md_path?: string | null;
  finance_md_path?: string | null;
}

export interface ReportRun {
  id: number;
  company_id: number;
  year?: number | null;
  template?: string | null;
  status: "queued" | "running" | "stage0" | "stage1" | "stage2" | "stage3" | "stage4" | "done" | "failed";
  current_stage?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  final_path?: string | null;
  error?: string | null;
}

export interface ReportContent {
  run_id: number;
  path: string;
  content: string;
}

export interface TaskEvent {
  id: number;
  run_id: number;
  stage?: number | null;
  level?: string | null;
  message: string;
  payload_json?: string | null;
  created_at: string;
}

export interface TaskSnapshot {
  run_id: number;
  status: string;
  current_stage?: number | null;
  error?: string | null;
  events: TaskEvent[];
}

export interface SettingsSnapshot {
  report_base_path: string;
  raw_base_path: string;
  deep_research_path: string;
  script_path: string;
  mapping_path: string;
  db_path: string;
  log_dir: string;
  anthropic_key_set: boolean;
  mineru_key_set: boolean;
  anthropic_model?: string;
  host?: string;
  port?: number;
  cors_origins?: string[];
}

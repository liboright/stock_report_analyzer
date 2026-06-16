# A 股年报分析系统 使用流程

> 适用版本：截至 2026-06-08
> 适用模块：`D:/quant/report_analyzer/`（Web 系统） + `D:/quant/report_data/`（数据与 skill 落盘）
> 复用模块：`D:/quant/report_gen/`（MinerU 解析器）、`D:/quant/deep-research-report/`（共享工具）

## 总览

整个系统按时间顺序分为 **4 个阶段**：

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  1.获取  │ →  │  2.解析  │ →  │  3.分析  │ →  │  4.生成  │
│  PDF    │    │  PDF→MD │    │  MD→结构│    │  深度报告│
└─────────┘    └─────────┘    └─────────┘    └─────────┘
   下载/上传     切分+解析+拆节    表格+业务理解    写作skill串联
```

每个阶段都对应一组**前端操作**和**后端接口/worker**。所有阶段都通过同一个 `Company × Year` 维度组织数据，文件统一落到 `D:/quant/report_data/{公司名}/` 单棵树下（详细布局见 [`docs/artifacts.md`](artifacts.md) §1）。

---

## 阶段 1：获取年报 PDF

**目标**：拿到一家 A 股上市公司指定年份的年报 PDF 原始文件。

提供两种入口，由用户在 `SearchUploadPage` 上选择：

### 1.1 在线下载（自动）

**入口**：`SearchUploadPage` → 「在线下载年报」按钮
**实现方式**：**由 `annual-report-search` skill 提供**，后端通过 Playwright Python 代码驱动 headless Chromium 自动化访问深交所/上交所的公开披露页面，模拟人工翻找年报并下载。

> 严格按 `.claude/skills/annual-report-search/references/{szse,sse}-flow.md` 的 LLM 流程翻成 Playwright Python 代码（headless）。该 skill 本身包含完整的流程说明文档（即 `flow.md`），后端的 `annual_report_downloader.py` 是 skill 流程的 Python 实现。

**两个交易所差异**：

| 交易所 | 代码前缀 | 域名 | WAF | 下载方式 |
|---|---|---|---|---|
| 深交所 (SZSE) | 000/001/002/003/300 | `disc.static.szse.cn` | 无 | 表格行 `<a>` 的 `attachpath` 即 PDF 直链，curl 即可 |
| 上交所 (SSE) | 600/601/603/605/688 | `static.sse.com.cn` | 有 acw_sc__v2 JS 挑战 | 需先 navigate PDF URL 让 WAF 写 cookie，再带 cookie curl |

**后端链路**：

| 步骤 | 模块 | 关键文件 |
|---|---|---|
| ① 交易所识别 | 按 stock_code 前缀分到 SZSE / SSE | `annual_report_downloader.py::detect_exchange` |
| ② Playwright 流程 | SZSE：填代码 → typeahead → 选公告类别=年度报告 → 查询 → 取 attachpath；SSE：填代码 + 选 YEARLY + 搜索 + 遍历结果匹配 stock_code 前缀 | `download_szse` / `_sse_search_page_url` |
| ③ PDF 拉取 | SZSE 直接 curl；SSE 先用 Playwright 拿 WAF cookie 再 curl（session 复用，多年同一次浏览器会话） | `_curl_download` / `_SseSession` |
| ④ 落盘 + 去重 | 写入 `{公司}/pdf/original/{公司}{年份}年年度报告.pdf`（相对 `REPORT_DATA_PATH`），计算 SHA-256 | 同上 |
| ⑤ 落库 | 写 `annual_report` 表：`source='cninfo'`、`parse_status='pending'` | `routers/companies.py::POST /companies/{name}/download` |

**公司名 → 股票代码 映射**：
- 当前通过 `D:/quant/report_data/mapping.json`（`{公司名: stock_code}`）手工维护
- 后期会替换为更完整的股票代码/股票名称本地映射（功能更全，能反向查、按名称模糊匹配等）
- 在新映射上线前，新增公司需要先在 `mapping.json` 中登记 stock_code

**支持范围**：默认下载**最近 5 年**（UI 中可多选）；SSE 多 year 共享同一浏览器会话以减少 WAF 挑战次数。

### 1.2 用户上传（手动）

**入口**：`SearchUploadPage` → 「上传 PDF」按钮（拖拽或选择本地文件 + 年份）
**后端链路**：

| 步骤 | 模块 | 关键文件 |
|---|---|---|
| ① multipart 接收 | FastAPI `UploadFile` | `routers/companies.py::POST /companies/{name}/upload` |
| ② SHA-256 去重 | 读流计算哈希，命中已有 PDF 跳过 | `backend/app/services/pdf_upload_service.py` |
| ③ 落盘 | 同下载路径，统一到 `{公司}/pdf/original/`（详见 [artifacts.md](artifacts.md) §1）| 同上 |
| ④ 落库 | `source='manual_upload'` | 同上 |

**前置条件**：先 `POST /companies {name: "..."}` 创建公司记录（前端「新建公司」Modal）。

---

## 阶段 2：解析 PDF → Markdown

**目标**：把原始 PDF 拆成结构化的 Markdown，供后续分析与生成使用。
**子阶段**：先切分（业务/财务）→ 再用 MinerU 解析 → 再按章节/H2 二次切分 → 同步抽取表格。

### 2.1 PDF 切分（业务报告 / 财务报告）

**原因**：年报常 200+ 页，超出 MinerU API 的 200 页单次上限。
**入口**：`ParsePage` 或「一键切分+解析」按钮
**后端接口**：`POST /companies/{name}/split-pdf`（同步）
**核心实现**：`backend/app/services/pdf_split_service.py`

切分算法（多信号综合判定「第X节 财务报告」一级标题位置）：

| 信号 | 判定条件 | 说明 |
|---|---|---|
| ① 文本 | `第[一二三四五六七八九十百]+节\s*财务报告` 正则 | 排除目录/正文误命中 |
| ② 页顶 | bbox.y < 页高 × 0.30 | 章节标题应靠近页眉 |
| ③ 字号 | 落入该页字号 top-3 之内 | 自适应，不硬限绝对字号 |
| ④ 短行 | ≤ 30 字符 | 排除跨行长段落 |
| ⑤ 居中 | 行中心到页中心距离 < 页宽 × 0.20 | 章节标题应水平居中 |
| 参考 | 目录页码（仅打分用） | 距离目录页码越近的候选分越高 |

**输出**（相对 `REPORT_DATA_PATH`）：
```
{公司名}/pdf/split/
  ├── {原名}_业务报告.pdf   ← 第 1 页 ~ 财务报告起始页前 1 页
  └── {原名}_财务报告.pdf   ← 财务报告起始页 ~ 末页
```
DB 字段：`split_status='done'`，写回 `finance_pdf_path` / `other_pdf_path`（详见 [artifacts.md](artifacts.md) §4）。

### 2.2 MinerU 解析（PDF → MD）

**目标**：对切分后的业务/财务 PDF 调 MinerU 解析为 Markdown，**每份独立落盘**。
**入口**：`POST /companies/{name}/parse-split?year=...&use_mock=false&include_other_years=true`
**核心实现**：
- `backend/app/services/mineru_parser/`（API 客户端 + 处理器 + 标题级别转换）
- `backend/app/services/pdf_parse_service.py::parse_pdfs_to_md_batch`（N 份 PDF 单次 MinerU batch）
- `backend/app/workers/parse_split_pipeline.py`（BackgroundTask 扫描同公司未完成文件并批量解析）
- 复用 `D:/quant/report_gen/report_generator/parser/MinerUOnlineParser`

**批量解析语义**：自 2026-06 起，`include_other_years=true` 默认会把同一家公司所有已切分但未完成的年份一起纳入 1 个 MinerU batch（公司所有未完成年份 × 业务/财务），单次提交、单次轮询，返回后逐文件落盘。

**状态机**（`parse_split_status`）：

| 文件 | 输入 | 输出 | 状态推进 |
|---|---|---|---|
| 业务报告 | `other_pdf_path` | `{公司}/md/raw/业务报告/{公司}{年份}年年度报告/{公司}{年份}年年度报告_业务报告.md` | `pending` → `business_done` |
| 财务报告 | `finance_pdf_path` | `{公司}/md/raw/财务报告/{公司}{年份}年年度报告/{公司}{年份}年年度报告_财务报告.md` | `business_done` → `done` |

**断点续跑**：以 MD 文件是否已落盘为兜底；已存在的输出文件不重复提交 MinerU，即使 DB 状态尚未回填也会跳过。
**进度推送**：每步通过 `progress_bus` 写 `task_event` 表，前端用 SSE（`GET /tasks/{run_id}/stream`）实时拉取；批量解析事件的 `payload.file` 标识具体 `{year}_{kind}`。

> **说明**：除此「切分+解析」组合端点外，还有**单独触发**章节切分 + 第三节 H2 拆的 `POST /companies/{name}/chapters?year=...` 端点，由前端 `ParsePage` 提供。

### 2.3 章节切分（按"第X节"）

仅**业务报告**走此步（财务报告目前不切章节）。
**核心实现**：`backend/app/services/chapter_split_service.py`
**逻辑**：按「第一节」「第二节」... 一级标题，把 Stage 1 的整本 MD 切成 N 份独立章节 MD。
**输出**（相对 `REPORT_DATA_PATH`，详见 [artifacts.md](artifacts.md) §1 树状图）：
```
{公司}/md/clean/{公司}{年份}年年报/by_section/
  ├── 01_第一节 重要提示、目录和释义.md
  ├── 02_第二节 公司简介和主要财务指标.md
  ├── 03_第三节 管理层讨论与分析.md
  ├── 04_第四节 公司治理.md
  └── ... 共 12+ 章
```

### 2.4 第三节二级拆分（按 H2）

仅**第三节 管理层讨论**走此步。
**核心实现**：`backend/app/services/section3_split_service.py` + 复用 `D:/quant/report_analyzer/scripts/split_section3.py`（**注意**：在本项目内，不是 `report_data/scripts/`）
**逻辑**：在 03_管理层讨论.md 内按 H2 标题（`##`）拆分。
**输出**：
```
md/clean/{公司}{年份}年年报/管理层讨论/
  ├── 01_报告期内公司所处行业情况.md
  ├── 02_主营业务分析.md
  ├── 03_研发投入.md
  └── ... 共 5~8 个 H2
```

### 2.5 表格抽取（HTML 表格 → 结构化）

**目标**：从已解析的 MD 中抽出 `<table>` 块，结构化为 `TableInfo`（含表头、合并单元格、单位、报告期年份映射等）。
**当前范围**：仅对**第三节 管理层讨论**的 MD 做表格抽取。
**核心实现**：`backend/app/services/md_table_parser/`（子包，6 个模块）

处理流水线（`extract_tables_from_md` 串起）：

| 步骤 | 模块 | 作用 |
|---|---|---|
| ① 定位 | `title_unit_locator.py` | 找 `<table>` 偏移 + 上方 Markdown 标题 + "单位：xxx" 行 |
| ② 年份归一 | `year_normalizer.py` | "本期/上期/前年" → 具体年份；YoY 列自动检测 |
| ③ 文本清洗 | `text_cleaner.py` | LaTeX 残留 / 方框字符 / 折行合并 |
| ④ 表格解析 | `table_extractor.py` | 委托 `D:/quant/deep-research-report/shared/tools/table_parser.py` 解析 rowspan/colspan |
| ⑤ 装配 | `parser.py` | 归一化 Path、IO/年份错误兜底 |

**未来扩展**：财务报告 MD 的表格抽取暂未做，待财务分析阶段需要时接入。

---

## 阶段 3：分析

**目标**：基于解析后的 Markdown，抽取并形成公司业务理解与行业认知，为最终深度报告准备素材。
**子阶段**：① 公司业务概况 ② 行业分析 ③ 数据分析（TODO） ④ 深度报告（TODO，目前仅前两步）。

### 3.1 公司业务概况 + 行业分析（已实现）

**调用方式**：通过 `stage1_business_understanding` skill（subprocess 调 `claude` CLI 跑 Claude Code skill）。
**核心实现**：
- Skill 定义：`D:/quant/report_data/.claude/skills/stage1_business_understanding/SKILL.md`
- Skill 调度：`backend/app/services/claude_skill_runner.py`
- 后端入口：`POST /reports/generate {company, year, skill: "stage1_business_understanding"}`
- Worker：`backend/app/workers/report_pipeline.py`

**Skill 工作流**（由 SKILL.md 定义，claude CLI 加载执行）：

| 阶段 | 动作 |
|---|---|
| 第一阶段 | 扫描 `{公司}/md/clean/{公司}{年份}年年报/管理层讨论/` 下的年份文件夹，确定可用的年份列表（由近及远） |
| 第二阶段 | 逐年份调用 `report_writer` SubAgent 写 `公司业务概况.md`（增量写作，支持模板驱动） |
| 第三阶段 | 同样方式写 `行业分析.md` |

**产物**（相对 `REPORT_DATA_PATH`，详见 [artifacts.md](artifacts.md) §1）：
```
{公司}/md/research_file/
  ├── {公司}_业务概况.md
  └── {公司}_行业分析.md
```

**调用机制**（`claude_skill_runner.py`）：
1. 后端校验 skill 名是否在 `SUPPORTED_SKILLS` 白名单内
2. `shutil.which("claude")` 拿全路径（Windows 下 claude 是 .cmd，避免 `WinError 2`）
3. 拼命令：`claude -p "/stage1_business_understanding {公司} {年份}" --bare --add-dir ...`
4. 注入 `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` 环境变量
5. 同步阻塞 subprocess，timeout 600s
6. 校验产物文件存在，路径写回 `report_run.final_path`

**约束**：白名单机制可防 skill 名注入；当前只支持 stage1；新增 stage2-4 需等对应 SKILL.md 完善后再扩白名单。

### 3.2 数据分析（TODO）

> 用户原话：「之后再对数据进行分析」，目前未实现。
> 计划基于阶段 2.5 抽出的结构化表格，做多年对比、同比/环比、异常点标注等。

### 3.3 深度报告（TODO，目前仅前两部分完成）

> 用户原话：「最后生成一个深度报告（这部分还没做完，只做了第一部分）」
> 计划串联 stage0（公司业务概况）→ stage1（行业分析）→ stage2（产品分析）→ stage3（财务分析）→ stage4（估值 + 总结）五阶段 skill。

---

## 阶段 4：生成（最终深度报告）

**目标**：把阶段 3 的所有分析素材（业务概况 + 行业分析 + 财务分析 + 估值）整合为一份 `公司_深度报告_{日期}.md`。
**入口**（规划中）：`POST /companies/{name}/reports/generate {template, stages?}`
**产物**（规划中，相对 `REPORT_DATA_PATH`）：
```
{公司}/md/final/{公司}_深度报告_{YYYYMMDD}.md
```
**当前状态**：未实现，等 3.2 / 3.3 完成后才能串联。

---

## 端到端 REST 接口速查

| Method | Path | 用途 | 阶段 |
|---|---|---|---|
| GET/POST | `/companies` | 列/建公司 | 1 |
| POST | `/companies/{name}/upload` | 上传 PDF | 1.2 |
| POST | `/companies/{name}/download` | 下载 PDF | 1.1 |
| POST | `/companies/{name}/split-pdf` | 切分 PDF（业务/财务） | 2.1 |
| POST | `/companies/{name}/parse-split` | 切分+解析组合（已切分前提下） | 2.2 |
| POST | `/companies/{name}/parse` | 旧版：单 PDF 全解析（含章节切分+section3 拆） | 2.2/2.3/2.4 |
| GET | `/companies/{name}/reports/{year}/files` | 列解析产物文件树 | 2.3/2.4 |
| POST | `/reports/generate` | 触发 skill（目前仅 stage1） | 3 |
| GET | `/reports/{run_id}/content` | 读最终 markdown | 3/4 |
| GET | `/tasks/{run_id}/stream` | SSE 实时进度 | 全阶段 |

---

## 数据落盘结构

> **详细路径布局、命名规则、ENV 变量、DB 路径字段语义、URL 映射、迁移对照表** 见 [`docs/artifacts.md`](artifacts.md)（**路径规范唯一事实源**）。本节仅给出"按阶段看产物"的速查：

### 顶层结构

```
D:/quant/report_data/                        ← REPORT_DATA_PATH
├── mapping.json                             ← 公司名→股票代码（临时机制）
├── .claude_state/                           ← SQLite + 日志 + 临时（不动）
└── {公司名}/
    ├── pdf/
    │   ├── original/                        ← 阶段 1：原始 PDF
    │   └── split/                           ← 阶段 2.1：业务/财务 PDF
    └── md/
        ├── raw/                             ← 阶段 2.2：MinerU 解析后
        │   ├── 业务报告/{公司}{年份}年年度报告/
        │   └── 财务报告/{公司}{年份}年年度报告/
        ├── clean/                           ← 阶段 2.3-2.5
        │   └── {公司}{年份}年年报/
        │       ├── by_section/              ← 阶段 2.3
        │       ├── 管理层讨论/               ← 阶段 2.4
        │       └── table/                    ← 阶段 2.5
        ├── research_file/                   ← 阶段 3.1
        │   └── table/                        ← 阶段 3 合并表格
        └── final/                            ← 阶段 4（TODO）
            └── {公司}_深度报告_{YYYYMMDD}.md
```

### 按阶段看产物

| 阶段 | 产物类型 | 相对路径 | 关键文件 |
|---|---|---|---|
| 1 | 原始 PDF | `{公司}/pdf/original/{公司}{年份}年年度报告.pdf` | `services/pdf_upload_service.py` |
| 2.1 | 业务/财务 PDF | `{公司}/pdf/split/..._业务报告.pdf` / `..._财务报告.pdf` | `services/pdf_split_service.py` |
| 2.2 业务 | MD + full.md + images + json | `{公司}/md/raw/业务报告/{公司}{年份}年年度报告/` | `workers/parse_split_pipeline.py` |
| 2.2 财务 | MD + full.md + images + json | `{公司}/md/raw/财务报告/{公司}{年份}年年度报告/` | 同上 |
| 2.3 | 章节 MD | `{公司}/md/clean/{公司}{年份}年年报/by_section/XX_第X节_xxx.md` | `services/chapter_split_service.py` |
| 2.4 | H2 MD | `{公司}/md/clean/{公司}{年份}年年报/管理层讨论/NN_xxx.md` | `services/section3_split_service.py` |
| 2.5 | 表格 CSV | `{公司}/md/clean/{公司}{年份}年年报/table/{类型}.csv` | `services/md_table_parser/` |
| 3.1 | 业务概况/行业分析 | `{公司}/md/research_file/{公司}_业务概况.md` 等 | `services/claude_skill_runner.py` |
| 4 | 最终深度报告 | `{公司}/md/final/{公司}_深度报告_{YYYYMMDD}.md` | （阶段 4 TODO）|

> 所有阶段的具体路径、命名规则、ENV 变量、DB 字段语义以 [`docs/artifacts.md`](artifacts.md) 为准。

---

## 状态机速查

### `annual_report.parse_status`
```
pending → parsing → done
                  ↘ failed
```

### `annual_report.split_status`（阶段 2.1）
```
(empty) → done
```

### `annual_report.parse_split_status`（阶段 2.2）
```
pending → business_done → done
        ↘ failed
```

### `report_run.status`（任意 pipeline）
```
queued → running → done
                 ↘ failed
                 ↘ stage_N（中间阶段，status 仍为 running，current_stage 推进）
```

---

## 关键文件索引

| 阶段 | 关键文件 |
|---|---|
| 1.1 下载 | Skill 流程文档 `.claude/skills/annual-report-search/references/{szse,sse}-flow.md` + Python 实现 `backend/app/services/annual_report_downloader.py`（Playwright + curl）|
| 1.1 公司查询 | `backend/app/services/company_search_service.py`（读 `mapping.json`） |
| 1.2 上传 | `backend/app/services/pdf_upload_service.py` |
| 2.1 切分 | `backend/app/services/pdf_split_service.py` |
| 2.2 解析 | `backend/app/workers/parse_split_pipeline.py` + `backend/app/services/mineru_parser/` |
| 2.3 章节 | `backend/app/services/chapter_split_service.py` |
| 2.4 section3 | `backend/app/services/section3_split_service.py` + `scripts/split_section3.py` |
| 2.5 表格 | `backend/app/services/md_table_parser/` |
| 3.1 skill | `D:/quant/report_data/.claude/skills/stage1_business_understanding/SKILL.md` + `backend/app/services/claude_skill_runner.py` + `backend/app/workers/report_pipeline.py` |
| 全局 | `backend/app/workers/progress_bus.py`（SSE 进度总线） |
| 前端 | `frontend/src/pages/SearchUploadPage.tsx` / `ParsePage.tsx` / `AnalysisPage.tsx` / `ReportPage.tsx` |

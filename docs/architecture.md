# A 股年报「搜索+解析+深度报告」Web 系统 实施方案

## Context

`D:/Quant/report_database/` 现已积累宁德时代 2023/2024/2025 三年的年报 Markdown 与 1 份终稿深度报告（CLI 模式产出）。本计划在此基础上构建**浏览器 Web 系统**，覆盖三大模块：

1. **年报搜索** — 输入公司名 → 检索/下载最近 3 年 PDF，或手动上传
2. **年报解析** — PDF → MD → 按"第X节"切分 → 第三节二级标题拆分
3. **年报分析** — 串联现有 5 阶段 Skill 流水线，输出最终深度报告

目标成果：单用户本地浏览器一键完成「建公司 → 下/传 PDF → 自动解析 → 生成报告 → 在线阅读」端到端闭环，复用现有 Python 解析与 Skill 能力（不重写）。

## 关键选型（已与用户对齐）

| 维度 | 选型 | 理由 |
|---|---|---|
| 前端 | **React 18 + Vite + TypeScript + Ant Design 5** | 中文组件成熟、与 Python 后端解耦 |
| 状态 | TanStack Query v5（服务端）+ Zustand（本地） | SSE 集成友好 |
| 后端 | **FastAPI 单进程单 worker** + BackgroundTasks | 单用户本地，无须 Redis/Celery |
| 持久化 | **SQLite**（`D:/Quant/report_database/.claude_state/state.db`） | 零运维、足够 |
| 实时通信 | **SSE**（`sse-starlette`） | 单向推送、断线重连原生支持 |
| 任务队列 | FastAPI BackgroundTasks + 启动时扫描 SQLite 自动续跑 | 单用户够用 |
| 公司名映射 | 手工维护 `mapping.json`（`{name: stockCode}`） | 简、可控、零外部 API 依赖 |
| 解析 | 复用 `D:/Quant/report_gen/report_generator/parser/MinerUOnlineParser` | 已实现，PYTHONPATH 引用 |
| LLM | 复用 Anthropic SDK（`claude-3-5-sonnet-20241022`） | 与现有 Skill 一致 |
| 部署 | `run.bat` 一键启后端 + `npm run dev` 启前端 | 单机本地，无 Docker |

## 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│  Browser  http://localhost:5173  (React + Vite)              │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST + SSE
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  FastAPI 单进程  uvicorn :8000                                │
│  routers/  →  services/  →  workers/  (BackgroundTasks)       │
│  + 内存 asyncio.Queue 串行执行                                │
└─────────┬─────────────┬─────────────────┬────────────────────┘
          ▼             ▼                 ▼
       SQLite       日志文件       直接 import：
       state.db     .claude_state/  • deep-research-report/shared/tools/**
                       logs/        • report_gen/report_generator/parser/**
                                    • report_database/scripts/split_section3.py
          │             │                 │
          ▼             ▼                 ▼
       ┌──────────────────────────────────────┐
       │  D:/Quant/report_database/           │
       │   raw/{公司}/pdf/   raw/{公司}/md/   │
       │   md/{公司}/input/  md/{公司}/output/│
       │   mapping.json  .claude_state/       │
       └──────────────────────────────────────┘
```

## 后端设计 `D:/Quant/report_database/backend/`

### 目录结构
```
backend/
├── app/
│   ├── main.py                  # FastAPI 入口、CORS、SSE
│   ├── config.py                # pydantic-settings 读 .env
│   ├── deps.py                  # DB session 依赖注入
│   ├── routers/
│   │   ├── companies.py         # /companies/...
│   │   ├── reports.py           # /reports/...
│   │   ├── tasks.py             # /tasks/{id} + SSE /stream
│   │   └── settings.py          # /settings
│   ├── services/                # 业务层（薄包装）
│   │   ├── company_search_service.py   # 查 mapping.json
│   │   ├── pdf_download_service.py     # 巨潮 URL 构造 + httpx
│   │   ├── pdf_upload_service.py       # multipart 落盘 + SHA-256 去重
│   │   ├── pdf_parse_service.py        # import MinerUOnlineParser
│   │   ├── chapter_split_service.py    # import split_by_sections
│   │   ├── section3_split_service.py   # import split_section3
│   │   ├── index_build_service.py      # import scan_company_reports
│   │   ├── skill_runner_service.py     # ★ 5 阶段串联核心
│   │   └── report_service.py           # 列/读 final/*.md
│   ├── workers/
│   │   ├── task_dispatcher.py   # asyncio.Queue + 串行
│   │   ├── parse_pipeline.py    # 下载→解析→拆节→section3
│   │   ├── report_pipeline.py   # 串 stage0→4
│   │   └── progress_bus.py      # 任务事件总线
│   ├── models/                  # SQLAlchemy ORM
│   ├── schemas/                 # Pydantic v2
│   └── db/
│       ├── session.py
│       └── migrations.py        # 启动建表
├── tests/
├── pyproject.toml
├── .env.example
└── run.sh / run.bat
```

### Service 层复用映射（关键）

| 新建 Service | 直接 import 复用 | 文件位置 |
|---|---|---|
| `company_search_service` | `search_engine.search_company()` 或读 `mapping.json` | `D:/Quant/deep-research-report/shared/tools/annual_report_search/` |
| `pdf_download_service` | httpx 调 `http://www.cninfo.com.cn/finalpage/{date}/{id}.PDF` | — |
| `pdf_parse_service` | `MinerUOnlineParser(pdf_path).parse()` | `D:/Quant/report_gen/report_generator/parser/mineru_online_parser.py` |
| `chapter_split_service` | `MinerUOnlineParser.split_by_sections()` | 同上（**已实现**） |
| `section3_split_service` | `scripts/split_section3.py::split_section3()` | `D:/Quant/report_database/scripts/split_section3.py` |
| `heading_annotate_service` | 上下文感知两链标注（v2，原地覆盖 `*_业务报告.md` 的 `#` 数） | `backend/app/services/heading_annotate_service.py` |
| `index_build_service` | `shared.tools.annual_report_reader.scan.scan_company_reports()` | `D:/Quant/deep-research-report/shared/tools/annual_report_reader/scan.py` |
| `skill_runner_service` | 各 stage Python 模块直接 import | `D:/Quant/deep-research-report/shared/tools/` |
| `report_service` | 读 `md/{公司}/output/final/*.md` | — |

**`REPORT_BASE_PATH` 已是 `D:\quant\report_database\md`**（见 `shared/tools/annual_report_search/constants.py:6`），与目标路径一致，**无需改动**。

### 启动路径注入（`app/config.py::lifespan`）
```python
sys.path.insert(0, settings.DEEP_RESEARCH_PATH)   # D:/Quant/deep-research-report
sys.path.insert(0, settings.REPORT_GEN_PATH)      # D:/Quant/report_gen/report_generator
sys.path.insert(0, settings.SCRIPT_PATH)          # D:/Quant/report_database/scripts
```

### SQLite Schema 草案
```sql
CREATE TABLE company (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  stock_code TEXT, industry TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE annual_report (
  id INTEGER PRIMARY KEY,
  company_id INTEGER REFERENCES company(id),
  year INTEGER NOT NULL,
  pdf_path TEXT NOT NULL, pdf_sha256 TEXT,
  source TEXT,                    -- 'cninfo'|'manual_upload'
  parse_status TEXT,              -- pending|parsing|done|failed
  md_path TEXT,
  UNIQUE(company_id, year));

CREATE TABLE report_run (
  id INTEGER PRIMARY KEY,
  company_id INTEGER REFERENCES company(id),
  year INTEGER,                   -- NULL=跨年
  template TEXT DEFAULT 'investment_report',
  status TEXT,                    -- queued|running|stage0..4|done|failed
  current_stage INTEGER,
  started_at TIMESTAMP, finished_at TIMESTAMP,
  final_path TEXT, error TEXT);

CREATE TABLE task_event (
  id INTEGER PRIMARY KEY,
  run_id INTEGER REFERENCES report_run(id),
  stage INTEGER, level TEXT, message TEXT,
  payload_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX idx_task_event_run ON task_event(run_id, id);
```

### 关键 REST 端点

| Method | Path | 用途 |
|---|---|---|
| GET  | `/companies` | 列出所有公司 |
| POST | `/companies` | 注册公司 `{name}` → 自动查 mapping.json |
| GET  | `/companies/{name}` | 详情（年报+报告+状态） |
| POST | `/companies/{name}/download` | 触发下载近3年 `{years?}` → `task_id` |
| POST | `/companies/{name}/upload` | multipart `file, year` |
| POST | `/companies/{name}/parse` | 触发 PDF→MD→拆节 `{year}` → `task_id` |
| POST | `/companies/{name}/reports/generate` | `{year, template, stages?}` → `run_id` |
| GET  | `/companies/{name}/reports` | 列出该公司报告 |
| GET  | `/reports/{run_id}` | 报告元信息 + 事件 |
| GET  | `/reports/{run_id}/content` | 报告 markdown 文本 |
| GET  | `/tasks/{task_id}` | 轮询任务快照 |
| GET  | `/tasks/{task_id}/stream` | **SSE 实时进度**（`text/event-stream`） |
| GET  | `/settings` / `PUT /settings` | 读/改可调项 |

## 前端设计 `D:/Quant/report_database/frontend/`

### 技术栈
- Vite 5 + React 18 + TypeScript
- `react-router-dom` v6 路由
- `@tanstack/react-query` v5 服务端状态
- `zustand` 本地状态
- `antd` v5 UI（中文友好）
- `axios` + `EventSource`（SSE）
- `react-markdown` + `remark-gfm` + `rehype-highlight` 渲染报告

### 页面与路由
| 路径 | 页面 | 关键交互 |
|---|---|---|
| `/` | **工作台** | 公司列表、状态徽标（pending/parsing/ready）、「新建公司」 |
| `/companies/:name` | **公司详情** | Tabs：年报 / 报告 / 元数据；按钮：下载/上传/解析/生成报告 |
| `/companies/:name/reports/new` | **报告生成向导** | Step1 选年份 → Step2 选模板 → Step3 SSE 实时进度 stepper + 日志流 → Step4 跳阅读器 |
| `/reports/:runId` | **报告阅读器** | Markdown 渲染、章节锚点、下载 |
| `/settings` | **设置** | API Key 状态、路径展示、mapping.json 编辑器 |

### SSE Hook（关键）
`src/hooks/useTaskStream.ts`：订阅 `/tasks/{id}/stream`，把 `task_event` 推到 React Query cache（自动 invalidate 公司详情）。

## 与现有代码集成要点

1. **三套 .claude/skills/ 重复去重**：
   - 保留 `D:/Quant/deep-research-report/.claude/skills/` 一份（Claude Code 加载约定源）
   - 删除 `D:/Quant/deep-research-report/skills/` 与 `D:/Quant/report_database/.claude/skills/`
   - 后端**不读 SKILL.md**，只 import `shared/tools/` 下的 Python 实现
2. **不重写 CLI**：现有 `scan_company_reports()` / `split_section3()` / `MinerUOnlineParser` 已是可 import 函数，service 层加 thin wrapper
3. **路径变量**统一在 `.env`：
   ```
   REPORT_BASE_PATH=D:/Quant/report_database/md
   RAW_BASE_PATH=D:/Quant/report_database/raw
   DEEP_RESEARCH_PATH=D:/Quant/deep-research-report
   REPORT_GEN_PATH=D:/Quant/report_gen/report_generator
   SCRIPT_PATH=D:/Quant/report_database/scripts
   DB_PATH=D:/Quant/report_database/.claude_state/state.db
   ANTHROPIC_API_KEY=sk-ant-...
   MINERU_API_KEY=...
   HOST=127.0.0.1  PORT=8000
   ```

## 实施里程碑（6 阶段，1-2 周/阶段）

### 阶段 1：后端骨架 + 公司 CRUD + 上传/下载
- 搭 `backend/`、SQLite、`.env`、CORS
- `company_search_service`（读 `mapping.json`）
- `pdf_download_service`（巨潮 URL + httpx）
- `pdf_upload_service`（multipart + SHA-256 去重）
- `pytest` 覆盖：建公司、上传、查询、SHA-256 去重
- **验证**：`curl POST /companies → POST /upload → GET /companies/{name}` 闭环

### 阶段 2：PDF 解析流水线
- 接入 `MinerUOnlineParser.parse()`，MD 落 `raw/{公司}/md/{年}.md`
- **接入 `ContextAwareHeadingAnnotator.annotate_business_md()` 修正 `#` 数**（见下文两链模型）→ MD 落 `raw/{公司}/md/raw/业务报告/{年}/业务报告.md`
- 接入 `split_by_sections()` 按"第X节"切到 `md/{公司}/input/`
- 接入 `split_section3.py` 拆 section3 到 `output/mid_file/`
- `POST /parse` + 进度事件
- **验证**：拿宁德时代 2023 真 PDF 跑通，校验 `input/` 12+ 章节 + `mid_file/` 5-8 二级标题

### 标题标注 `heading_annotate_service` —— 两链模型（v2）

> 解决：MinerU 把所有候选标题统一加 1 个 `#`（全变 H1），下游 `chapter_split_service` / `section3_split_service` 无法直接消费。两链模型按整份报告**是否含 `（一）/（二）/（三）` 这一层**决定走哪条链。

**两条链**（以 `三、报告期内公司从事的业务` 为 anchor probe）：

| 链 | 触发 | L1 | L2 | L3 | L4 | L5 | L6 |
|---|---|---|---|---|---|---|---|
| A | scope 内有 `（一）` | `第X节` | `一、` | `（一）` | `1、/1./1 ` | `（1）/（1).` | `1）/1).` |
| B | scope 内无 `（一）` | `第X节` | `一、` | `1、/1./1 ` | `（1）/（1).` | `1）/1).` | — |

**算法 4 步**（详见 `workspace/heading_annotate/heading_annotate_service.py` + `workspace/requirements.md §2`）：

1. **anchor 定位** — 找首个 `#` 行 + 行体含「报告期内公司从事的业务」
2. **链决策** — anchor 管辖范围（到下一个 `第X节`）内是否出现 `（一）/（二）/（三）`
3. **two-pass 改写** — 按"链的 level_table + 当前行体样式"动态判定每行 `#` 数（最浅命中）
4. **同级序号校验** — 首项必为 1、连续递增、父级换则子级重置；anchor/probe 范围内违规抛 `ValueError`，普通区域违规行去 `#` 变正文

**等价族**（不影响层级）：`1、` / `1.` / `1 ` 全判 L3；`（1）` / `（1).` / `（1) ` 全判 L4；`1）` / `1).` / `1) ` 全判 L5。

**非子标题**：`A./B./C.`、空 `#` 行、inline-punct 结尾行 → 去掉 `#` 变正文。

**兼容契约**：`annotate_text(md) -> (new, count)` / `annotate_business_md(path) -> count`（v1 API 不变，worker `parse_split_pipeline.py:287` 无须改动）。

### 阶段 3：5 阶段 Skill 集成
- `index_build_service` 调 `scan_company_reports`
- `skill_runner_service` 串 stage0→4（import 模式，非 subprocess）
- 每 stage 写 `task_event` + `report_run.status` 推进；失败可重试
- `POST /reports/generate` + SSE
- **验证**：拿 CATL 2023 端到端跑通，diff 与现有 `宁德时代_深度报告_20260524.md` 关键章节标题一致

### 阶段 4：前端工作台 + 公司详情
- Vite + React + AntD 初始化
- 工作台、公司详情、React Query 封装、axios 拦截器

### 阶段 5：报告生成页 + 实时进度
- ReportWizard 三步表单
- `useTaskStream` SSE hook 驱动 stepper + 日志面板
- ReportReader 渲染

### 阶段 6：端到端 + 文档 + 启动脚本
- Playwright 跑通 CATL 2023 完整链路
- `README.md`（含 mapping.json 维护说明）
- `run.bat` 一键启
- 性能基线：单 PDF 解析 < 90s，单报告生成 < 8min

## 关键文件清单

**新建**：
- `D:/Quant/report_database/backend/`（整树，详见上文）
- `D:/Quant/report_database/frontend/`（整树）
- `D:/Quant/report_database/mapping.json`（`{name: stockCode}`）
- `D:/Quant/report_database/run.bat`
- `D:/Quant/report_database/README.md`
- `D:/Quant/report_database/.claude_state/`（SQLite + 日志）

**复用、不改**：
- `D:/Quant/deep-research-report/shared/tools/**`（核心 Python 实现）
- `D:/Quant/report_gen/report_generator/parser/**`（MinerU）
- `D:/Quant/report_database/scripts/split_section3.py`

**清理**（仅文件去重，不改逻辑）：
- 删除 `D:/Quant/deep-research-report/skills/` 和 `D:/Quant/report_database/.claude/skills/`，保留 `D:/Quant/deep-research-report/.claude/skills/`

## Verification

### 阶段 1 验证
```bash
cd D:/Quant/report_database/backend
uvicorn app.main:app --reload --port 8000
# 另开终端：
curl -X POST localhost:8000/companies -H 'Content-Type: application/json' -d '{"name":"宁德时代"}'
curl -X POST localhost:8000/companies/宁德时代/upload -F "file=@D:/Quant/report_gen/report/宁德时代/pdf/宁德时代2023年年度报告.pdf" -F "year=2023"
curl localhost:8000/companies/宁德时代
pytest backend/tests/test_companies.py -v   # 期望 100% 通过
```

### 阶段 2 验证
```bash
curl -X POST localhost:8000/companies/宁德时代/parse -H 'Content-Type: application/json' -d '{"year":2023}'
curl -N localhost:8000/tasks/{id}/stream
# 校验：
#   md/宁德时代/input/宁德时代2023年年度报告/ 下 12+ 个章节 .md
#   md/宁德时代/output/mid_file/管理层讨论/2023/ 下 5-8 个二级标题 .md
```

### 阶段 3 验证
```bash
curl -X POST localhost:8000/companies/宁德时代/reports/generate -H 'Content-Type: application/json' \
  -d '{"year":2023,"template":"investment_report"}'
curl -N localhost:8000/tasks/{id}/stream
# 5-8 分钟内出 md/宁德时代/output/final/宁德时代_深度报告_YYYYMMDD.md，体积 > 30KB
# 与 D:/Quant/report_database/md/宁德时代/output/final/宁德时代_深度报告_20260524.md 关键章节标题 diff 应一致
```

### 阶段 4-5 验证
Playwright 脚本：打开 `localhost:5173` → 完成一次 CATL 2023 全流程 → 截屏。

### 阶段 6 验证
- 双击 `run.bat` 后 30 秒内浏览器可达 5173
- 全部 pytest 通过
- 手工跑 CATL 2023/2024/2025 三个报告均成功

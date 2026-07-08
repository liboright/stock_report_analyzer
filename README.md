# Stock Report Analyzer

A 股上市公司年报「**搜索 → 解析 → 分析 → 深度报告**」一体化 Web 系统。

项目围绕 **公司 × 年份** 维度组织数据，把巨潮/上交所披露的 PDF 年报，经过切分、解析、结构化、表格抽取，再通过 Claude Code Skills 生成业务概况与行业分析，最终产出深度研究报告。

## 功能特性

- **年报获取**：支持从深交所 / 上交所公开披露页面自动下载（Playwright headless + WAF cookie 复用），也支持用户手动上传 PDF；按 SHA-256 去重。
- **PDF 解析**：自动把 200+ 页年报切分为「业务报告 / 财务报告」两部分，调用 MinerU 完成 PDF → Markdown，并通过 H2 章节再切分，输出可读的结构化 Markdown。
- **表格抽取**：内置 `md_table_parser`，从 Markdown 中识别并合并同源表格，导出为 CSV 供后续分析。
- **深度分析 Skill**：通过 `claude_skill_runner` 在子进程中调用 `claude` CLI 执行 Claude Code Skill（当前内置 `stage1_business_understanding`：生成业务概况 + 行业分析）。
- **任务进度实时推送**：基于 SQLite + SSE 的进度总线，前端可以实时看到 pipeline 每个阶段的运行状态。
- **前后端一体**：FastAPI 提供 REST API + SSE 进度流；Vite + React + Ant Design 前端按 4 阶段（SearchUpload / Parse / Analysis / Report）组织页面。

## 仓库结构

```
stock_report_analyzer/
├── README.md                # 本文件
├── report_analyzer/         # Web 系统（前端 + 后端）
│   ├── backend/             # FastAPI 后端
│   ├── frontend/            # Vite + React 前端
│   ├── docs/                # 项目文档（usage / architecture / artifacts）
│   ├── scripts/             # 一次性脚本（如 split_section3.py）
│   └── CLAUDE.md            # 子模块 Claude Code 说明
└── report_data/             # 数据与 Skill 落盘目录
    ├── mapping.json         # 公司名 → 股票代码（临时机制）
    ├── {公司名}/             # 每家公司一份：pdf/、md/...
    └── .claude/             # Claude Code Skills
```

> 路径布局、命名规则、ENV 变量、数据库字段语义、URL 映射以 [`report_analyzer/docs/artifacts.md`](report_analyzer/docs/artifacts.md) 为唯一事实源；端到端流程参见 [`report_analyzer/docs/usage.md`](report_analyzer/docs/usage.md)。

## 技术栈

- **后端**：FastAPI · SQLAlchemy · SQLite · Pydantic · uvicorn
- **前端**：Vite · React · TypeScript · Ant Design
- **PDF / 抓取**：Playwright（headless Chromium） · MinerU · pdfplumber
- **AI**：Claude Code Skills（subprocess 调 `claude` CLI）
- **进度**：SSE（Server-Sent Events）+ SQLite 状态机

## 环境要求

- Python **≥ 3.10**（建议使用 conda 虚拟环境）
- Node.js **≥ 18**（前端 Vite 需要）
- 可访问深交所 / 上交所披露页面（自动下载依赖）
- 有效的 `ANTHROPIC_API_KEY`（调用 Claude Code Skills）
- 有效的 `MINERU_API_KEY`（PDF → Markdown 解析）

## 快速启动

### 1. 克隆与配置

```bash
# 假定工作根目录为 D:/Quant
cd D:/Quant
git clone <your-repo-url> stock_report_analyzer
cd stock_report_analyzer
```

### 2. 启动后端

```bash
cd D:/Quant/stock_report_analyzer/report_analyzer/backend

# 安装依赖（推荐 conda 环境）
pip install -e ".[dev]"

# 准备环境变量
cp .env.example .env
# 编辑 .env，至少填入：
#   ANTHROPIC_API_KEY=...
#   MINERU_API_KEY=...
#   REPORT_DATA_PATH=D:/Quant/stock_report_analyzer/report_data

# 启动 API（默认 :8000）
uvicorn app.main:app --reload --port 8000

# 健康检查
# 浏览器访问 http://127.0.0.1:8000/docs 查看 OpenAPI 文档
```

### 3. 启动前端

```bash
cd D:/Quant/stock_report_analyzer/report_analyzer/frontend

# 安装依赖
npm install

# 启动 dev server（默认 :5173，已配置 /api → :8000 代理）
npm run dev

# 浏览器访问 http://localhost:5173
```

### 4. （可选）注册新公司

项目使用 `report_data/mapping.json` 维护「公司名 → 股票代码」映射，新增公司时需要先登记：

```json
{
 "宁德时代": "300750",
 "贵州茅台": "600519"
}
```

## 端到端流程

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  1.获取  │ →  │  2.解析  │ →  │  3.分析  │ →  │  4.生成  │
│  PDF    │    │  PDF→MD │    │  MD→结构│    │  深度报告│
└─────────┘    └─────────┘    └─────────┘    └─────────┘
   下载/上传     切分+解析+拆节    表格+业务理解    写作skill串联
```

各阶段对应的前端页面、后端接口、落盘位置详见 [`report_analyzer/docs/usage.md`](report_analyzer/docs/usage.md)。

## 测试

```bash
cd D:/Quant/stock_report_analyzer/report_analyzer/backend
pytest -v
```

## 文档导航

| 主题 | 路径 |
|---|---|
| 端到端使用流程（4 阶段） | [`report_analyzer/docs/usage.md`](report_analyzer/docs/usage.md) |
| 路径规范唯一事实源 | [`report_analyzer/docs/artifacts.md`](report_analyzer/docs/artifacts.md) |
| 整体架构设计 | [`report_analyzer/docs/architecture.md`](report_analyzer/docs/architecture.md) |
| 后端模块说明 | [`report_analyzer/backend/README.md`](report_analyzer/backend/README.md) |
| 历史实施进度 | [`report_analyzer/PROGRESS.md`](report_analyzer/PROGRESS.md) |
| 子模块 Claude Code 说明 | [`report_analyzer/CLAUDE.md`](report_analyzer/CLAUDE.md) |

## 复用模块

本项目不复制外部代码，而是通过 `sys.path` 注入复用：

- `D:/quant/deep-research-report/shared/tools/`（含 `table_parser.py`）
- `D:/quant/report_gen/report_generator/parser/`（MinerU 解析器）
- `D:/quant/stock_report_analyzer/report_analyzer/scripts/split_section3.py`

启动时由 `app/config.py::lifespan` 注入 `sys.path`。

# Report Analyzer — Claude Code 项目说明

> A 股年报「搜索 + 解析 + 深度报告」Web 系统
> 技术栈：FastAPI + SQLAlchemy + SQLite + Vite + React + Ant Design + Playwright + MinerU + Claude Code Skills

## 项目文档索引

> 进入本项目时，**先读 `docs/usage.md`** —— 它是项目最权威的使用流程说明（4 阶段：获取 → 解析 → 分析 → 生成），包含每个阶段的入口、关键文件、数据落盘位置、状态机演进。
> 涉及**具体文件路径/命名/落盘位置**时，再读 `docs/artifacts.md` —— 它是**路径规范的唯一事实源**（目录树、ENV、DB 路径字段、URL 映射、迁移对照）。

| 文档 | 路径 | 用途 |
|---|---|---|
| **使用流程** | [`docs/usage.md`](docs/usage.md) | 端到端 4 阶段流程说明（**先读这个**） |
| **中间文件位置（路径规范唯一事实源）** | [`docs/artifacts.md`](docs/artifacts.md) | 目录结构、命名规则、ENV、DB 路径字段、URL 映射、迁移对照（**查路径时读这个**） |
| 架构设计 | [`docs/architecture.md`](docs/architecture.md) | 整体架构、模块划分、关键选型、里程碑 |
| 后端 README | [`backend/README.md`](backend/README.md) | 后端启动 + 目录结构 |
| 表格解析 | [`backend/docs/md_table_parser.md`](backend/docs/md_table_parser.md) | `md_table_parser` 子包详细说明 |
| PDF 切分 | [`backend/docs/pdf_split.md`](backend/docs/pdf_split.md) | `pdf_split_service` 详细说明 |
| 进度 | [`PROGRESS.md`](PROGRESS.md) | 历史阶段实施进度（M1~M6） |

## 核心约定

### 数据落盘结构
> **详细路径布局、命名规则、ENV 变量、DB 路径字段语义、URL 映射、迁移对照表** 见 [`docs/artifacts.md`](docs/artifacts.md)（**路径规范唯一事实源**）。本节仅列出顶层速查：

- **根目录**：`D:/quant/stock_report_analyzer/report_data/`（`REPORT_DATA_PATH`）
- **公司子目录**：`D:/quant/stock_report_analyzer/report_data/{公司名}/`
- **公司映射**：`D:/quant/stock_report_analyzer/report_data/mapping.json`（**临时机制**，后期会替换为完整股票代码/名称本地映射）
- **数据库**：`D:/quant/stock_report_analyzer/report_data/.claude_state/state.db`（SQLite）
- **日志**：`D:/quant/stock_report_analyzer/report_data/.claude_state/logs/`

### 复用模块路径（不复制代码）
- `D:/quant/deep-research-report/shared/tools/`（核心 Python 实现，含 `table_parser.py`）
- `D:/quant/report_gen/report_generator/parser/`（MinerU 解析器）
- `D:/quant/stock_report_analyzer/report_analyzer/scripts/split_section3.py`（**注意**：在本项目内，不是 `report_data/scripts/`）
- 启动时由 `app/config.py::lifespan` 注入 `sys.path`

### 关键命令
```bash
# 后端
cd D:/quant/stock_report_analyzer/report_analyzer/backend
uvicorn app.main:app --reload --port 8000

# 前端
cd D:/quant/stock_report_analyzer/report_analyzer/frontend
npm run dev
# → http://localhost:5173（Vite proxy /api → :8000）

# 测试
cd D:/quant/stock_report_analyzer/report_analyzer/backend && pytest -v
```

### Skill 调用约定
- Skill 文件统一在 `D:/quant/stock_report_analyzer/report_data/.claude/skills/`
- 后端通过 `claude_skill_runner`（subprocess 调 `claude` CLI）跑 skill，**白名单**在 `SUPPORTED_SKILLS`
- 当前唯一支持的 skill：`stage1_business_understanding`（生成业务概况 + 行业分析）

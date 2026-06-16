# 实施进度

> 详细设计：`docs/architecture.md`
> 计划副本：`C:\Users\LiBo\.claude\plans\polished-giggling-snail.md`

## 当前状态：M1 + M2 + M3 + M4 完成

```
[✓] M1 后端骨架 + 公司 CRUD + 上传/下载       (13 测试)
[✓] M2 PDF 解析流水线                         (4 测试)
[✓] M3 stage1 Skill 集成（subprocess 调 claude CLI）(8 测试)
[✓] M4 前端工作台 + 公司详情                   (Vite + React + AntD, build 通过)
[ ] M5 报告生成页 + 实时进度（SSE）
[ ] M6 端到端测试 + 文档 + 启动脚本
```

**后端测试数**：26/26 通过（19.39s）

## M1：后端骨架（已完成）

### 已实现
- `pyproject.toml` + 依赖（fastapi / sqlalchemy / pydantic / httpx / sse-starlette / anthropic / pytest）
- `app/config.py` — pydantic-settings + 路径注入（lifespan 启动时把 `DEEP_RESEARCH_PATH`、`REPORT_GEN_PATH`、`SCRIPT_PATH` 加进 `sys.path`）
- `app/db/session.py` — SQLAlchemy 2.x engine + SessionLocal + 建表
- `app/models/` — 4 张表 ORM（Company / AnnualReport / ReportRun / TaskEvent）
- `app/schemas/` — 8 个 Pydantic v2 schema
- `app/services/` — company_search / pdf_upload / pdf_download
- `app/routers/` — companies / reports / tasks / settings
- `app/main.py` — FastAPI 入口 + CORS + lifespan

### 关键 API
| Method | Path | 用途 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/settings` | 配置快照 |
| GET/POST | `/companies` | 列出/创建公司 |
| GET | `/companies/{name}` | 公司详情 |
| POST | `/companies/{name}/upload` | 上传 PDF（SHA-256 去重） |
| GET | `/companies/{name}/reports` | 列出年报 |

## M2：PDF 解析流水线（已完成）

### 已实现
- `app/services/pdf_parse_service.py` — 真实 MinerU + mock 双模式
- `app/services/chapter_split_service.py` — 按"第X节"切分到 `input/`
- `app/services/section3_split_service.py` — 包装 `scripts/split_section3.py`，monkey patch `REPORT_BASE_PATH`
- `app/workers/progress_bus.py` — 内存 pub/sub + 持久化 `task_event`
- `app/workers/parse_pipeline.py` — 3 步串行 + status 推进（`queued` → `running` → `done/failed`）
- `POST /companies/{name}/parse?year={y}&use_mock={true|false}` 触发

### 关键修复
1. **参数顺序**：`bg: BackgroundTasks` 必须放所有有默认参数前
2. **sys.path 注入**：split_section3.py 硬编码路径错，service 显式加 `DEEP_RESEARCH_PATH/shared/tools`
3. **SessionLocal 引用锁定**：`from app.db.session import SessionLocal` 绑死旧 engine（fixture 重建时新 engine 不生效）。**改用 `from app.db import session as db_session` + `db_session.SessionLocal()` 动态访问**
4. **章节切分"目录"误伤**：`"目录" in title` 误伤"第一节 重要提示、目录和释义"，改成 `title == "目录" or title.startswith("目录 ")` 精确判断
5. **BackgroundTask 调度延迟**：`time.sleep(2.0)` 让出时间片，让 threadpool 启动 pipeline
6. **status 推进**：`run_parse_pipeline` 启动立即设 `status=running`，避免 30s 误判

## 当前端到端能力

```bash
# 1) 启动后端
cd D:/Quant/report_database/backend
uvicorn app.main:app --reload --port 8000

# 2) 启动前端（另一终端）
cd D:/Quant/report_database/frontend
npm run dev
# → http://localhost:5173

# 3) 后端 REST（OpenAPI 文档 / 调试）
# http://127.0.0.1:8000/docs

# 4) curl 端到端（直接走后端）
curl -X POST http://127.0.0.1:8000/companies -d '{"name":"宁德时代"}' -H 'Content-Type: application/json'
curl -X POST http://127.0.0.1:8000/companies/宁德时代/upload \
  -F "file=@D:/Quant/report_gen/report/宁德时代/pdf/宁德时代2023年年度报告.pdf" \
  -F "year=2023"
curl -X POST "http://127.0.0.1:8000/companies/宁德时代/parse?year=2023&use_mock=true"
curl -X POST http://127.0.0.1:8000/reports/generate \
  -d '{"company":"宁德时代","year":2023,"skill":"stage1_business_understanding"}' \
  -H 'Content-Type: application/json'
curl http://127.0.0.1:8000/tasks/{run_id}
curl http://127.0.0.1:8000/reports/{run_id}/content
```

## M3：stage1 Skill 集成（已完成，按用户约束只接 stage1）

> 用户原话："目前的 skill 只有 stage1_business_understanding 基本完善了，接通就可以，不要开发skill"
> 调整后方案：M3 不重写 5 阶段流水线，**改为通过 subprocess 调 `claude` CLI 跑 stage1 skill**。
> 这样保留了 Claude Code 加载 skill 的能力（SKILL.md 解析、模板渲染都在 CLI 侧），后端只负责触发 + 状态机。

### 已实现
- `app/services/claude_skill_runner.py` — subprocess 包装 `claude -p "/<skill> <company> [year]" --bare --add-dir ...`
  - 600s timeout
  - 从 settings 注入 `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`
  - 跑完校验产物 `md/{公司}/output/research_file/{公司}_业务概况.md` 存在
  - 非 0 退出 / 超时 / 产物缺失都抛 `ClaudeSkillError`
- `app/workers/report_pipeline.py` — BackgroundTask 入口
  - 立即设 `status=running, current_stage=1`
  - 调 `run_skill()` → 成功写 `final_path`（相对路径），失败写 `error`
- `app/routers/reports.py` —
  - `POST /reports/generate` 返回 202 + `run_id`
  - `GET /reports/{run_id}/content` 读最终 markdown（status≠done → 409）
  - `GET /reports/by-company/{name}` 列该公司所有 run

### 关键修复
1. **Windows .cmd 解析**：`subprocess.run(['claude'])` 在 Windows 报 `WinError 2`（claude 是 .cmd 不是 .exe）。改用 `shutil.which("claude")` 拿全路径作为 `cmd[0]`
2. **白名单校验**：`_build_command` 在拼命令前先查 `SUPPORTED_SKILLS`，防止任意 skill 名注入

### 当前支持的 skill
| skill | 产物 |
|---|---|
| `stage1_business_understanding` | `md/{公司}/output/research_file/{公司}_业务概况.md` |

如需新增 stage2-4（产品分析 / 财务分析 / 估值 / 总结），等对应 skill 在 `.claude/skills/` 里完善后再扩展 `SUPPORTED_SKILLS` 白名单 + `_expected_output` 路径映射即可。

## M4：前端工作台 + 公司详情（已完成）

### 已实现
- Vite 5 + React 18 + TypeScript 工程（`D:/Quant/report_database/frontend/`）
- 依赖：antd v5 / @tanstack/react-query v5 / zustand / react-router-dom v6 / axios
- 入口 + 布局：`src/main.tsx`（Provider 链）、`src/components/AppLayout.tsx`（Header + Outlet）
- API 客户端：`src/api/{client,companies,reports,tasks,settings}.ts`，统一 baseURL `/api`（Vite 反代到 8000），拦截器 4xx/5xx 弹 message（409 例外）
- 类型：`src/types/api.ts` 与 Pydantic schema 对齐
- React Query hook：`src/hooks/useCompanies.ts`（list / detail / create）
- 三个页面：
  - `/` **Dashboard**（`pages/Dashboard.tsx`）— 公司列表 + 「新建公司」Modal
  - `/companies/:name` **CompanyDetail**（`pages/CompanyDetail.tsx`）— Header + Descriptions 元信息 + 三段 Card（年报 / 报告运行 / 元数据），**已实现「上传 PDF」Modal**（M1 后端 /upload 调通）
  - `/settings` **SettingsPage**（`pages/SettingsPage.tsx`）— 配置只读展示

### 验证
- `npm run build` 通过（3144 modules，1.2MB bundle）
- `tsc --noEmit` 无错误
- 端到端：起后端 8000 + 前端 5173，Vite 代理 `/api → :8000` 工作
  - `GET /api/companies` 200（已有 1 条历史数据）
  - `GET /api/companies/宁德时代` 200，返回 `annual_reports` + `report_runs` 完整数据
  - `GET /api/settings` 200，配置快照正确
  - `OPTIONS /api/companies` CORS preflight 204
- 类型对齐点：后端 Pydantic `annual_reports` / `report_runs`（snake_case `report_base_path` 等）已映射

### M4 范围外（留给 M5）
- 「解析 / 生成报告」按钮的 SSE 实时进度
- ReportWizard 多步表单
- `useTaskStream` SSE hook
- ReportReader markdown 渲染（`react-markdown` + `remark-gfm`）
- 工作台里"近 7 天活跃"小卡片

## 项目文件分布

```
D:/Quant/report_database/
├── docs/architecture.md            设计方案
├── PROGRESS.md                     ← 本文件
├── mapping.json                    公司名→stockCode 映射
├── backend/                        ★ FastAPI 后端
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db/session.py
│   │   ├── models/{company,annual_report,report_run,task_event}.py
│   │   ├── schemas/                # 8 个 Pydantic schema
│   │   ├── routers/{companies,reports,tasks,settings}.py
│   │   ├── services/
│   │   │   ├── company_search_service.py

## 项目文件分布

```
D:/Quant/report_database/
├── docs/architecture.md            设计方案
├── PROGRESS.md                     ← 本文件
├── mapping.json                    公司名→stockCode 映射
├── backend/                        ★ FastAPI 后端
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db/session.py
│   │   ├── models/{company,annual_report,report_run,task_event}.py
│   │   ├── schemas/                # 8 个 Pydantic schema
│   │   ├── routers/{companies,reports,tasks,settings}.py
│   │   ├── services/
│   │   │   ├── company_search_service.py
│   │   │   ├── pdf_upload_service.py
│   │   │   ├── pdf_download_service.py
│   │   │   ├── pdf_parse_service.py
│   │   │   ├── chapter_split_service.py
│   │   │   ├── section3_split_service.py
│   │   │   └── claude_skill_runner.py        # M3
│   │   └── workers/
│   │       ├── progress_bus.py
│   │       ├── parse_pipeline.py
│   │       └── report_pipeline.py             # M3
│   ├── tests/                      26 个测试，全绿
│   ├── pyproject.toml
│   ├── .env / .env.example
│   └── README.md
├── frontend/                       ★ M4 新增：Vite + React + AntD
│   ├── package.json
│   ├── vite.config.ts              /api → :8000 反代
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx                Provider 链（Query/ConfigProvider/Router）
│       ├── App.tsx                 路由
│       ├── components/AppLayout.tsx
│       ├── pages/{Dashboard,CompanyDetail,SettingsPage}.tsx
│       ├── api/{client,companies,reports,tasks,settings}.ts
│       ├── hooks/useCompanies.ts
│       └── types/api.ts
├── .claude_state/                  SQLite + 日志目录
│   ├── state.db
│   └── logs/app.log
├── raw/                            用户上传/下载的 PDF
│   └── 宁德时代/pdf/
├── md/                             ★ 解析后的报告
│   └── 宁德时代/
│       ├── input/{公司}{年份}年年度报告/    # 按"第X节"切分
│       ├── output/
│       │   ├── navi/{公司}_{年}_index.json
│       │   ├── mid_file/管理层讨论/{年份}/  # 第三节按 H2 拆分
│       │   └── final/{公司}_深度报告_{date}.md
│       └── 报告原文（已存在）
└── scripts/split_section3.py       复用，未改
```

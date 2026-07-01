# 中间文件存放位置（路径规范唯一事实源）

> 适用版本：2026-06-08 起的报告存放路径重构之后；2026-06-18 起 `report_data/` 整体上移到仓库根 `D:/quant/stock_report_analyzer/` 下。
> 路径变量集中定义在 `backend/app/config.py::Settings`（pydantic-settings 读 `.env`）

## 0. 概览

**本文件是路径规范的唯一事实源（single source of truth）**。所有以下内容都应参照本文件，不另行规定：

- 后端代码（`backend/app/**/*.py`）的路径拼接
- 复用模块（`D:/quant/deep-research-report/shared/tools/**`）的常量与默认参数
- 测试 fixture 的真实数据路径
- Claude Skills 的路径示例（`D:/quant/stock_report_analyzer/report_analyzer/.claude/skills/**`、`D:/quant/stock_report_analyzer/report_data/.claude/skills/**`）
- 文档中的所有路径示意

**2026-06-08 重构说明**：
- 旧 `report/raw/{公司}/...` 和 `md/{公司}/...` **两棵平行树** 合并为单棵树
- `md/` 子目录按语义重命名：`raw/`（原始 MinerU 解析）、`clean/`（清洗后）、`research_file/`（业务概况+行业分析）、`final/`（最终报告）
- ENV 变量从 7 个收敛到 6 个（删除 `RAW_BASE_PATH` / `REPORT_BASE_PATH`，统一以 `REPORT_DATA_PATH` 为 base）
- 阶段 2.2'（preliminary 解析）和阶段 2.2 之前的整本解析（legacy `parse_pipeline.py`）**整段删除**——对应历史 DB 记录与文件已清理

## 1. 目标目录树

```
D:/quant/stock_report_analyzer/report_data/                                   ← 路径变量：REPORT_DATA_PATH
├── mapping.json                                        ← 公司名→股票代码（MAPPING_PATH）
├── .claude_state/                                      ← SQLite + 日志 + 临时（不动）
│   ├── state.db                                        ← DB_PATH
│   ├── logs/                                           ← LOG_DIR
│   └── tmp/                                            ← 上传临时缓存
└── {公司}/                                             ← 中文公司名（与 DB company.name 一致）
    ├── pdf/
    │   ├── original/                                   ← 阶段 1：原始 PDF
    │   │   └── {公司}{年份}年年度报告.pdf
    │   └── split/                                      ← 阶段 2.1：业务/财务 PDF 切分
    │       ├── {公司}{年份}年年度报告_业务报告.pdf
    │       └── {公司}{年份}年年度报告_财务报告.pdf
    └── md/
        ├── raw/                                        ← 阶段 2.2：MinerU 解析后
        │   ├── 业务报告/{公司}{年份}年年度报告/         ← 阶段 2.2 业务
        │   │   ├── full.md
        │   │   ├── {公司}{年份}年年度报告_业务报告.md
        │   │   ├── images/
        │   │   └── {uuid}_content_list_v2.json
        │   └── 财务报告/{公司}{年份}年年度报告/         ← 阶段 2.2 财务
        │       ├── full.md
        │       ├── {公司}{年份}年年度报告_财务报告.md
        │       ├── images/
        │       └── {uuid}_content_list_v2.json
        ├── clean/                                      ← 阶段 2.3-2.5
        │   └── {公司}{年份}年年报/                      ← 每份年报一个独立目录
        │       ├── by_section/                         ← 阶段 2.3：按"第X节"切分
        │       │   ├── 01_第一节_xxx.md
        │       │   ├── 02_第二节_xxx.md
        │       │   └── ... (12+ 章)
        │       ├── 管理层讨论/                          ← 阶段 2.4：第三节按 H2 拆
        │       │   ├── 01_xxx.md
        │       │   └── ... (5~14 个 H2)
        │       └── table/                               ← 阶段 2.5：表格抽取
        │           ├── 营业收入构成.csv
        │           ├── 现金流.csv
        │           ├── 研发投入.csv
        │           ├── 产能产量.csv
        │           ├── 产销情况.csv
        │           ├── 客户供应商.csv
        │           ├── 技术参数.csv
        │           ├── 费用分析.csv
        │           └── 其他/                            ← 阶段 2.5：未命中 8 类的表（按清理后标题命名）
        ├── research_file/                              ← 阶段 3.1：业务概况+行业分析（不分年）
        │   ├── {公司}_业务概况.md
        │   ├── {公司}_业务概况_{年份}.md                ← 增量写作版本
        │   ├── {公司}_行业分析.md
        │   └── table/                                  ← 阶段 3 合并表格
        │       └── ...
        └── final/                                       ← 阶段 4：最终深度报告
            └── {公司}_深度报告_{YYYYMMDD}.md
```

> 标注"不动"：mapping.json、.claude_state/（含 state.db / logs / tmp）不在路径重构范围。

## 2. ENV 变量清单

| 配置项 | 默认值 | 用途 |
|---|---|---|
| `REPORT_DATA_PATH` | `D:/quant/stock_report_analyzer/report_data` | **唯一根目录**。所有相对路径均以此为 base。 |
| `MAPPING_PATH` | `D:/quant/stock_report_analyzer/report_data/mapping.json` | 公司名→股票代码映射 |
| `DB_PATH` | `D:/quant/stock_report_analyzer/report_data/.claude_state/state.db` | SQLite 数据库 |
| `LOG_DIR` | `D:/quant/stock_report_analyzer/report_data/.claude_state/logs` | 后端应用日志 |
| `DEEP_RESEARCH_PATH` | `D:/quant/deep-research-report` | 外部依赖：annual_report_reader / table_parser |
| `SCRIPT_PATH` | `D:/quant/stock_report_analyzer/report_analyzer/scripts` | 阶段 2.4 用到的 `split_section3.py` 等脚本 |

> ❌ 已废弃（重构后不再使用）：`RAW_BASE_PATH`、`REPORT_BASE_PATH`——历史 .env 残留将导致 pydantic 警告，应清理。

**派生属性**（`backend/app/config.py::Settings`）：

| 派生 | 公式 | 用途 |
|---|---|---|
| `db_url` | `sqlite:///{Path(DB_PATH).resolve().as_posix()}` | SQLAlchemy 引擎 |
| `ensure_runtime_dirs()` | `mkdir(parents=True, exist_ok=True)` 建好 `DB_PATH.parent`、`LOG_DIR`、`REPORT_DATA_PATH` | 启动时自检 |
| `inject_external_paths()` | `sys.path.insert(0, ...)` 把 `DEEP_RESEARCH_PATH` + `SCRIPT_PATH` 加进 `sys.path` | 启动时注入 |

## 3. 文件/目录命名规则

### 3.1 占位符

| 占位符 | 含义 | 示例 |
|---|---|---|
| `{公司}` | 中文公司名（与 `company.name` 一致） | `宁德时代` / `比亚迪` / `贵州茅台` |
| `{年份}` | 4 位阿拉伯数字 | `2023` / `2024` / `2025` |
| `{YYYYMMDD}` | 8 位日期（生成日） | `20260524` |
| `{uuid}` | MinerU 返回的 UUID（`content_list_v2.json` 文件名） | `0b4e1c89...` |

### 3.2 各阶段文件命名

| 阶段 | 目录 | 文件命名规则 |
|---|---|---|
| 1 | `pdf/original/` | `{公司}{年份}年年度报告.pdf` |
| 2.1 | `pdf/split/` | `{公司}{年份}年年度报告_业务报告.pdf`、`{公司}{年份}年年度报告_财务报告.pdf` |
| 2.2 业务 | `md/raw/业务报告/{公司}{年份}年年度报告/` | `{公司}{年份}年年度报告_业务报告.md`（主文件）、`full.md`（MinerU 原始）、`images/`、`{uuid}_content_list_v2.json` |
| 2.2 财务 | `md/raw/财务报告/{公司}{年份}年年度报告/` | 同上，文件名后缀为 `_财务报告.md` |
| 2.3 | `md/clean/{公司}{年份}年年报/by_section/` | `XX_{节标题}.md`（XX 为中文数字转 2 位阿拉伯数字，不足补 0）。"目录"章节**跳过不生成**。 |
| 2.4 | `md/clean/{公司}{年份}年年报/管理层讨论/` | `NN_{标题}.md`（NN 为顺序编号或中文序号转 2 位数字）。文件头部带 YAML 元信息：<br>`company`、`year`、`section`、`original_title` |
| 2.5 | `md/clean/{公司}{年份}年年报/table/` | `{表格类型}.csv`（固定 8 类，详见 §1 树状图）；未命中 8 类 → `其他/{清理后标题}.csv`（重名 `_{2}` `_{3}` 自增去重）|
| 3.1 | `md/research_file/` | `{公司}_业务概况.md`、`{公司}_业务概况_{年份}.md`（增量）、`{公司}_行业分析.md` |
| 3.1 表格 | `md/research_file/table/` | （阶段 3 合并表格，业务待实现）|
| 4 | `md/final/` | `{公司}_深度报告_{YYYYMMDD}.md` |

### 3.3 命名反模式（禁止）

- ❌ 任何路径中含 `report/` 或 `report/raw/`（旧 `RAW_BASE_PATH` 残留）
- ❌ `md/input/` 或 `md/output/` 子目录（旧命名，已重命名为 `md/clean/...`）
- ❌ `md/raw/preliminary/`（阶段 2.2' 已删除）
- ❌ `md/split/...`（旧 raw 树下的 split 子树，已并入 `md/raw/{业务,财务}报告/`）
- ❌ `output/mid_file/research_file/`（旧 mid_file 子树残留，已并入 `md/research_file/`）
- ❌ 路径分隔符用 `\`（统一 POSIX `/`）
- ❌ 大小写 `Quant`（统一小写 `quant`）

## 4. DB 路径字段语义

所有路径字段**都存相对路径**（相对 `REPORT_DATA_PATH` 的 POSIX 风格字符串）。**绝对路径在 DB 中不存在**——前端通过 `/api/static/{md,raw}/{rel}` 拼 URL。

### 4.1 `annual_report` 表

| 字段 | 含义 | 是否使用 | 示例相对路径 |
|---|---|---|---|
| `pdf_path` | 原始 PDF 路径（相对 `REPORT_DATA_PATH`）| ✅ 在用 | `宁德时代/pdf/original/宁德时代2025年年度报告.pdf` |
| `finance_pdf_path` | 切分后财务 PDF | ✅ 在用 | `宁德时代/pdf/split/宁德时代2025年年度报告_财务报告.pdf` |
| `other_pdf_path` | 切分后业务 PDF | ✅ 在用 | `宁德时代/pdf/split/宁德时代2025年年度报告_业务报告.pdf` |
| `business_md_path` | 业务 MD（阶段 2.2）| ✅ 在用 | `宁德时代/md/raw/业务报告/宁德时代2025年年度报告/宁德时代2025年年度报告_业务报告.md` |
| `finance_md_path` | 财务 MD（阶段 2.2）| ✅ 在用 | `宁德时代/md/raw/财务报告/宁德时代2025年年度报告/宁德时代2025年年度报告_财务报告.md` |
| `md_path` | **deprecated**（2026-06-08 前为整本解析产物；现已 UPDATE 为 NULL，**未来 drop column**）| ❌ 不用 | — |
| `pdf_sha256` | 原始 PDF SHA-256（去重用）| ✅ 在用 | （哈希字符串）|
| `split_status` | `''` / `'done'` | ✅ 在用 | — |
| `parse_split_status` | `pending` / `business_done` / `done` / `failed` | ✅ 在用 | — |
| `parse_status` | `pending` / `parsing` / `done` / `failed` | ✅ 在用 | — |

### 4.2 `report_run` 表

| 字段 | 含义 | 备注 |
|---|---|---|
| `final_path` | 任务最终产物路径（**绝对路径**）| `report_pipeline.py` 等通过 `Path(__file__).parents[2]` 等算 base；可能为绝对路径。Phase 4 后会改为相对 `REPORT_DATA_PATH`。 |
| `template` | pipeline 名 | 取值：`annual_report_download` / `parse_split` / `chapter_split` / `report` / ... |
| `current_stage` | 当前阶段（0-N）| — |

## 5. 静态文件 URL 映射

后端用 `REPORT_DATA_PATH` 为 base 提供两个静态文件路由：

| URL | base | 用途 |
|---|---|---|
| `GET /api/static/md/{rel_path:path}` | `REPORT_DATA_PATH` | 读 MD 文件（如 `宁德时代/md/clean/.../01_xxx.md`）|
| `GET /api/static/raw/{rel_path:path}` | `REPORT_DATA_PATH` | 读 PDF 文件（如 `宁德时代/pdf/original/...pdf`）|

两个路由共用 base，区别仅在 `_safe_resolve` 校验和 `FileResponse` 的 `media_type`。前端 `getFileUrl()` 默认拼 `/api/static/md/`。

## 6. 阶段 → 路径映射表

| 阶段 | 产物 | 相对路径 | 生成方式 |
|---|---|---|---|
| 1.1 在线下载 | 原始 PDF | `{公司}/pdf/original/{公司}{年份}年年度报告.pdf` | `POST /companies/{name}/download` |
| 1.2 用户上传 | 原始 PDF | 同上 | `POST /companies/{name}/upload` |
| 2.1 PDF 切分 | 业务 PDF | `{公司}/pdf/split/{公司}{年份}年年度报告_业务报告.pdf` | `POST /companies/{name}/split-pdf` |
| 2.1 PDF 切分 | 财务 PDF | `{公司}/pdf/split/{公司}{年份}年年度报告_财务报告.pdf` | 同上 |
| 2.2 业务 MD | MD 主文件 | `{公司}/md/raw/业务报告/{公司}{年份}年年度报告/{公司}{年份}年年度报告_业务报告.md` | `POST /companies/{name}/parse-split` Stage 1 |
| 2.2 业务 MD | full.md / images / json | 同目录 | 同上（MinerU 原始产物）|
| 2.2 财务 MD | MD 主文件 | `{公司}/md/raw/财务报告/{公司}{年份}年年度报告/{公司}{年份}年年度报告_财务报告.md` | `POST /companies/{name}/parse-split` Stage 2 |
| 2.3 章节切分 | 各章 MD | `{公司}/md/clean/{公司}{年份}年年报/by_section/XX_第X节_xxx.md` | `POST /companies/{name}/chapters?year=...` Step A |
| 2.4 第三节 H2 拆 | H2 MD | `{公司}/md/clean/{公司}{年份}年年报/管理层讨论/NN_xxx.md` | `POST /companies/{name}/chapters?year=...` Step B |
| 2.5 表格抽取 | CSV | `{公司}/md/clean/{公司}{年份}年年报/table/{类型}.csv` | `POST /companies/{name}/tables/extract?year=...` |
| 3.1 业务概况 | MD | `{公司}/md/research_file/{公司}_业务概况.md` | `POST /reports/generate`（stage1 skill）|
| 3.1 行业分析 | MD | `{公司}/md/research_file/{公司}_行业分析.md` | 同上 |
| 3.x 合并表格 | CSV | `{公司}/md/research_file/table/...` | （待实现）|
| 4 最终报告 | MD | `{公司}/md/final/{公司}_深度报告_{YYYYMMDD}.md` | （阶段 4 待实现）|

## 7. 路径迁移对照表

下表列出**重构前**的旧路径与**重构后**的新路径的对应关系。重构前数据已通过 `backend/scripts/migrate_to_single_tree.py` 一次性迁移。

| 阶段 | 旧路径（重构前）| 新路径（重构后）| 备注 |
|---|---|---|---|
| 1 | `report/raw/{公司}/pdf/original/{公司}{年份}年年度报告.pdf` | `{公司}/pdf/original/{公司}{年份}年年度报告.pdf` | DB `pdf_path` 字符串**不变**（去掉前缀 `report/raw/`）|
| 2.1 | `report/raw/{公司}/pdf/split/{公司}{年份}年年度报告_业务报告.pdf` | `{公司}/pdf/split/{公司}{年份}年年度报告_业务报告.pdf` | DB `other_pdf_path` 字符串**不变** |
| 2.1 | `report/raw/{公司}/pdf/split/{公司}{年份}年年度报告_财务报告.pdf` | `{公司}/pdf/split/{公司}{年份}年年度报告_财务报告.pdf` | DB `finance_pdf_path` 字符串**不变** |
| 2.2 业务 | `report/raw/{公司}/md/split/{公司}{年份}年年度报告/{公司}{年份}年年度报告_业务报告.md` | `{公司}/md/raw/业务报告/{公司}{年份}年年度报告/{公司}{年份}年年度报告_业务报告.md` | DB `business_md_path` 字符串从 `md/split/` 改为 `md/raw/业务报告/` |
| 2.2 财务 | `report/raw/{公司}/md/split/{公司}{年份}年年度报告/{公司}{年份}年年度报告_财务报告.md` | `{公司}/md/raw/财务报告/{公司}{年份}年年度报告/{公司}{年份}年年度报告_财务报告.md` | DB `finance_md_path` 字符串从 `md/split/` 改为 `md/raw/财务报告/` |
| 2.2' | `report/raw/{公司}/md/preliminary/...` | （**已删除**）| 阶段 2.2' 整段删除，2024 宁德时代记录清理 |
| 2.2 之前 | `report/raw/{公司}/md/{公司}{年份}年年度报告.md` | （**已删除**）| 阶段 2.2 之前的整本解析整段删除，2023 宁德时代记录清理；PDF 保留在 `pdf/original/`，可重跑 `parse-split` 重生 |
| 2.3 | `report/md/{公司}/input/{公司}{年份}年年度报告/XX_第X节_xxx.md` | `{公司}/md/clean/{公司}{年份}年年报/by_section/XX_第X节_xxx.md` | 旧 `input/` → 新 `clean/{年}年年报/by_section/` |
| 2.4 | `report/md/{公司}/output/mid_file/管理层讨论/{年份}/NN_xxx.md` | `{公司}/md/clean/{公司}{年份}年年报/管理层讨论/NN_xxx.md` | 旧 `output/mid_file/管理层讨论/` → 新 `clean/{年}年年报/管理层讨论/` |
| 2.5 | `report/md/{公司}/output/tables/*.csv` | `{公司}/md/clean/{公司}{年份}年年报/table/*.csv` | 旧 `output/tables/` → 新 `clean/{年}年年报/table/` |
| 3.1 | `report/md/{公司}/output/research_file/*.md` | `{公司}/md/research_file/*.md` | 旧 `output/research_file/` → 新 `research_file/` |
| 3.1 表格 | `report/md/{公司}/output/research_file/table/...` | `{公司}/md/research_file/table/...` | 同上 |
| 3.1 旧并存 | `report/md/{公司}/output/mid_file/research_file/...` | （**已删除**）| 旧 mid_file/research_file 与 output/research_file 并存，已统一到 `md/research_file/` |
| 4 | `report/md/{公司}/output/final/*.md` | `{公司}/md/final/*.md` | 旧 `output/final/` → 新 `final/` |

> **路径分隔符归一化**：所有 DB 路径字段中历史遗留的 `\` 全部改为 `/`。

## 8. 关键文件索引

| 内容 | 关键文件 |
|---|---|
| 路径变量定义 | `backend/app/config.py::Settings` |
| 阶段 1 上传 | `backend/app/services/pdf_upload_service.py` |
| 阶段 1 下载 | `backend/app/workers/download_pipeline.py` + `backend/app/services/annual_report_downloader.py` |
| 阶段 2.1 切分 | `backend/app/services/pdf_split_service.py` |
| 阶段 2.2 解析 | `backend/app/workers/parse_split_pipeline.py` + `backend/app/services/mineru_parser/` |
| 阶段 2.3-2.4 章节 + H2 | `backend/app/services/chapter_split_service.py` + `backend/app/services/section3_split_service.py`（由 `POST /chapters` 触发）|
| 阶段 2.5 表格 | `backend/app/services/md_table_parser/` |
| 阶段 3.1 skill | `D:/quant/stock_report_analyzer/report_data/.claude/skills/stage1_business_understanding/SKILL.md` + `backend/app/services/claude_skill_runner.py` + `backend/app/workers/report_pipeline.py` |
| 静态文件路由 | `backend/app/main.py`（`/api/static/md/` 和 `/api/static/raw/`）|
| 复用模块路径常量 | `D:/quant/deep-research-report/shared/tools/annual_report_search/constants.py`、`annual_report_reader/utils.py`、`table_extractor.py` |
| 迁移脚本 | `backend/scripts/migrate_to_single_tree.py` |

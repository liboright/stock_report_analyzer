---
name: annual-report-search
description: 自动下载 A 股上市公司最近三年（默认）年度报告 PDF。触发场景：(1) 用户要求"下载年报"/"下载年度报告"/"年报搜索"/"获取年报"；(2) 用户提供 6 位股票代码并希望批量下载 PDF；(3) 用户提到"上交所"/"深交所" + "年报"。通过 playwright MCP 驱动浏览器访问深交所/上交所公告页面，按交易所识别分支执行，PDF 自动保存到项目 `D:/quant/report_data/{公司名}/pdf/original/` 目录，文件名 `{公司名}{年份}年年度报告.pdf`。不下载摘要、修订版、英文版。
---

# 年报搜索

## 用途

通过浏览器自动化，按 6 位 A 股股票代码，从深交所/上交所网站下载指定年份的年度报告 PDF，保存到项目约定的 raw 目录。

## 输入参数（从用户消息中提取）

| 参数 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `stock_code` | 是 | — | 6 位股票代码（`000001`/`600519`/`300750`） |
| `company_name` | 否 | `stock_code` | 用于目录名与文件名。**优先从 `D:/quant/report_data/mapping.json` 反查**：`mapping.json` 是 `{公司名: 股票代码}`，需要遍历 value 匹配。匹配不到时退回 `stock_code` |
| `years` | 否 | 最近 3 年 | 例：当前 2026-06 → 默认 `[2025, 2024, 2023]`。可由用户显式覆盖（"下载 2022-2024" → `[2022, 2023, 2024]`） |
| `output_dir` | 否 | （无固定默认） | **必须**用调用方在 prompt 里传入的路径，**不要再追加任何子目录**。常见用法：后端 worker 会传 `D:/quant/report_data/{公司名}/pdf/.staging/`，落盘到该目录即可；worker 负责后续从 staging 复制到 pdf/original/ |

**用户只提供公司名时**：先 `Glob` 读取 `D:/quant/report_data/mapping.json` 反查代码，再继续。

## 输出

每一年一份 PDF，路径（**严格用 prompt 传入的 `output_dir`，不要硬编码 `original/`**）：
```
<output_dir>/{company_name}{year}年年度报告.pdf
```

每完成一个文件向用户报告一次；某年缺失（如尚未发布）跳过并说明，不中断整体流程。

## 交易所识别（按代码前缀）

| 前缀 | 交易所 | 流程文档 |
|------|--------|----------|
| `000`/`001`/`002`/`003`/`300` | 深圳（SZSE） | `references/szse-flow.md` |
| `600`/`601`/`603`/`605`/`688` | 上海（SSE） | `references/sse-flow.md` |
| 其它 | — | 询问用户确认，不强行继续 |

**创业板**（`300`）走深交所流程，**科创板**（`688`）走上交所流程。

## 通用步骤

```
1. 解析输入（提取 stock_code / company_name / years）
   └─ mapping.json 反查缺失字段
2. mkdir -p output_dir
3. for year in years（按从新到旧）:
   ├─ 按代码前缀选 references/szse-flow.md 或 sse-flow.md
   ├─ 按流程打开浏览器、搜索、定位、下载
   ├─ 校验下载文件（> 100 KB + 扩展名 .pdf）
   ├─ 移动/重命名到 {output_dir}/{company_name}{year}年年度报告.pdf
   └─ 报告单年结果（成功/失败/跳过）
4. 汇总：成功 N / 失败 M / 跳过 K
```

## 关键约束

### 选对公告
同一年度可能有多份相关公告，**只下载主报告**：
- ✅ 选中：`XXX 2025年年度报告` / `XXX 2025 年年度报告`
- ❌ 跳过：`2025年年度报告摘要` / `2025年年度报告（修订版）` / `2025 Annual Report`（英文）
- 当主报告与修订版共存时：**优先修订版**（数据更准），文件名保留 `年度报告`（不带"修订"）

### 文件大小
交易所某些页面"下载"返回的是 HTML 详情页，不是 PDF：
- 真实 PDF：通常 1 MB - 30 MB
- 若 < 100 KB → 不是 PDF，重新走流程

### 下载路径

**两个交易所的反爬差异很大**，按代码前缀走对应流程：

| 交易所 | WAF | curl 复杂度 |
|--------|-----|-------------|
| SZSE（深交所）| **无** | `curl -A "<UA>"` 直接拿到 PDF |
| SSE（上交所）| JS 挑战 acw_sc__v2 | 必须先浏览器拿 cookie，带 cookie 调 curl |

**SZSE 优势**：
- 表格行 `<a>` 元素的 **`attachpath` 属性**就是 PDF 直链（无需点进详情页）
- 静态 CDN 域名 `disc.static.szse.cn` 无反爬，curl 即可
- 一次浏览器会话查多家公司，提取 attachpath 后 curl 极快

**SSE 必须的 3 步**：
1. `browser_evaluate` 取 `document.cookie`（含 acw_sc__v2）
2. `curl -A "<UA>" -b "<cookie>" -L --compressed` 下载 PDF
3. 完整流程见 `references/sse-flow.md` 步骤 9

playwright MCP 的 `browser_file_upload` **不能**用于把远程 PDF 存到本地。

### 错误恢复
- **未找到公告**：某年报尚未发布（如 2025 年报 2026 年 4 月底前才出）→ 跳过，记录提示
- **页面加载失败**：刷新一次后重试；仍失败 → 跳过并附错误
- **下载链接 404**：尝试换"摘要"外的其他链接
- **单年失败不影响其它年份**：try/except 包住每一年

## 触发关键词

以下用户表达应触发本 skill：
- "下载 XXX 的年报" / "下载 600519 的最近三年年报"
- "搜索/获取 宁德时代 的年度报告"
- "从深交所/上交所下载年报"
- "下载 000001 2022-2024 年的年报"

## 不应触发

- 用户只想看已有 PDF 的内容（用 PDF 阅读类 skill）
- 用户提供非 A 股代码（港股/美股，结构不同）
- 用户想下载季报/半年报（需修改公告类别）

## 工具集

- `mcp__plugin_playwright_playwright__*` 浏览器自动化
- `Read` / `Bash` / `Glob` 读 mapping.json、移动文件
- 不用 `Write`（不修改源文件）

## 相关资源

- `references/szse-flow.md` — 深交所年报下载完整步骤
- `references/sse-flow.md` — 上交所年报下载完整步骤（含日期范围计算）

# PDF 切分：财务报告 / 业务报告

> 把年报 PDF 切分成两份，**规避 MinerU API 的 200 页限制**。

## 背景

MinerU 在线 API 单文件限 200 页（实测宁德时代 2025 年报 232 页），直接调必失败。
解决方案：先按"最后一节 = 财务报告"切分成两份，每份独立解析，避开限制。

## 产物

| 部分 | 文件名 | 页数（示例：宁德 2025）|
|---|---|---|
| 业务报告（非财务，含目录/管理层讨论等）| `REPORT_DATA_PATH/{公司}/pdf/split/{原名}_业务报告.pdf` | 106 |
| 财务报告（最后一节）| `REPORT_DATA_PATH/{公司}/pdf/split/{原名}_财务报告.pdf` | 126 |
| **合计** | — | **232 = 232** ✓|

两份均 < 200 页，可独立调 MinerU。

## 切分算法：多信号综合定位

不要硬限字号（不同年报字号差异大），用一组**结构特征**综合判断：

| 信号 | 章节标题特征 | 正文 / 目录文字特征 |
|---|---|---|
| **文本** | 含"第X节 财务报告" | 包含"财务报告"也包含其他文字 |
| **居中** | 水平居中（年报章节标题排版习惯）| 左对齐（正文 / 目录）|
| **字号相对** | 该行字号是**本页 top N**（自适应）| 字号接近正文 9-10pt |
| **独立成行** | 整行只有标题文字（< 30 字符）| 连续段落，每行几十~上百字符 |
| **垂直位置** | 在页面上 30% 顶部区域 | 散布在页面中部 |
| **目录页码参考** | 目录里"第X节 财务报告"对应的页码 | 目录页本身 |

### 5 必选 + 1 参考

```python
# app/services/pdf_split_service.py
SECTION_RE = re.compile(r"第[一二三四五六七八九十百]+节\s*财务报告")
TOC_MARKER_RE = re.compile(r"^\s*目\s*录\s*$", re.MULTILINE)
SECTION_TOC_RE = re.compile(r"第[一二三四五六七八九十百]+节\s*财务报告[\s.\u3000]+(\d+)")

CENTER_TOLERANCE = 0.20   # 行中心到页中心的距离 / 页宽
TOP_FRACTION = 0.30       # 标题应在页面上 30% 内
TOP_N_SIZES = 3           # 字号相对 top 3（自适应，不用绝对字号）
MAX_TITLE_CHARS = 30      # 标题行字符数上限（独立成行）
```

**关键设计**：
- **不硬限字号**：`size_threshold` 由该页实际字号分布算 top N，自适应不同 PDF
- **5 个必选信号缺一不可**（text + 页顶 + top 字号 + 短行 + 居中），大幅降低误命中
- **目录页码仅做参考**：不直接定位，仅在多候选时加权；目录解析失败也不阻塞
- **多候选时取 page 较大的**（`key=lambda c: (-c[2], -c[0])`）：目录在前几页，正文靠后，相同 score 取更靠后的页 = 优先正文

### 实测验证

宁德时代 2025（232 页）探测结果：

| 候选 | size | len | y0 | 居中 | 通过 5 必选？|
|---|---|---|---|---|---|
| p6 目录行 | 10.6 | 159 | 398 | ✓ | ❌ `len > 30` 拒 |
| **p106 正文标题** | **16.0** | **8** | **87** | **✓** | ✅ **全部通过** |
| 其他"详见第八节..."引用 | 9~10.6 | 41~59 | — | — | ❌ `len > 30` 拒 |

## API

### `POST /companies/{name}/split-pdf?year=...`

**同步执行**（PDF < 10MB，几秒完事）。

**请求示例**：
```bash
curl -X POST "http://127.0.0.1:8000/companies/宁德时代/split-pdf?year=2025"
```

**响应示例**：
```json
{
  "company": "宁德时代",
  "year": 2025,
  "finance_pdf": "宁德时代/pdf/split/宁德时代2025年年度报告_财务报告.pdf",
  "other_pdf": "宁德时代/pdf/split/宁德时代2025年年度报告_业务报告.pdf",
  "finance_start_page": 106,
  "total_pages": 232,
  "title_text": "第八节 财务报告",
  "message": "PDF 切分完成"
}
```

**错误码**：
- `404 公司不存在`
- `404 {year} 年年报未上传，请先 POST /upload`
- `500 未找到'第X节 财务报告'一级标题`（PDF 不是标准年报格式）

### 数据库字段（`annual_report` 表新增）

| 字段 | 类型 | 说明 |
|---|---|---|
| `split_status` | `VARCHAR(16)` | `pending` / `splitting` / `done` / `failed` / `null` |
| `finance_pdf_path` | `VARCHAR(512)` | 相对 `REPORT_DATA_PATH`，如 `宁德时代/pdf/split/.../..._财务报告.pdf` |
| `other_pdf_path` | `VARCHAR(512)` | 相对 `REPORT_DATA_PATH`，如 `宁德时代/pdf/split/.../..._业务报告.pdf` |

老 DB 迁移（一次性，已部署环境执行）：
```sql
ALTER TABLE annual_report ADD COLUMN split_status VARCHAR(16);
ALTER TABLE annual_report ADD COLUMN finance_pdf_path VARCHAR(512);
ALTER TABLE annual_report ADD COLUMN other_pdf_path VARCHAR(512);
```

## 关键文件

| 文件 | 作用 |
|---|---|
| `app/services/pdf_split_service.py` | **核心 service**：多信号定位 + 切分 + DB 写入 |
| `app/routers/companies.py` | `POST /split-pdf` 端点（仿 `preliminary-parse` 形状）|
| `app/schemas/pdf_split.py` | `SplitPDFResponse` 响应体 |
| `app/models/annual_report.py` | 加 3 字段 |
| `app/schemas/annual_report.py` | `AnnualReportRead` 加 3 Optional |
| `frontend/src/types/api.ts` | 前端类型对齐（加 3 Optional）|
| `tests/test_pdf_split_service.py` | 5 个测试（含 mock PDF 端到端）|
| `pyproject.toml` | 加 `pymupdf>=1.24.0` |

## 静态访问

切分产物在 `REPORT_DATA_PATH` 下（具体在 `{公司}/pdf/split/`），可通过 `/static/raw/{rel_path}` 路由直接下载：

```bash
# 财务报告
curl -I "http://127.0.0.1:8000/static/raw/宁德时代/pdf/split/宁德时代2025年年度报告_财务报告.pdf"
# → 200, application/pdf, ~1.3 MB

# 业务报告
curl -I "http://127.0.0.1:8000/static/raw/宁德时代/pdf/split/宁德时代2025年年度报告_业务报告.pdf"
# → 200, application/pdf, ~1.2 MB
```

## 下游使用

切分完后，可分别调 MinerU 解析：

```bash
# 财务报告解析（用现有 preliminary-parse 或全解析）
curl -X POST "http://127.0.0.1:8000/companies/宁德时代/preliminary-parse?year=2025" \
  --data-urlencode "pdf_path=宁德时代/pdf/split/宁德时代2025年年度报告_财务报告.pdf"
# 同理解析业务报告
```

> 后续可加"切分+解析"组合端点（用户当前决策：只做切分，解析单独触发）。

## 范围外（明确不做）

- ❌ 切分后自动调 MinerU 解析（只切不解析）
- ❌ 多级 / 子章节切分（只切"最后一节 财务报告"）
- ❌ TOC 抽取（年报无 outline，文本正则已足够）
- ❌ 支持"第十一节 备查文件目录"等附属节
- ❌ 前端 UI 按钮（API 通后另开 issue；类型补齐是为以后 UI 铺路）
- ❌ 重复切分去重（每次都重新切，浪费几十 MB 但不阻塞功能）

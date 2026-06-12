# md_table_parser：年报 Markdown 中 HTML 表格解析

> 从年报 markdown 文件（含 `<table>...</table>` HTML 块）抽取结构化表格。
> 输出 `List[TableInfo]` Python 对象，**不写盘**。

## 背景

年报 markdown 是 MinerU / pdf_split 等前置流程的产物。每章的 md 里
嵌入大量 `<table>` 块（HTML 形式），需要：

1. **解析所有表格的内容**（保留 rowspan/colspan 后的二维网格）
2. **抽取标题**（取自表格上方的最近 Markdown 标题）
3. **抽取单位**（来自两处：表格上方 `单位：xxx` 行 / 列名末尾括号 `(千元)`）
4. **年份标准化**（把 `本报告期`/`本期`/`上年同期`/... 映射为具体 `YYYY年`）

### 现状

- **外部已有可复用的 HTML 表格解析器** `D:/quant/deep-research-report/shared/tools/table_parser.py`
  - 0 第三方依赖，纯 stdlib `html.parser`，完整处理 rowspan/colspan
  - 已通过 `app/config.py::inject_external_paths()` 注入 `sys.path`，可直接 `import table_parser`
  - **本模块不复制其源码**，仅 import 复用
- 报告期年份推断：优先从父目录名（如 `/2024/`）取，文件名 stem 兜底
- **不**做：8 类表格分类、多年合并、CSV 落盘、FastAPI 路由

## 产物

| 字段 | 类型 | 说明 |
|---|---|---|
| `TableInfo.source_path` | `Path` | 源 md 文件绝对路径 |
| `TableInfo.table_index` | `int` | 在该 md 中第几个 `<table>`（0-based） |
| `TableInfo.report_year` | `int` | 报告期年份（从父目录推断） |
| `TableInfo.title` | `Optional[str]` | 表格上方最近的 Markdown 标题（已剥前置序号） |
| `TableInfo.title_level` | `Optional[int]` | 标题级别 1-6（`#` 个数） |
| `TableInfo.unit` | `Optional[UnitInfo]` | 单位信息（见下文） |
| `TableInfo.headers` | `List[List[str]]` | 多级表头（已年份改写），每行 = 1 层 |
| `TableInfo.data_grid` | `List[List[str]]` | 二维数据网格：`None` → `""`、折行已合并、文本已清洗 |
| `TableInfo.row_count` | `int` | `len(data_grid)` |
| `TableInfo.col_count` | `int` | 列数 |
| `TableInfo.header_columns` | `List[HeaderColumn]` | 展平后每列的元信息（长度 = `col_count`） |
| `TableInfo.year_mapping` | `YearMapping` | 报告期 + 相对年份映射 + 替换处数 |
| `TableInfo.raw_html` | `str` | 完整 `<table>...</table>` 原文 |
| `TableInfo.raw_offset` | `int` | 在原 md 文本中的字符起始偏移 |

## 关键算法

### 1. 报告期年份推断

```
优先级 1: 任一父目录名是 4 位数字（1990~2100）→ 取之
优先级 2: 文件名 stem 含 (19|20)\d{2} → 取第一个出现
都拿不到 → 抛 ReportYearInferenceError
```

### 2. 表格位置定位

```python
_TABLE_BLOCK_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
for m in _TABLE_BLOCK_RE.finditer(md_text):
    locations.append(TableLocation(start=m.start(), end=m.end(), raw_html=m.group(0), ...))
```

不引入 BeautifulSoup（保持零新依赖）。

### 3. 标题与单位定位

- **标题**：从表格 `start` 偏移向前扫所有 `^#{1,6}\s+(.+?)$` 行（`re.MULTILINE`），取**最后一个**（最贴近表格）。解析出 level + 标题文本，剥常见前导序号（`1）`/`（1）`/`一、`）。
- **单位**：在"上一个标题结束位置 ~ `<table` 之间"范围内匹配 `^\s*单位\s*[:：]\s*(.+?)$`。允许多条（合并到 `raw_lines`，`primary` 取第一条）。先清掉方框字符避免误判。

### 4. 年份标准化映射表

| 输入模式 | 输出 | 改写? | 标记 `is_yoy`? |
|---|---|---|---|
| `\d{4}\s*年`（显式年） | 保持 | 否 | 否 |
| `本报告期` / `本期` / `本年度` / `本年` / `当期` / `报告期末` | `{Y}年` | 是 | 否 |
| `上年度` / `上年末` / `上年同期` / `上期` / `期初` / `年初` / `去年` | `{Y-1}年` | 是 | 否 |
| `前年` / `上上年` / `两年前` | `{Y-2}年` | 是 | 否 |
| `同比` / `比上年同期` / `变动比例` / `增减` / `yoy` / `变化率` | 保持 | **否** | **是** |

**关键边界**：
- `"营业收入比上年同期增减"` 同时含 `"上年同期"` 和 `"增减"`，必须由 `detect_yoy_column` 在装配层**优先**判定为 yoy，避免被 `normalize_year_in_cell` 改写
- 裸 `报告期` / `报告期内` 不放匹配（避免误匹配 `报告期内取得和处置子公司方式` 这类描述性文本）
- 裸 `变化` 不放匹配（避免误匹配 `是否发生重大变化` 这类是非列），只匹配 `变化率`

### 5. 文本清洗

| 模式 | 处理 |
|---|---|
| `$1 5 . 0 1 \%$` | → `15.01%`（去 `$`、去数字间空白、去 `\`） |
| `$200\mathrm{Wh/kg}$` | → `200Wh/kg` |
| 兜底任何 `$...$` | 去 `$` + 折叠空白 |
| `□ ■ ☐ ☒ \u00A0` | 删除 |
| 连续空白 | 折叠为单空格 |
| CJK 字符之间空白 | **删除**（处理 `<td>项目\n名称</td>` 字面换行 → `项目名称`） |
| `None` cell | `""` |

### 6. 折行合并

判定为续行需**同时**满足：
1. 当前行非空 cell 数 ≤ 2
2. 这些非空 cell 都在**最后一列**
3. 不是 `is_group_header` 类型行

合并方式：`prev[last_col] + "\n" + cur[last_col]`。

### 7. Header 校验（3 步，装配层）

外部 `table_parser._is_header_row` 启发式不可靠（白名单过窄 + 过检会重复），本模块在装配层做反向校验：

1. **dedup**：剔除 `parsed.headers` 中与 `parsed.data_grid` 重复的行（处理"行 1 既在 headers 又在 data_grid" bug）
2. **反证**：剩余 `headers` 的第一行若不像 header（含 value 模式 → 逗号/百分号/小数）→ 全部降级为 data
3. **兜底**：`headers` 仍为空时，看 `data_grid[0]` 是否像 header → 若是则取它作 header

**Value 模式**（用于"是否像 header"判断）：`[,%]` 或 `\d+\.\d+`。

### 8. 装配流程

```
1. Header 校验（dedup + 反证 + 兜底）
2. 切分 headers / data_grid（按 n_header_rows 切片）
3. 折行合并（仅对 data 部分）
4. 清洗表头行 + 展平多级表头（" / " 拼接）
5. 构造 HeaderColumn[] + 改写表头中的相对年份
6. 提取列名括号单位 (千元) → UnitInfo.from_column_brackets
7. 清洗 data_grid
8. 对正文 (preceding_text) 做相对年份计数（不改原文）
```

## 公共 API

### 顶层入口

```python
from app.services.md_table_parser import extract_tables_from_md
from pathlib import Path

tables: List[TableInfo] = extract_tables_from_md(
    "D:/quant/report_data/宁德时代/md/clean/宁德时代2024年年报/管理层讨论/2024/04_四、主营业务分析.md"
)
# 或传 Path
tables = extract_tables_from_md(Path("...") / "2024" / "04.md")
```

**返回**：`List[TableInfo]`，按 `<table>` 在文本中出现顺序；无表则 `[]`（不抛）。

**异常**：
- `MdFileNotFoundError` — 路径不存在（同时是 `FileNotFoundError`）
- `MdReadError` — 读文件失败（编码/权限/IO 异常）
- `ReportYearInferenceError` — 无法从父目录或文件名推断报告期年份

### 装配层

```python
from app.services.md_table_parser import (
    extract_tables_from_md_text,  # 给定 md 文本 + 报告期年份，跳过文件 IO
    convert_parsed_table,         # 给定外部 table_parser.ParsedTable → TableInfo
)
```

### 定位层

```python
from app.services.md_table_parser import (
    extract_year_from_md_path,    # Path → int
    find_table_locations,         # md_text → List[TableLocation]
    extract_title_for_offset,     # md_text + offset → (title, level) | (None, None)
    extract_unit_for_offset,      # md_text + table_start + title_end → UnitInfo | None
)
```

### 年份标准化

```python
from app.services.md_table_parser import (
    build_year_mapping,            # report_year → YearMapping
    detect_yoy_column,             # str → bool
    extract_explicit_year,         # str → Optional[int]
    normalize_year_in_cell,        # (str, YearMapping) → (new_str, changed: bool)
    normalize_year_in_text,        # (str, YearMapping) → (new_str, count: int)
)
```

### 文本清洗

```python
from app.services.md_table_parser import (
    clean_cell,                    # Optional[str] → str
    clean_text,                    # 同 clean_cell
    merge_continuation_rows,       # List[List[Optional[str]]] → List[List[str]]
)
```

## 数据结构

```python
@dataclass
class HeaderColumn:
    index: int                  # 在展平后 headers 中的列索引（0-based）
    raw: str                    # 原始表头文本（未做年份改写）
    normalized: str             # 已做年份标准化后的文本；若 is_yoy=True 则与 raw 一致
    is_year: bool               # 是否为年份列（显式 YYYY年 或 被映射的相对年份列）
    is_yoy: bool                # 是否为"同比 / 变化 / 增减"类列
    year_value: Optional[int]   # 解析出的具体年份 YYYY；yoy 列或非年份列为 None
    column_role: str            # "item" | "year" | "yoy" | "amount" | "ratio" | "other"

@dataclass
class UnitInfo:
    raw_lines: List[str]                    # 表格上方所有 "单位：..." 行的原始内容
    primary: Optional[str]                  # 主单位（取 raw_lines[0]）
    from_column_brackets: dict[int, str]    # 列索引 → 该列名末尾括号里提取出的单位

@dataclass
class YearMapping:
    report_year: int           # 从父目录推断出的报告期年份
    current_year: int          # = report_year
    previous_year: int         # = report_year - 1
    year_before_previous: int  # = report_year - 2
    applied_count: int         # 累计替换处数（用于调试 / 日志）

@dataclass
class TableLocation:           # 内部中间 DTO
    start: int
    end: int
    raw_html: str
    title: Optional[str]
    title_level: Optional[int]
    unit: Optional[UnitInfo]
    preceding_text: str        # 标题到 <table> 之间的非空文字

@dataclass
class TableInfo:               # 最终输出
    source_path: Path
    table_index: int
    report_year: int
    title: Optional[str]
    title_level: Optional[int]
    unit: Optional[UnitInfo]
    headers: List[List[str]]
    data_grid: List[List[str]]
    row_count: int
    col_count: int
    header_columns: List[HeaderColumn]
    year_mapping: YearMapping
    raw_html: str
    raw_offset: int
```

## 异常层级

```
MdTableError (Exception)
├── MdFileNotFoundError (FileNotFoundError) — 路径不存在
├── MdReadError (IOError)                   — 读文件失败
├── ReportYearInferenceError (ValueError)   — 父目录/文件名无 4 位年份
│   属性: path, candidates
└── NoTableFoundError                       — md 中无 <table>（默认不抛，仅供严格模式）
```

调用方可用 `except MdTableError` 统一捕获，或用多重继承（`except FileNotFoundError`）兼容标准库。

## 使用示例

### 最小用法

```python
from pathlib import Path
from app.services.md_table_parser import extract_tables_from_md

p = Path("D:/quant/report_data/宁德时代/md/clean/宁德时代2024年年报/管理层讨论/2024/04_四、主营业务分析.md")
tables = extract_tables_from_md(p)

print(f"共 {len(tables)} 张表")
for t in tables:
    unit = t.unit.primary if t.unit else None
    print(f"[{t.table_index}] {t.title} | 单位={unit} | {t.row_count}行 x {t.col_count}列")
```

### 遍历年份列和同比列

```python
for t in tables:
    year_cols = [(hc.index, hc.normalized, hc.year_value) for hc in t.header_columns if hc.is_year]
    yoy_cols = [(hc.index, hc.normalized) for hc in t.header_columns if hc.is_yoy]
    print(f"[{t.table_index}] 年份列: {year_cols}")
    print(f"          同比列: {yoy_cols}")
```

预期（2024 年报）：
```
[0] 营业收入整体情况
    年份列: [(1, '2024年', 2024), (3, '2023年', 2023)]
    同比列: [(5, '同比增减')]
[5] 重大销售合同
    年份列: [(3, '2024年履行金额', 2024), (5, '2024年确认的销售收入金额', 2024)]
[12] 费用
    年份列: [(1, '2024 年', 2024), (2, '2023 年', 2024)]
    同比列: [(3, '同比增减')]
```

### 直接调用装配层（跳过文件 IO）

```python
from pathlib import Path
from app.services.md_table_parser import extract_tables_from_md_text

md_text = Path("...").read_text(encoding="utf-8")
tables = extract_tables_from_md_text(md_text, report_year=2024, source_path=Path("..."))
```

适用场景：md 文本已在内存（如从 DB 读出）、批量处理需复用同一份文本。

### 错误处理

```python
from app.services.md_table_parser import (
    extract_tables_from_md, MdFileNotFoundError, MdReadError,
    ReportYearInferenceError, MdTableError,
)

try:
    tables = extract_tables_from_md(p)
except MdFileNotFoundError:
    print(f"文件不存在: {p}")
except MdReadError as e:
    print(f"读文件失败: {e}")
except ReportYearInferenceError as e:
    print(f"无法推断年份: {e.path}, 候选: {e.candidates}")
except MdTableError as e:
    print(f"其他解析错误: {e}")
```

## 关键文件

| 文件 | 作用 |
|---|---|
| `app/services/md_table_parser/__init__.py` | re-export 全部公开 API + `__all__` |
| `app/services/md_table_parser/parser.py` | 顶层入口 `extract_tables_from_md` |
| `app/services/md_table_parser/table_extractor.py` | 装配层 `convert_parsed_table` + `extract_tables_from_md_text`，含 Header 校验 3 步 |
| `app/services/md_table_parser/title_unit_locator.py` | 表格位置 + 标题 + 单位定位 |
| `app/services/md_table_parser/year_normalizer.py` | 相对年份 → `YYYY年` 映射 + YoY 列识别 |
| `app/services/md_table_parser/text_cleaner.py` | LaTeX 剥离 / 方框字符 / 折行合并 / CJK 词内空白移除 |
| `app/services/md_table_parser/models.py` | 5 个 dataclass DTO |
| `app/services/md_table_parser/exceptions.py` | 5 个异常类 |
| `tests/test_md_table_parser.py` | 35 个测试用例 |

## 外部依赖

- **`table_parser`（来自 `D:/quant/deep-research-report/shared/tools/table_parser.py`）**
  - 0 第三方依赖，stdlib `html.parser`
  - 装配层通过 `_import_external_table_parser()` 惰性导入，**自动注入 `shared/tools/` 到 sys.path**
  - 不修改其源码（仅 import）
- **本模块不引入新第三方依赖**

## 测试覆盖

`pytest tests/test_md_table_parser.py` — **35/35 通过**

| 类别 | 用例数 | 覆盖点 |
|---|---|---|
| 年份推断 | 3 | 父目录 4 位数 / 文件名 stem 兜底 / 抛错 |
| LaTeX 清洗 | 4 | `1 5 . 0 1 \%$` / `200\mathrm{Wh/kg}` / 兜底 `$...$` / 方框字符 |
| CJK 词内空白 | 1 | 半角/全角/换行；保留 CJK-拉丁 / 数字-CJK 边界 |
| 折行合并 | 2 | 基本合并 / 不合并 |
| 标题/单位定位 | 2 | 真实 2024 解析 / 单元测试 |
| Header 兜底 | 2 | 首列不在白名单 → 兜底 / "字段|值" KV 摘要表不误判 |
| 表头年份标准化 | 2 | `本报告期`/`上年同期` → 2024/2023；同比列保留 |
| 列名括号单位 | 1 | `销售额（千元）` → `from_column_brackets` |
| 同比列识别 | 7 | 4 个正向（同比/上年同期/变动比例等）+ 3 个反向（"重大变化"类是非列不误判） |
| 异常体系 | 1 | 继承关系 |
| 真实数据回归 | 3 | 2024 解析 ≥ 5 张表 / 2025 KV 摘要 / 2025 CJK 表头 |
| 杂项 | 7 | `build_year_mapping` / `extract_explicit_year` / `normalize_year_in_text` / `find_table_locations` 基础 |

## 已知 limitation

- **key-value 摘要表（如 "前五名客户合计销售金额 | 165,061,533"）**：`headers=[]`，数据在 `data_grid`。调用方需按 `col_count=2` + 短文本识别自行处理。
- **HTML 单元格内字面换行**已被 `clean_cell` 移除（CJK 字符间空白移除规则）。
- **多级表头（rowspan/colspan 展开后）**：已通过 ` / ` 拼接相邻层文本；若某列在所有 header 层都为空，回退到 `""`。
- **外部 `table_parser` 的 `is_header_row` 启发式**：仅识别首列含 "项目/客户/供应商/序号/产品种类/时间/接待" 或任意 cell 含 `\d{4}年` 的行。本模块已用 3 步 Header 校验覆盖其 false negative 和 false positive，但仍依赖行级"是否像 header"判断（`row_looks_like_header` 用 `[,%]` 和 `\d+\.\d+` 作为 value 模式）。极端 case 可能误判。

## 范围外（明确不做）

- ❌ 8 类表格分类（`TableType` 枚举）
- ❌ 多年份合并到统一 CSV
- ❌ FastAPI 路由 / SQLAlchemy 落库
- ❌ 修复 `workspace/table_extract/interfaces/__init__.py` 的 broken import
- ❌ 修改外部 `D:/quant/deep-research-report/shared/tools/table_parser.py` 源码

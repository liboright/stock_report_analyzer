# 表格提取功能架构增强方案

## 1. 模块概述

- **模块名称**: `table_extract`
- **职责**: 从年报 Markdown 文件中解析 HTML 表格，分类后合并多年数据输出为 CSV
- **依赖关系**: 依赖 `table_parser`（解析）和 `table_extractor`（主流程），由 `table_extract.py` 入口调用
- **输出位置**: `D:\quant\report_database\md\{公司}\output\tables\`

```
输入 → TableParser → TableExtractor → 8类CSV
       (rowspan处理)  (分类/合并)     (输出)
```

## 2. 文件结构

```
table_extract/
├── __init__.py            - 包导出，主要公开接口
├── core.py                - 主流程类 TableExtractor
├── parser.py              - HTML 表格解析器（增强 rowspan）
├── classifier.py         - 表格分类逻辑
├── merger.py              - 多年份数据合并与列对齐
├── models.py              - 数据结构定义（dataclass）
├── utils.py               - 工具函数
└── exceptions.py          - 异常类型

interfaces/                - 接口定义（不含实现）
├── __init__.py
├── core.py               - TableExtractor 核心类接口
├── parser.py             - TableParser 接口
├── models.py             - 数据结构类型定义
└── exceptions.py         - 异常类型定义
```

## 3. 数据结构定义（models.py）

```python
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum

class TableType(Enum):
    """表格类型枚举"""
    REVENUE_COMPOSITION = "营业收入构成"      # 收入构成分析
    EXPENSE_ANALYSIS = "费用分析"            # 费用率分析
    CASH_FLOW = "现金流"                     # 现金流分析
    RD_INVESTMENT = "研发投入"               # 研发投入
    PRODUCTION_CAPACITY = "产能产量"         # 产能产量
    PRODUCTION_SALES = "产销情况"            # 产销情况
    CUSTOMER_SUPPLIER = "客户供应商"         # 客户供应商
    TECH_PARAMS = "技术参数"                # 技术参数
    OTHER = "其他"

@dataclass
class CellPosition:
    """单元格位置（用于 rowspan 追踪）"""
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1

@dataclass
class TableCell:
    """表格单元格"""
    value: str
    row: int                    # 起始行
    col: int                    # 起始列
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False

@dataclass
class ParsedTable:
    """解析后的完整表格（含 rowspan 网格）"""
    headers: List[List[str]]            # 多级表头行
    data_grid: List[List[Optional[str]]] # 二维数据网格（None = 被rowspan覆盖）
    row_count: int
    col_count: int
    raw_html: str = ""

@dataclass
class TableInfo:
    """单个表格的完整信息"""
    year: str
    title: str
    section: str
    unit: str
    table_type: TableType
    headers: List[List[str]] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    raw_html: str = ""

@dataclass
class ColumnMapping:
    """列对齐映射（用于多年份合并）"""
    source_col: int
    target_col: int
    semantic: str  # 如 "金额", "占比", "同比"

@dataclass
class MergedData:
    """合并后的表格数据"""
    table_type: TableType
    item_col: str = "项目"
    years: List[str] = field(default_factory=list)
    headers: List[List[str]] = field(default_factory=list)  # 多级表头
    data: List[List[str]] = field(default_factory=list)
    column_mappings: List[ColumnMapping] = field(default_factory=list)  # 列语义映射

class TableParseError(Exception):
    """表格解析异常"""
    pass

class ColumnAlignError(Exception):
    """列对齐异常"""
    pass
```

## 4. 核心接口定义

### 4.1 parser.py - HTML 表格解析器

```python
class TableParser:
    """
    HTML表格解析器
    完整支持 rowspan 和 colspan，维护单元格网格
    """

    def parse(self, html: str) -> ParsedTable:
        """
        解析HTML表格为二维网格

        处理逻辑：
        1. 创建 row_count x col_count 的空网格
        2. 遍历 <tr>，为每个单元格确定实际位置
        3. rowspan 单元格内容复制到后续行的同列位置
        4. colspan 展开为多个列
        5. 返回展开后的网格数据

        Returns:
            ParsedTable: 包含 data_grid（None 表示被合并单元格覆盖）
        """
        ...

    def parse_multiple(self, html_content: str) -> List[ParsedTable]:
        """
        从 HTML 内容中提取所有表格

        Args:
            html_content: 包含多个表格的 HTML 字符串

        Returns:
            List[ParsedTable]: 解析后的表格列表
        """
        ...
```

### 4.2 classifier.py - 表格分类器

```python
class TableClassifier:
    """
    表格分类器
    使用多级关键词匹配 + 上下文感知，提高分类精确度
    """

    def classify(self, table: ParsedTable, title: str = "", section: str = "") -> TableType:
        """
        根据表格内容分类

        Args:
            table: ParsedTable 对象
            title: 表格标题（来自 Markdown）
            section: 所属小节

        Returns:
            TableType: 分类结果

        分类策略：
        1. 先检查排除关键词（is_exclude_table）
        2. 多级匹配：优先精确匹配，其次模糊匹配
        3. 考虑上下文（标题、小节）提高准确性
        4. 避免误分类：产销情况 vs 产能产量
        """
        ...

    def is_exclude_table(self, table: ParsedTable, title: str) -> bool:
        """检查是否应该排除（非结构化数据）"""
        ...

    def get_match_score(self, table: ParsedTable, table_type: TableType) -> float:
        """计算表格与类型的匹配分数"""
        ...
```

### 4.3 merger.py - 数据合并器

```python
class TableMerger:
    """
    多年份表格数据合并器
    处理列数不一致、对齐、空值填充
    """

    def merge(self, tables_by_year: Dict[str, List[TableInfo]], table_type: TableType) -> MergedData:
        """
        合并多年份数据

        Args:
            tables_by_year: {"2025": [TableInfo,...], "2024": [...]}
            table_type: 要合并的表格类型

        Returns:
            MergedData: 合并后的数据

        列对齐策略：
        1. 识别表头结构（如 "金额/占比" 子表头）
        2. 按语义列对齐（同列位置比较，而非名称比较）
        3. 缺失列用 "-" 填充
        4. 多年份重复列模式检测
        """
        ...

    def align_columns(self, tables: List[TableInfo]) -> List[ColumnMapping]:
        """
        对齐不同年份的列

        Returns:
            List[ColumnMapping]: 列映射关系
        """
        ...

    def detect_header_structure(self, headers: List[List[str]]) -> Dict[str, Any]:
        """
        检测表头结构（多级表头）

        Returns:
            {"main_headers": [...], "sub_headers": [...], "span": N}
        """
        ...
```

### 4.4 core.py - TableExtractor 主流程

```python
class TableExtractor:
    """
    表格提取主流程
    协调 parser、classifier、merger
    """

    def __init__(self, company: str, base_dir: str):
        """
        Args:
            company: 公司名（如 "宁德时代"）
            base_dir: 基础目录
        """
        ...

    def extract(self) -> Dict[TableType, MergedData]:
        """
        执行完整提取流程

        1. 扫描 md/{公司}/output/mid_file/管理层讨论/{年份}/*.md
        2. 解析每个文件中的 HTML 表格
        3. 分类表格（8 类）
        4. 合并多年数据
        5. 输出 CSV

        Returns:
            Dict[TableType, MergedData]: 类型到合并数据的映射
        """
        ...

    def extract_from_md(self, md_path: str) -> List[TableInfo]:
        """从单个 md 文件提取表格"""
        ...

    def save_to_csv(self, merged: MergedData, output_path: str) -> None:
        """保存为 CSV（支持双行表头）"""
        ...
```

## 5. rowspan 处理改造

### 5.1 问题分析

当前实现的问题：
1. `rowspan` 属性被忽略（代码注释 "暂不处理"）
2. 多行 rowspan 跨行数据被截断
3. rowspan 单元格的后续行没有正确填充

### 5.2 改造方案

**核心思路**：建立二维网格，rowspan 单元格内容复制到后续行

```
原始 HTML:
<tr><td rowspan="2">A</td><td>B</td></tr>
<tr><td>C</td></tr>              <- rowspan 单元格 "A" 应该出现在这里

解析后网格（row_count=2, col_count=2）:
[ ["A", "B" ],      <- 行0: A(rowspan=2) 在列0，B 在列1
  ["A", "C" ] ]     <- 行1: A(rowspan=2) 延续到列0，C 在列1
```

**关键算法**：

```python
def _build_grid(self, rows: List[List[TableCell]]) -> List[List[Optional[str]]]:
    """
    构建二维数据网格，处理 rowspan 复制

    1. 计算最大行数和列数
    2. 创建 row_count x col_count 的空网格
    3. 填充单元格，遇到 rowspan 将内容复制到后续行
    4. 追踪已填充位置，避免覆盖
    """
    # 计算网格维度
    row_count = max(cell.row + cell.rowspan for row in rows for cell in row)
    col_count = max(cell.col + cell.colspan for row in rows for cell in row)

    # 创建空网格
    grid = [[None] * col_count for _ in range(row_count)]
    filled = [[False] * col_count for _ in range(row_count)]  # 追踪占用

    # 填充单元格
    for row in rows:
        for cell in row:
            for r in range(cell.row, cell.row + cell.rowspan):
                for c in range(cell.col, cell.col + cell.colspan):
                    if r < row_count and c < col_count:
                        # rowspan 复制：只要没有被更晚的单元格占用，就填充
                        if not filled[r][c]:
                            grid[r][c] = cell.value
                        filled[r][c] = True

    return grid
```

### 5.3 边界条件

| 情况 | 处理策略 |
|------|----------|
| rowspan 超出表格行数 | 裁剪到表格实际行数 |
| colspan + rowspan 组合 | 先展开 colspan，再复制 rowspan |
| 多个 rowspan 重叠 | 先到先得，后续检查 filled 标志 |
| 空 rowspan (rowspan=0) | 按 rowspan=1 处理 |

## 6. 表格分类逻辑改进

### 6.1 当前问题

1. 关键词匹配过于简单，无法区分"产销情况"和"产能产量"
2. 忽略上下文（标题、小节）
3. 缺少多级匹配机制

### 6.2 改进方案

**分类决策树**：

```
1. 检查排除关键词 → 排除
2. 检查客户供应商特征 → 客户供应商
3. 检查产销情况特征（销售量/生产量/库存量）→ 产销情况
4. 检查产能产量特征（产能/GWh 但无销售量）→ 产能产量
5. 检查研发投入特征 → 研发投入
6. 检查技术参数特征 → 技术参数
7. 检查现金流特征 → 现金流
8. 检查费用分析特征 → 费用分析
9. 检查营业收入构成特征 → 营业收入构成
10. 其他 → 其他
```

**匹配分数计算**：

```python
def get_match_score(self, table: ParsedTable, table_type: TableType) -> float:
    """
    计算匹配分数，考虑：
    1. 关键词出现次数
    2. 关键词位置（标题 > 表头 > 数据）
    3. 互斥关键词（排除项）
    4. 上下文权重
    """
    score = 0.0
    all_text = self._get_context_text(table)  # 包含标题、小节、表格内容

    for kw in TYPE_KEYWORDS[table_type]:
        if kw in all_text:
            # 标题中出现给权重 3，表头中给权重 2，数据中给权重 1
            weight = self._get_keyword_weight(kw, table)
            score += weight

    # 减去排除关键词
    for ex_kw in TYPE_EXCLUDE_KEYWORDS[table_type]:
        if ex_kw in all_text:
            score -= 2

    return score
```

### 6.3 关键区分逻辑

**产销情况 vs 产能产量**：
```python
# 产销情况必须有：销售量 或 生产量 或 库存量
# 产能产量特征：产能、GWh，但不包含销售量/库存量
has_sales = any("销售量" in row for row in table.data_grid)
has_capacity = any("产能" in row or "GWh" in row for row in table.data_grid)

if has_sales:
    return TableType.PRODUCTION_SALES
elif has_capacity:
    return TableType.PRODUCTION_CAPACITY
```

## 7. 多年份合并列对齐策略

### 7.1 问题分析

1. 不同年份列数不一致（有的多一列"占比"）
2. 年份顺序可能错乱
3. 合并时出现列错位

### 7.2 对齐策略

**策略 1：语义列对齐（推荐）**

不按位置对齐，而按语义对齐：
```python
# 识别每列的语义（如 "金额"、"占比"、"同比"）
semantic_map = {
    "2025": {0: "项目", 1: "金额", 2: "占比", 3: "同比"},
    "2024": {0: "项目", 1: "金额", 2: "占比"},
}
# 缺失的"同比"列填充 "-"
```

**策略 2：表头结构检测**

```python
def detect_header_structure(headers: List[List[str]]) -> Dict:
    """
    检测多级表头结构

    例如：
    [ ["项目", "2025年", "", "2024年", ""],
      ["", "金额", "占比", "金额", "占比"] ]

    返回：
    {
        "main_headers": ["项目", "2025年", "2024年"],
        "sub_headers": ["金额", "占比", "金额", "占比"],
        "span": 2  # 子表头跨 2 列
    }
    """
```

**策略 3：列位置对齐（备选）**

按列位置对齐，缺失列用"-"填充：
```python
def align_by_position(tables: List[TableInfo]) -> List[List[str]]:
    """
    按列位置对齐
    2025: [项目, 金额, 占比, 同比]
    2024: [项目, 金额, 占比    ]  <- 缺失同比列

    对齐后：
    2025: [项目, 金额, 占比, 同比]
    2024: [项目, 金额, 占比, -   ]
    """
    max_cols = max(len(t.rows[0]) for t in tables if t.rows)
    # 缺失位置填充 "-"
```

### 7.3 合并算法

```python
def merge_years(self, tables_by_year: Dict[str, List[TableInfo]], table_type: TableType) -> MergedData:
    # 1. 收集所有年份和项目
    all_years = sorted(tables_by_year.keys(), reverse=True)
    all_items = {}  # {item_name: {year: [col1, col2, ...]}}

    # 2. 检测列语义结构
    col_semantics = self._detect_col_semantics(tables_by_year, table_type)

    # 3. 对齐填充
    for item_name, year_data in all_items.items():
        for year in all_years:
            if year not in year_data:
                # 用 "-" 填充缺失列
                year_data[year] = ["-"] * col_count

    # 4. 构建输出
    # 表头格式：项目 | 2025年金额 | 2025年占比 | 2024年金额 | 2024年占比
```

## 8. 多级表头识别

### 8.1 识别场景

年报表格常见多级表头：
```
| 项目 | 2025年 | 2024年 |      ← 第一级：年份
|      | 金额   | 占比   | 金额  | 占比 |  ← 第二级：子列
```

### 8.2 识别算法

```python
def recognize_multi_level_headers(self, rows: List[List[str]]) -> List[List[str]]:
    """
    识别多级表头

    返回：
    [
        ["项目", "2025年", "2024年"],
        ["", "金额", "占比", "金额", "占比"]
    ]
    """
    if not rows:
        return []

    # 找"项目"列位置
    item_col = self._find_item_column(rows[0])
    if item_col == -1:
        return [rows[0]]  # 无法识别，返回单级

    # 检测年份行（包含"2025年"等）
    year_row_idx = self._find_year_row(rows)

    # 如果有年份行，分割为两级表头
    if year_row_idx >= 0:
        main_header = rows[year_row_idx].copy()
        sub_header = rows[year_row_idx + 1] if year_row_idx + 1 < len(rows) else []

        # 填充项目列的空值
        main_header[item_col] = "项目"

        return [main_header, sub_header]

    return [rows[0]]  # 单级表头
```

## 9. 边界条件与异常处理

| 边界情况 | 处理策略 |
|----------|----------|
| rowspan 超出表格范围 | 裁剪 rowspan 值到有效范围 |
| 空表格（无数据行） | 跳过该表格，记录警告 |
| 列数差异过大（>3列） | 发出警告，尝试最佳匹配 |
| 无法识别的表头结构 | 使用第一行作为表头，数据行从第二行开始 |
| 多年份列顺序不一致 | 以最新年份为基准，重新排列旧年份列 |
| 表格被错误分类 | 使用人工规则纠正（如"项目可行性"→ 排除） |
| 单元格内容为空 | 保持为空字符串，不填充"-" |
| colspan 和 rowspan 组合 | 先处理 colspan 展开，再处理 rowspan 复制 |

## 10. 向后兼容

### 10.1 命令行接口

```python
# 保持原有接口不变
python table_extract.py 宁德时代

# 输出目录结构保持不变
# D:/quant/report_database/md/宁德时代/output/tables/
# ├── 营业收入构成.csv
# ├── 费用分析.csv
# ├── 现金流.csv
# ├── 研发投入.csv
# ├── 产能产量.csv
# ├── 产销情况.csv
# ├── 客户供应商.csv
# └── 技术参数.csv
```

### 10.2 CSV 格式兼容

输出格式保持双行表头：
```csv
,2025年,2025年,2024年,2024年
项目,金额,占比,金额,占比
营业收入,1000,100%,800,100%
```

## 11. 实现优先级

1. **P0 - rowspan 修复**：修复多行 rowspan 数据截断问题
2. **P1 - 列对齐**：修复产销情况列错位、多年份合并问题
3. **P2 - 分类精度**：改进关键词匹配，避免误分类
4. **P3 - 多级表头**：支持"金额/占比"类子表头识别
5. **P4 - 异常处理**：完善边界条件处理和日志
# 年报章节检索工具 - 架构设计文档

## 1. 模块概述

### 1.1 模块名称与职责

| 模块 | 位置 | 职责 |
|------|------|------|
| `annual_report_reader` | `deep-research-report/tools/annual_report_reader/` | 现有模块，年报读取底层支持，被增强复用 |
| `annual_report_search` | `deep-research-report/tools/annual_report_search/` | 新增模块，提供年报章节检索功能 |

### 1.2 核心功能

| 函数 | 功能描述 |
|------|----------|
| `build_index(company, year, force_rebuild)` | 为指定公司的年报构建章节索引（含 section_id） |
| `search_sections(query, company, year, top_k)` | 基于自然语言检索匹配的年报章节 |
| `read_section(company, year, section_id)` | 根据章节ID提取章节完整内容 |

### 1.3 与其他模块的依赖关系

```
annual_report_search (新增)
    │
    └──► annual_report_reader (复用)
            ├── models.py    → SectionNode 数据结构
            ├── core.py      → 索引读写
            └── utils.py     → Markdown 解析工具
```

---

## 2. 文件结构

```
deep-research-report/tools/
├── annual_report_reader/              # 现有模块（向后兼容增强）
│   ├── __init__.py                    # 导出核心类和函数
│   ├── models.py                      # SectionNode, IndexCache 数据结构
│   ├── core.py                        # build_index(), read_section()
│   ├── utils.py                       # REPORT_BASE_PATH, parse_markdown_headings()
│   └── exceptions.py                 # ReportNotFoundError, ChapterNotFoundError
│
└── annual_report_search/              # 新增检索模块
    ├── __init__.py                    # 包导出，公开 search_sections, read_section
    ├── search_engine.py               # 检索引擎（核心）
    │       ├── SearchEngine           # 检索引擎类
    │       ├── preprocess_query()     # Query 预处理（分词、停用词过滤、同义词扩展）
    │       └── calculate_match_score() # 匹配打分
    ├── section_id.py                  # 章节ID生成与解析
    │       ├── generate_section_id()   # 生成 section_id（如 "3.1.2"）
    │       ├── extract_number_from_title()  # 从标题提取序号
    │       ├── has_section_number()   # 检查标题是否包含序号
    │       ├── parse_section_id()     # 解析 section_id 为层级路径
    │       └── find_section_node()    # 在章节树中定位节点
    └── constants.py                   # 常量配置
            ├── REPORT_BASE_PATH       # 年报基础路径
            ├── STOPWORDS             # 停用词集合
            └── SYNONYMS              # 同义词词表
```

---

## 3. 数据结构定义

### 3.1 SectionNode（增强）

```python
# annual_report_reader/models.py
@dataclass
class SectionNode:
    section_id: str                    # 章节ID，如 "3.1.2"，无序号则为 ""
    path: list[str]                   # 父节点ID列表，如 ["3", "3.1"]
    title: str                         # 章节标题
    level: int                        # 标题层级 (1/2/3)
    line_start: int                   # 起始行号（全局累积行号）
    line_end: Optional[int]           # 结束行号
    file: str                         # 所属文件名
    file_offset: int                  # 文件在全局的起始行偏移
    subsections: list["SectionNode"]  # 子章节列表
```

### 3.2 SectionMatch（检索结果）

```python
# annual_report_search/search_engine.py
@dataclass
class SectionMatch:
    company: str
    year: str
    section_id: str
    title: str
    level: int
    match_score: float
    file: str
    line_start: int
    line_end: int
```

### 3.3 索引缓存 JSON 结构

```json
{
  "company": "宁德时代",
  "year": "2024",
  "report_dir": "D:\\quant\\report_database\\md\\宁德时代\\input\\宁德时代2024年年度报告",
  "generated_at": "2026-05-16T10:30:00",
  "sections": [
    {
      "section_id": "3",
      "path": [],
      "title": "第三节 管理层讨论与分析",
      "level": 1,
      "line_start": 1,
      "line_end": 2000,
      "file": "3_第三节 管理层讨论与分析.md",
      "file_offset": 1,
      "subsections": [
        {
          "section_id": "3.1",
          "path": ["3"],
          "title": "一、报告期内公司所处行业情况",
          "level": 2,
          "line_start": 3,
          "line_end": 200,
          "file": "3_第三节 管理层讨论与分析.md",
          "file_offset": 1,
          "subsections": [
            {
              "section_id": "3.1.2",
              "path": ["3", "3.1"],
              "title": "2、行业发展状况及发展趋势",
              "level": 3,
              "line_start": 9,
              "line_end": 50,
              "file": "3_第三节 管理层讨论与分析.md",
              "file_offset": 1,
              "subsections": []
            }
          ]
        }
      ]
    }
  ],
  "file_offsets": {
    "3_第三节 管理层讨论与分析.md": 1
  },
  "total_lines": 5000
}
```

---

## 4. 接口定义

### 4.1 build_index

```python
# annual_report_reader/core.py
def build_index(
    company: str,
    year: str,
    force_rebuild: bool = False
) -> dict:
    """
    为指定公司的年报构建章节索引

    参数:
        company: 公司名称，如 "宁德时代"
        year: 报告年度，如 "2024"
        force_rebuild: 是否强制重建索引（跳过缓存），默认 False

    返回:
        dict: {
            "company": str,
            "year": str,
            "sections": list[dict],  # 章节树，每个节点包含 section_id
            "cache_file": str        # 缓存文件路径
        }

    异常:
        ReportNotFoundError: 年报目录不存在
    """
```

### 4.2 search_sections

```python
# annual_report_search/search_engine.py
def search_sections(
    query: str,
    company: str,
    year: str | None = None,
    top_k: int = 5
) -> list[dict]:
    """
    基于自然语言检索匹配的年报章节

    参数:
        query: 自然语言查询，如 "公司主要业务是什么"、"营收情况"
        company: 公司名称，如 "宁德时代"
        year: 可选，限定报告年度，如不指定则搜索公司所有年份
        top_k: 返回最多几条结果，默认 5

    返回:
        list[dict]: [
            {
                "company": str,
                "year": str,
                "section_id": str,      # 如 "3.1.2"
                "title": str,
                "level": int,
                "match_score": float,   # 0-1 之间的匹配得分
                "file": str,
                "line_start": int,
                "line_end": int
            },
            ...
        ]

    异常:
        ReportNotFoundError: 索引文件不存在
    """
```

### 4.3 read_section

```python
# annual_report_search/section_id.py
def read_section(
    company: str,
    year: str,
    section_id: str
) -> dict:
    """
    根据公司名称、年份、章节ID提取该章节的完整内容

    参数:
        company: 公司名称，如 "宁德时代"
        year: 报告年度，如 "2024"
        section_id: 章节ID，如 "3.1.2"

    返回:
        dict: {
            "company": str,
            "year": str,
            "section_id": str,
            "title": str,
            "content": str,           # 章节正文内容（不含章节标题）
            "word_count": int,        # 正文字数
            "file": str,
            "line_start": int,
            "line_end": int
        }

    异常:
        ReportNotFoundError: 索引文件不存在
        ChapterNotFoundError: 章节ID不存在
    """
```

---

## 5. 核心业务逻辑

### 5.1 build_index 流程

```
build_index(company, year, force_rebuild)
│
├─► 1. 确定路径
│      ├─► report_dir = {base}/md/{company}/input/{company}{year}年年度报告/
│      └─► cache_path = {base}/md/{company}/output/navi/{company}_{year}_index.json
│
├─► 2. 检查缓存（force_rebuild=False 时）
│      ├─► 缓存存在 → 加载并返回
│      └─► 缓存不存在或 force_rebuild=True → 继续构建
│
├─► 3. 扫描 report_dir 下的 .md 文件
│      ├─► 匹配模式: r"^\d+_.*\.md$"
│      └─► 排除: "00_目录.md", "报告结构.md"
│
├─► 4. 按文件名数字排序
│
├─► 5. 第一遍：计算全局 file_offsets
│      └─► 累积行号，每个文件记录起始偏移
│
├─► 6. 第二遍：解析文件构建章节树
│      ├─► 提取 # ## ### 标题
│      └─► 调用 _assign_section_ids() 生成 section_id 和 path
│
├─► 7. 保存索引到 JSON
│
└─► 8. 返回索引结构
```

### 5.2 section_id 生成规则

| 层级 | 来源 | 规则 | 示例 |
|------|------|------|------|
| 第1级 | 文件名 | 提取 `_` 之前的数字 | `"03_第三节..."` → `"3"` |
| 第2级 | `##` 标题 | **必须包含序号**（一、1、（1）等）| `"一、主要业务"` → `"3.1"` |
| 第3级 | `###` 标题 | **必须包含序号** | `"2、行业发展"` → `"3.1.2"` |

> **重要**：只有包含序号的标题才分配 section_id，无序号的标题不分配但仍作为层级节点存在。

### 5.3 search_sections 流程

```
search_sections(query, company, year, top_k)
│
├─► 1. 确定搜索范围
│      ├─► 指定 year → 只加载该年索引
│      └─► 未指定 year → 加载公司所有年份索引
│
├─► 2. 加载索引缓存
│      └─► 索引不存在 → 抛出 ReportNotFoundError
│
├─► 3. Query 预处理
│      ├─► 分词（jieba）
│      ├─► 停用词过滤
│      └─► 同义词扩展
│
├─► 4. 展平章节树为列表
│
├─► 5. 多级匹配打分
│      ├─► 精确匹配 → 1.0
│      ├─► 查询词在标题中 → 0.8
│      ├─► 同义词匹配 → 0.6
│      └─► 部分字符串匹配 → 0.3
│
├─► 6. 排序并返回 top_k
```

### 5.4 read_section 流程

```
read_section(company, year, section_id)
│
├─► 1. 加载索引缓存
│
├─► 2. 解析 section_id
│      └─► "3.1.2" → ["3", "3.1", "3.1.2"]
│
├─► 3. 在章节树中定位节点
│      ├─► 第1级：在 sections 中找 section_id == "3"
│      ├─► 第2级：在 subsections 中找 section_id == "3.1"
│      └─► 第3级：在 subsections 中找 section_id == "3.1.2"
│
├─► 4. 计算局部行号
│      ├─► local_line_start = line_start - file_offset + 1
│      └─► local_line_end = line_end - file_offset + 1
│
├─► 5. 读取文件并提取内容（跳过标题行）
│
└─► 6. 返回结果（含 word_count）
```

---

## 6. 章节ID体系

### 6.1 ID 格式

采用层级点号 notation：`"3.1.2"` 表示：
- 第3节（文件级）
- 第1个子章节（## 标题）
- 第2个子子章节（### 标题）

### 6.2 序号识别正则

```python
# annual_report_search/section_id.py
NUMBER_PATTERNS = [
    r'^（[一二三四五六七八九十]+）',  # （一）、（二）...
    r'^（[0-9]+）',                  # （1）、（2）...
    r'^[一二三四五六七八九十]+、',   # 一、二、三...
    r'^[0-9]+、',                    # 1、2、3...
    r'^[0-9]+\.',                    # 1. 2. 3.
]
```

### 6.3 path 字段

每个节点的 `path` 字段表示父节点 ID 列表（不含自身）：

| 节点 | section_id | path |
|------|-----------|------|
| 第三节 | `"3"` | `[]` |
| 一、行业情况 | `"3.1"` | `["3"]` |
| 2、行业发展 | `"3.1.2"` | `["3", "3.1"]` |

---

## 7. 常量配置

### 7.1 路径配置

```python
# annual_report_search/constants.py
from pathlib import Path

REPORT_BASE_PATH: Path = Path(r"D:\quant\report_database\md")
INDEX_CACHE_DIR: Path = Path(r"output/navi")
ANNUAL_REPORT_DIR: Path = Path(r"input")
```

### 7.2 停用词

```python
STOPWORDS: set[str] = {
    '的', '是', '了', '在', '和', '与', '或', '什么',
    '怎么', '如何', '吗', '呢', '的', '了', '是'
}
```

### 7.3 同义词词表

```python
SYNONYMS: dict[str, list[str]] = {
    "业务": ["业务", "主要业务", "经营", "营业范围"],
    "营收": ["营收", "营业收入", "收入", "销售", "营业额"],
    "利润": ["利润", "净利润", "盈利", "收益"],
    "资产": ["资产", "总资产", "净资产"],
    "负债": ["负债", "总负债", "债务"],
    "股东": ["股东", "股权", "股份", "持有人"],
    "管理": ["管理", "管理层", "经营", "管理层讨论"],
    "财务": ["财务", "财务数据", "会计", "财务指标"],
    "行业": ["行业", "产业", "市场", "竞争"],
    "风险": ["风险", "风险因素", "不确定性"],
}
```

---

## 8. 异常类型

```python
# annual_report_reader/exceptions.py
class ReportNotFoundError(Exception):
    """年报目录或索引文件不存在"""
    pass

class ChapterNotFoundError(Exception):
    """章节ID不存在"""
    pass
```

| 异常 | 触发场景 |
|------|----------|
| `ReportNotFoundError` | 年报目录不存在、索引文件不存在 |
| `ChapterNotFoundError` | section_id 在索引中不存在 |

---

## 9. 边界条件与异常处理

| 边界情况 | 处理策略 |
|----------|----------|
| section_id 格式错误或为空 | 抛出 `ChapterNotFoundError` |
| 章节内容为空 | 返回空字符串，`word_count = 0` |
| line_end 为 None | 使用文件总行数 |
| 索引文件不存在 | 抛出 `ReportNotFoundError` |
| 年报目录不存在 | 抛出 `ReportNotFoundError` |
| 标题无序号 | 不分配 section_id（值为空字符串 `""`） |
| 多结果得分相同 | 按 section_id 字典序排序 |

---

## 10. 依赖关系

### 10.1 内部依赖

```
annual_report_search
    │
    ├──► annual_report_reader
    │        ├── core.py          → build_index(), 索引读写
    │        ├── models.py        → SectionNode 数据结构
    │        └── utils.py        → REPORT_BASE_PATH, 路径拼接
    │
    └──► 标准库
             ├── pathlib          → 路径处理
             ├── json            → JSON 序列化
             └── re              → 正则匹配
```

### 10.2 外部依赖

```
jieba>=0.42.1   # 中文分词
```

---

## 11. 模块导出（__init__.py）

```python
# annual_report_search/__init__.py
from .search_engine import search_sections
from .section_id import read_section
from .constants import REPORT_BASE_PATH

__all__ = ["search_sections", "read_section", "REPORT_BASE_PATH"]
```

---

## 12. 关键设计决策

1. **section_id 分配规则**：只有包含序号（一、1、（1）等）的标题才分配 section_id，无序号标题作为层级节点存在但不分配 ID

2. **检索策略**：轻量级关键词匹配 + 同义词扩展，不使用向量数据库，保证简单高效

3. **索引缓存**：JSON 格式存储，支持 force_rebuild 强制重建

4. **路径设计**：复用 annual_report_reader 的 REPORT_BASE_PATH，统一使用 `{base}/md/{company}/...` 结构

5. **向后兼容**：annual_report_reader 模块增强后保持原有接口不变
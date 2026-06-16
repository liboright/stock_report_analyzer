# 年报章节检索工具 - 总体设计

## 1. 概述

### 1.1 目标
为 `deep_research_report` skill 开发一个工具，使其能够：
1. 通过自然语言检索年报子章节
2. 返回检索到的子章节内容
3. 供写作智能体通过目录检索子章节内容

### 1.2 交付形式
- **Python 模块**（不是 MCP Server），被 skill 直接 import 调用
- 位置：`D:\quant\report_database\deep-research-report\tools\annual_report_search\`

### 1.3 年报文件路径
- **年报文件**：`D:\quant\report_database/md/{company}/input/{company}{year}年年度报告/`
- **索引缓存**：`D:\quant\report_database/md/{company}/output/navi/{company}_{year}_index.json`

---

## 2. 三个核心函数

### 2.1 build_index(company, year, force_rebuild=False)
**功能**：为指定公司的年报构建章节索引

**输入**：
- `company`: 公司名称，如 "宁德时代"
- `year`: 报告年度，如 "2024"
- `force_rebuild`: 是否强制重建索引（跳过缓存），默认 False

**输出**：
```python
{
    "company": str,
    "year": str,
    "sections": [...],  # 章节树，每个节点包含 section_id
    "cache_file": str   # 缓存文件路径
}
```

**流程**：
1. 检查缓存是否存在，不存在则构建
2. 扫描年报目录下的所有 .md 文件
3. 解析每个文件的标题（# ## ###），构建三级嵌套章节树
4. **为每个节点生成唯一的 section_id 和 path**
5. 保存索引到 JSON 缓存文件

### 2.2 search_sections(query, company, year=None, top_k=5)
**功能**：基于自然语言检索匹配的年报章节

**输入**：
- `query`: 自然语言查询，如 "公司主要业务是什么"
- `company`: 公司名称
- `year`: 可选，限定报告年度
- `top_k`: 返回最多几条结果，默认 5

**输出**：
```python
[
    {
        "company": str,
        "year": str,
        "section_id": str,     # 如 "3.1.2"
        "title": str,
        "level": int,
        "match_score": float,  # 0-1 之间的匹配得分
        "file": str,
        "line_start": int,
        "line_end": int
    },
    ...
]
```

**检索策略**：轻量级关键词匹配 + 同义词扩展（无向量数据库）

### 2.3 read_section(company, year, section_id)
**功能**：根据公司名称、年份、章节ID提取该章节的完整内容

**输入**：
- `company`: 公司名称
- `year`: 报告年度
- `section_id**: 章节ID，如 "3.1.2"

**输出**：
```python
{
    "company": str,
    "year": str,
    "section_id": str,
    "title": str,
    "content": str,           # 章节正文内容（不含章节标题）
    "word_count": int,
    "file": str,
    "line_start": int,
    "line_end": int
}
```

---

## 3. 章节ID体系设计

### 3.1 ID格式
采用层级点号 notation：`"3.1.2"` 表示：第3节 > 第1个子章节 > 第2个子子章节

### 3.2 ID生成规则

| 层级 | 来源 | 示例 |
|------|------|------|
| 第1级 | 文件名 "_" 之前的数字（如 `03_第三节...` → `3`） | `3`, `10` |
| 第2级 | `##` 标题中的序号（**必须包含**如 `一、`、`1、`、`（1）`） | `3.1` |
| 第3级 | `###` 标题中的序号（**必须包含**如 `一、`、`1、`、`（1）`） | `3.1.2` |

**重要**：只有包含序号的 `##` / `###` 标题才算子章节。没有序号的标题（如 `公司业务回顾`）不是子章节，不分配 section_id。

### 3.3 path 字段
每个节点还有一个 `path` 字段，表示父节点 ID 列表，如 `["3", "3.1"]`

---

## 4. 数据结构

### 4.1 SectionNode（增强）
```python
@dataclass
class SectionNode:
    section_id: str                      # 新增：如 "3.1.2"
    path: list[str]                     # 新增：父节点ID列表，如 ["3", "3.1"]
    title: str                          # 章节标题
    level: int                          # 标题层级 (1/2/3)
    line_start: int                     # 起始行号（全局累积行号）
    line_end: Optional[int]             # 结束行号
    file: str                           # 所属文件名
    subsections: list["SectionNode"]     # 子章节列表
```

### 4.2 SectionMatch（新增）
```python
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

---

## 5. 文件组织

```
deep-research-report/tools/
├── annual_report_reader/           # 现有模块（增强，向后兼容）
│   ├── models.py                   # 修改：添加 section_id, path 字段
│   ├── core.py                     # 修改：build_index() 生成 section_id
│   ├── utils.py                    # 修改：REPORT_BASE_PATH + section_id 生成
│   └── exceptions.py               # 保持不变
│
└── annual_report_search/          # 新增 检索工具模块
    ├── __init__.py                 # 导出 search_sections, read_section
    ├── section_id.py              # 章节ID生成与解析
    ├── search_engine.py           # 检索引擎
    └── constants.py                # 同义词词表、停用词
```

---

## 6. 依赖

```python
jieba>=0.42.1  # 中文分词
```

---

## 7. 验证方案

1. **构建索引**：对宁德时代 2024 年报执行 `build_index()`，检查输出的 JSON 中每个章节是否有唯一 `section_id`
2. **章节检索**：执行 `search_sections("公司主要业务", "宁德时代")`，验证返回结果是否相关
3. **内容提取**：使用返回的 `section_id` 执行 `read_section()`，验证提取的内容是否正确
4. **端到端测试**：模拟写作智能体流程：搜索"营收情况" → 获取 `section_id` → 提取内容

---

## 8. 子文档

每个核心函数的详细设计文档：

1. `annual_report_search_build_index_design.md` - build_index 详细设计
2. `annual_report_search_search_sections_design.md` - search_sections 详细设计
3. `annual_report_search_read_section_design.md` - read_section 详细设计
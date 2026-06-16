# build_index 详细设计

## 1. 函数签名

```python
def build_index(company: str, year: str, force_rebuild: bool = False) -> dict:
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

## 2. 路径配置

| 配置项 | 值 |
|--------|-----|
| 年报基础路径 | `D:\quant\report_database` |
| 年报文件目录 | `{base}/md/{company}/input/{company}{year}年年度报告/` |
| 索引缓存路径 | `{base}/md/{company}/output/navi/` |
| 缓存文件名格式 | `{company}_{year}_index.json` |

**注意**：
- 完整索引缓存路径：`D:\quant\report_database\md\{company}\output\navi\{company}_{year}_index.json`
- 需要修改 `annual_report_reader/utils.py` 中的 `REPORT_BASE_PATH`

## 3. 流程图

```
build_index(company, year)
│
├─► 1. 扫描年报目录下的所有 .md 文件
│      ├─► 匹配模式: [0-9]+_*.md（如 "03_第三节..." 或 "10_第十节..."）
│      └─► 排除文件: "00_目录.md", "报告结构.md"
│
├─► 2. 按文件名数字排序（如 01, 02, 03 ... 10）
│
├─► 3. 第一遍：计算 file_offsets 和每个文件的行数
│      └─► 全局累积行号从 1 开始
│
├─► 4. 第二遍：解析每个文件构建章节树
│      ├─► 使用 parse_markdown_headings() 提取 # ## ### 标题
│      └─► 为每个标题节点分配 section_id:
│            ├─► 第一级: 从文件名 "_" 之前的数字提取（如 "03" → "3", "10" → "10"）
│            ├─► 第二级 ##: 从标题中提取中文/阿拉伯数字序号
│            │      规则: 只有包含序号（如 一、1、（1））才算子章节
│            │             没有序号的不是子章节，不分配 section_id
│            └─► 第三级 ###: 同上规则
│
├─► 5. 遍历章节树，为每个节点生成 path 字段
│
├─► 6. 保存索引缓存到 JSON
│      └─► 路径: {base}/md/{company}/output/navi/{company}_{year}_index.json
│
└─► 7. 返回索引结构

## 4. section_id 生成算法

### 4.1 第一级 ID（来自文件名）
```python
# 文件名: "03_第三节 管理层讨论与分析.md" 或 "10_第十节财务报告.md"
# 提取 "_" 之前的数字，可能是1位或2位
file_index = int(re.match(r'^(\d+)_', file_name).group(1))
# "03" → 3, "10" → 10
```

### 4.2 子章节识别规则（## 和 ### 标题）

**重要**：只有包含序号（如 `（一）`、`一、`、`1、`、`（1）`）的标题才算子章节，才分配 section_id。

**无序号的标题不是子章节**，不分配 section_id，但在构建树时仍作为层级节点存在。

```python
# 标题匹配序号格式
NUMBER_PATTERNS = [
    r'^（[一二三四五六七八九十]+）',  # （一）、（二）...
    r'^（[0-9]+）',                  # （1）、（2）...
    r'^[一二三四五六七八九十]+、',   # 一、二、三...
    r'^[0-9]+、',                    # 1、2、3...
    r'^[0-9]+\.',                    # 1. 2. 3.
]

def has_section_number(title: str) -> bool:
    """检查标题是否包含序号"""
    for pattern in NUMBER_PATTERNS:
        if re.match(pattern, title):
            return True
    return False
```

### 4.3 第二级 ID（## 标题）

```python
# 如果标题包含序号，提取序号
if has_section_number(title):
    section_number = extract_section_number(title)  # 一 → 1, （1） → 1
    section_id = f"{file_index}.{section_number}"
else:
    # 无序号，不是子章节，不分配 section_id
    section_id = None
```

### 4.4 第三级 ID（### 标题）

同上，使用相同的序号提取逻辑。

### 4.5 完整算法

```python
def generate_section_id(file_idx: int, h2_idx: int, h3_idx: int) -> str | None:
    """
    生成章节ID

    参数:
        file_idx: 文件序号（第一级）
        h2_idx: ## 标题序号（第二级），0 或 None 表示没有
        h3_idx: ### 标题序号（第三级），0 或 None 表示没有

    返回:
        str 或 None: 如 "3.1.2"，无序号则返回 None

    示例:
        generate_section_id(3, 1, 2) → "3.1.2"
        generate_section_id(3, 1, 0) → "3.1"
        generate_section_id(3, 0, 0) → "3"
    """
    if h2_idx == 0 or h2_idx is None:
        return str(file_idx)  # 只有文件级

    parts = [str(file_idx), str(h2_idx)]
    if h3_idx and h3_idx > 0:
        parts.append(str(h3_idx))

    return ".".join(parts)
```

## 5. path 生成算法

```python
def generate_path(parent_path: list[str], section_id: str) -> list[str]:
    """
    生成 path 字段

    参数:
        parent_path: 父节点的 path
        section_id: 当前节点的 section_id

    示例:
        parent_path=["3", "3.1"], section_id="3.1.2" → ["3", "3.1"]
    """
    return parent_path + [section_id] if parent_path else [section_id]
```

**注意**：`path` 不包含自身，只包含父节点 ID 列表

## 6. 索引 JSON 结构

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
      "subsections": [
        {
          "section_id": "3.1",
          "path": ["3"],
          "title": "一、报告期内公司所处行业情况",
          "level": 2,
          "line_start": 3,
          "line_end": 200,
          "file": "3_第三节 管理层讨论与分析.md",
          "subsections": [
            {
              "section_id": "3.1.2",
              "path": ["3", "3.1"],
              "title": "2、行业发展状况及发展趋势",
              "level": 3,
              "line_start": 9,
              "line_end": 50,
              "file": "3_第三节 管理层讨论与分析.md",
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

## 7. 复用现有代码

| 函数 | 来源 | 用途 |
|------|------|------|
| `parse_markdown_headings()` | `annual_report_reader.utils` | 解析 Markdown 标题 |
| `build_section_tree()` | `annual_report_reader.utils` | 构建嵌套章节树 |
| `REPORT_BASE_PATH` | `annual_report_reader.utils` | 需修改为新路径 |
| `_get_sorted_chapter_files()` | `annual_report_reader.core` | 获取排序后的章节文件 |
| `IndexCache` | `annual_report_reader.models` | 需增强 section_id 和 path |

## 8. 修改清单

### 8.1 annual_report_reader/utils.py
```python
# 修改 REPORT_BASE_PATH
REPORT_BASE_PATH: "Path" = Path(r"D:\quant\report_database\md")

# 新增函数
def generate_section_id(file_idx: int, h2_idx: int, h3_idx: int) -> str: ...
def extract_number_from_title(title: str) -> int | None: ...
```

### 8.2 annual_report_reader/models.py
```python
@dataclass
class SectionNode:
    section_id: str = ""               # 新增
    path: list[str] = field(default_factory=list)  # 新增
    # ... 其他字段保持不变
```

### 8.3 annual_report_reader/core.py
```python
# 在 build_index() 函数中
# 构建章节树后，遍历树为每个节点生成 section_id 和 path
def _assign_section_ids(sections: list[SectionNode], parent_path: list[str] = None) -> None:
    for section in sections:
        # 计算 section_id
        section.section_id = calculate_section_id(section)
        # 计算 path
        if parent_path:
            section.path = parent_path + [section.section_id]
        else:
            section.path = [section.section_id] if section.section_id else []
        # 递归处理子节点
        if section.subsections:
            _assign_section_ids(section.subsections, section.path)
```

## 9. 验证用例

| 用例 | 输入 | 预期输出 |
|------|------|----------|
| 基本索引构建 | company="宁德时代", year="2024" | 返回包含所有章节的索引，section_id 唯一 |
| 强制重建 | force_rebuild=True | 重新解析文件，更新缓存 |
| 无效公司 | company="不存在的公司" | 抛出 ReportNotFoundError |
| 一级章节 | 文件名 "03_第三节..." | section_id = "3" |
| 二级章节有序号 | ## 标题 "一、" | section_id = "X.1" |
| 二级章节无序号 | ## 标题 "重大事项说明" | section_id = None（不是子章节） |
| 三级章节有序号 | ### 标题 "1、" | section_id = "X.Y.1" |
| 三级章节无序号 | ### 标题 "其他事项" | section_id = None（不是子章节） |
| 文件名2位数字 | 文件名 "10_第十节..." | section_id = "10" |
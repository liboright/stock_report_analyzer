# read_section 详细设计

## 1. 函数签名

```python
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
            "content": str,       # 章节正文内容（不含章节标题）
            "word_count": int,   # 正文字数
            "file": str,
            "line_start": int,
            "line_end": int
        }

    异常:
        ReportNotFoundError: 索引文件不存在
        ChapterNotFoundError: 章节ID不存在
    """
```

## 2. 路径配置

| 配置项 | 值 |
|--------|-----|
| 索引缓存路径 | `{base}/md/{company}/output/navi/{company}_{year}_index.json` |
| 年报文件目录 | `{base}/md/{company}/input/{company}{year}年年度报告/` |

## 3. 流程图

```
read_section(company, year, section_id)
│
├─► 1. 加载索引缓存
│      └─► 路径: {base}/md/{company}/output/navi/{company}_{year}_index.json
│
├─► 2. 解析 section_id
│      └─► "3.1.2" → 层级路径 ["3", "3.1", "3.1.2"]
│
├─► 3. 在章节树中定位目标节点
│      ├─► 第一级: 在 sections 中找 section_id == "3" 的节点
│      ├─► 第二级: 在上一级的 subsections 中找 section_id == "3.1" 的节点
│      └─► 第三级: 在上一级的 subsections 中找 section_id == "3.1.2" 的节点
│
├─► 4. 读取文件
│      └─► file_path = {base}/md/{company}/input/{company}{year}年年度报告/{node.file}
│
├─► 5. 提取内容
│      ├─► 计算文件内局部行号: local_line = global_line - file_offset + 1
│      ├─► 提取 lines[local_line_start - 1 : local_line_end]
│      └─► 拼接返回（不含标题行）
│
└─► 6. 返回结果
```

## 4. section_id 解析

```python
def parse_section_id(section_id: str) -> list[str]:
    """
    解析 section_id 为层级路径

    参数:
        section_id: 如 "3.1.2"

    返回:
        list[str]: ["3", "3.1", "3.1.2"]
    """
    return section_id.split(".")
```

## 5. 节点定位算法

```python
def find_section_node(sections: list[dict], section_id: str) -> dict | None:
    """
    在章节树中查找指定 section_id 的节点

    参数:
        sections: 章节树根节点列表
        section_id: 要查找的章节ID，如 "3.1.2"

    返回:
        dict | None: 找到的节点，不存在则返回 None
    """
    # 解析 section_id 为路径
    parts = parse_section_id(section_id)
    if not parts:
        return None

    current_nodes = sections
    for i, part in enumerate(parts):
        # 在当前层级查找匹配的节点
        found = None
        for node in current_nodes:
            if node.get("section_id") == part:
                found = node
                break

        if found is None:
            return None

        # 如果是最后一个部分，返回该节点
        if i == len(parts) - 1:
            return found

        # 否则继续查找子节点
        current_nodes = found.get("subsections", [])

    return None
```

## 6. 内容提取

```python
def extract_section_content(
    report_dir: Path,
    node: dict
) -> str:
    """
    提取章节内容

    参数:
        report_dir: 年报文件目录
        node: 章节节点，包含 file, line_start, line_end

    返回:
        str: 章节正文内容（不含标题行）
    """
    file_path = report_dir / node["file"]

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n')

    # 从索引获取 file_offset
    # 注：需要从索引缓存中获取 file_offsets
    file_offset = index_cache["file_offsets"].get(node["file"], 1)

    # 计算文件内局部行号（1-based）
    local_line_start = node["line_start"] - file_offset + 1
    local_line_end = node["line_end"] - file_offset + 1 if node.get("line_end") else len(lines)

    # 提取内容（跳过标题行，即第一行）
    section_lines = lines[local_line_start:local_line_end]

    return '\n'.join(section_lines).strip()
```

## 7. 返回结果示例

```json
{
  "company": "宁德时代",
  "year": "2024",
  "section_id": "3.2.1",
  "title": "1、主要业务",
  "content": "报告期内，公司主要从事动力电池系统、储能系统以及锂电池材料的研发、...\n公司在全球动力电池领域的市场份额持续提升，...",
  "word_count": 1256,
  "file": "3_第三节 管理层讨论与分析.md",
  "line_start": 37,
  "line_end": 120
}
```

## 8. 复用现有代码

| 函数 | 来源 | 用途 |
|------|------|------|
| 索引读取 | `annual_report_reader.core` | 读取并解析 JSON 缓存 |
| 文件读取 | `annual_report_reader.core.read_section()` | 参考文件读取逻辑 |

## 9. 验证用例

| 用例 | 输入 | 预期输出 |
|------|------|----------|
| 基本提取 | section_id="3" | 返回第三节全部内容 |
| 子章节提取 | section_id="3.1" | 返回"一、报告期内公司所处行业情况"的内容 |
| 深层次提取 | section_id="3.1.2" | 返回"2、行业发展状况"的内容 |
| 无效section_id | section_id="99.99" | 抛出 ChapterNotFoundError |
| 跨年提取 | year="2023", section_id="3.2" | 返回2023年报的对应章节 |

## 10. 边界处理

1. **section_id 为空或格式错误** → 抛出 ChapterNotFoundError
2. **章节内容为空** → 返回空字符串，word_count = 0
3. **文件不存在** → 抛出 ReportNotFoundError
4. **line_end 为 None** → 使用文件总行数作为结束行
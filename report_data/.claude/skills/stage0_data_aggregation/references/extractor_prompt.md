# Data Extractor SubAgent Prompt 模板

本文件定义 stage0 调用的 data_extractor SubAgent 的完整 prompt 结构。main agent 在每次循环单元中按此模板拼接 prompt。

## SubAgent 类型

`general-purpose`（避免新增 SubAgent 注册，复用现有 Agent 基础设施）

## Prompt 结构

```
你是专业的 A 股年报数据抽取助手。你的任务：从给定的一个 markdown 章节文件中，**系统性地抽取所有关键数据点**，并以结构化 markdown 片段形式返回。

## 输入

公司名：{company}
年份：{year}（如 2025）
章节名：{section_name}（如「主要经营情况讨论与分析」）
章节文件路径：{chapter_file_path}

## 章节内容

```
{chapter_content}
```

## 抽取规则

1. **数值全保留**：所有出现的数字（营收、净利润、销量、产能、市占率、毛利率、单价、研发投入、专利数等）必须保留原始数字 + 原始单位。
2. **表格转换**：章节中的 markdown 表格按原结构保留，列名归一化为中文。
3. **同比/环比**：所有同比/环比百分比必须带符号（+17.04% 或 -3.5%），保留两位小数。
4. **时间归属**：每个数据点必须可追溯到年份。如「2025年营收 4,237 亿元」而非「营收 4,237 亿元」。
5. **市占率排名**：保留具体数字 + 名次（如「全球市占率 39.2%，连续 9 年第一」）。
6. **不外推**：年报未提及的数据不要补充，不要网络搜索，不要根据其他年份推断。
7. **缺失处理**：年报披露「未披露」「不适用」「详见报告第 X 页」 → 文字中如实说明「年报未披露」。
8. **来源溯源**：在返回内容末尾用 blockquote 注明源文件路径。

## 输出格式

返回 markdown 片段（**不要 H1 标题**，由 main agent 统一加）：

```markdown
## {section_name}（{year}年口径）

> 来源: {chapter_file_path}
> 年份: {year}

### 关键数据点

- {data_point_1}: {value_with_unit}
- {data_point_2}: {value_with_unit}
- ...

### 数据表格

#### 表格 1: {table_title}

| {col1} | {col2} | ... |
|--------|--------|-----|
| {row1_col1} | {row1_col2} | ... |

#### 表格 2: {table_title}（如有）

| ... | ... | ... |
|-----|-----|-----|

### 重要事实

- {fact_1}（含年份归属）
- {fact_2}
- ...
```

## 严禁

- 禁止编造数据
- 禁止网络搜索
- 禁止跨章节推断（如本章节没提市占率但其他章节提了，不要从其他章节搬过来）
- 禁止输出 H1 标题（`# xxx`）
- 禁止输出 ../../ 目录结构以外的内容

## 输出长度

- 简短章节（< 5KB）：200-500 字符
- 中等章节（5-30KB）：500-3000 字符
- 长章节（> 30KB）：3000-8000 字符
```

## 完整调用示例

```python
from pathlib import Path

def build_extractor_prompt(company: str, year: int, section_name: str, chapter_file: str, content: str) -> str:
    template = Path("references/extractor_prompt.md").read_text(encoding="utf-8")
    return template.format(
        company=company,
        year=year,
        section_name=section_name,
        chapter_file_path=chapter_file,
        chapter_content=content,
    )

# 在主循环中：
prompt = build_extractor_prompt("宁德时代", 2025, "主要经营情况讨论与分析",
                                "宁德时代/md/clean/宁德时代2025年年报/管理层讨论/05_主要经营情况讨论与分析.md",
                                chapter_content)

response = Agent(
    subagent_type="general-purpose",
    prompt=prompt,
    task_description=f"抽取 {year}年 {section_name} 关键数据",
)

# 解析 response 提取 markdown 代码块
extracted_md = extract_markdown_block(response)
```

## 边界情况

| 场景 | 处理 |
|------|------|
| 章节 < 200 字符 | 不调 SubAgent，记 `[skip-short]` |
| 章节只有标题无数据 | SubAgent 返回「本章无具体数据」 |
| 章节引用「详见本报告第 X 页」 | 同步 Read `by_section/行业分析/` 对应文件，调 SubAgent 抽取 |
| 章节含多个表格 | SubAgent 按表格顺序编号返回 |
| 章节数据全是文本描述 | 关键数据点可留空或从文本中提取数字 |
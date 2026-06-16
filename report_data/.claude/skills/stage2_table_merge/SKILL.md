---
name: stage2_table_merge
description: 对单一跨年表分组做语义对齐 → 生成 long+wide CSV。当 service 阶段 3.x 评估为 weak 时由后端 worker 自动触发；不要手工调用。
---

# Stage 2: 跨年表格合并（弱组语义对齐）

## 目的

后端 `table_merge_service` 已对同公司 N 年的同名表做程序化合并，但**当列名/科目名漂移严重**时（column Jaccard < 0.8 或 row Jaccard < 0.5）会判定为 **weak 组**，落入本 skill 做 LLM 语义对齐。

输入是阶段 2.5 抽取出的 N 张 CSV（每张 1 年），输出是**一张 long 表 + 一张 wide 表**，落到 `md/research_file/table/`，文件名 `{stem}_{title}_long.csv` / `_wide.csv`。

## 输入

CLI 位置参数（whitespace 分隔，5 段；csv_paths 内部用 `|` 分隔）：

```
/stage2_table_merge <company> <group_key> <years_csv> <path1>|<path2>|... <empty>
```

| 段 | 示例 | 含义 |
|---|---|---|
| company | `贵州茅台` | 公司名 |
| group_key | `05_五\|营业收入` | 分组键，格式 `{源md_stem}\|{sanitized_title}` |
| years_csv | `2023,2024,2025` | 该组涉及的年份（逗号分隔） |
| csv_paths | `公司/.../2023/营业收入.csv\|公司/.../2024/营业收入.csv\|...` | N 个 CSV 的 POSIX 相对路径（pipe 分隔） |

**注意**：不要手敲这条命令。`POST /companies/{name}/tables/merge` 端点会按弱组列表逐组调本 skill。

## 输出

| 输出 | 路径 | 描述 |
|------|------|------|
| long CSV | `md/research_file/table/{stem}_{title}_long.csv` | 长表（year/科目/metric/value/unit） |
| wide CSV | `md/research_file/table/{stem}_{title}_wide.csv` | 宽表（subject × metric_year） |

后端 `claude_skill_runner._expected_table_merge_outputs()` 会校验两个文件都存在，否则算 skill 失败。

## 数据来源

每张输入 CSV 的格式（阶段 2.5 产物）：
```
# source_md, 05_五、报告期内主要经营情况.md
# table_seq, 1/2
# report_year, 2025
# title, 主营业务分行业情况
# unit, 千元
# year_mapping, current=2025,previous=2024,ybp=2023
# ============== END HEADER ==============

项目,2025年金额,2024年金额
营业收入,1000000,950000
...
```

读 `unit` + `year_mapping` 拿到单位与年份映射。读 headers（首列是科目，其余是指标列）。读 data_grid（行数据）。

## 工具

### llm_logger（同 stage1）

```python
from llm_logger import get_logger
logger = get_logger("贵州茅台")
```

每次调 SubAgent 前 `logger.log_start(...)`，调完 `logger.log_end(call_id, ...)`。

### general-purpose SubAgent

本 skill 用 `general-purpose`（不用 `report_writer`——那是写散文用的，本任务是对齐表格数据）。

调用：
```
Agent(
  subagent_type="general-purpose",
  prompt=generated_prompt,
  task_description="语义对齐 group_key=X"
)
```

---

## 工作流程

### 第一阶段：解析参数

1. 读 CLI prompt 字符串，whitespace 切分 → `[skill, company, group_key, years_csv, csvs_str]`
2. 解析 `years_csv` → List[int]（按 `,` split + int）
3. 解析 `csvs_str` → List[str]（按 `|` split）
4. 解析 `group_key`：`{stem}|{title}`（按第一个 `|` split）

### 第二阶段：读 N 个 CSV

对每个 `csv_path`，用 `Read` 工具读文件，**完整读**（不要分页截断，年报单张表 200 行内）：
- 抽 `# ============== END HEADER ==============` 之前的元数据
- 抽 headers（header 行）
- 抽 data rows

把 N 个 `{year, unit, year_mapping, headers, rows}` 装进 SubAgent 的 prompt 里。

### 第三阶段：调 SubAgent 做语义对齐

**Prompt 模板**（传给 general-purpose）：

```
你是表格语义对齐专家。给定 {N} 张同一概念但列名/科目名漂移的跨年 CSV，请按以下规则合并。

# 公司
{company}

# 分组
group_key = {group_key}
源 md stem = {stem}
清理后标题 = {title}

# 输入（按年份升序）
{year_1}: headers=[...], rows=[...]
{year_2}: headers=[...], rows=[...]
...

# 任务
1. 读懂每张表的列含义（如"金额"、"占比"、"毛利率比上年增减"等）
2. 判断每张表是「单指标多年」还是「多指标同年」
3. 生成两张产出表：

## long 表（CSV）
列顺序严格用：_row_type, year, source_md_stem, subject, metric, value, unit
- `_row_type` ∈ {"data", "section_header"}
- 第 1 列非空且其余列全空 → `_row_type=section_header`，metric/value 留空
- 否则 `_row_type=data`，subject=首列，metric=列名（去空白），value=cell 原样（保留中文同比/"不适用"）
- unit 来自该年份 CSV 元数据头；year 是该行所属年份
- 全部 record 写出
- 单位保持原始（不要把千元归一为元）

## wide 表（CSV）
- 首列 `subject`；其余列命名 `{metric}_{year}`（如 `金额_2024`）
- 分节标题行保留为独立一行，subject 填标题，metric 列全空
- value 原样保留（str）

# 写盘
- long 写到 {公司}/md/research_file/table/{safe_stem}_{safe_title}_long.csv
- wide 写到 {公司}/md/research_file/table/{safe_stem}_{safe_title}_wide.csv
- 用 Write 工具，UTF-8 BOM（utf-8-sig），首行是 CSV 头

# 命名
- safe_stem = 把 stem 的非法字符 (\\ / : * ? " < > |) 替换为 _
- safe_title = 同上
- 文件名格式：`{safe_stem}_{safe_title}_long.csv` / `_wide.csv`

# 关键规则
1. 不要归一单位（千元/元 各自保留）
2. 不要做 fuzzy match（不需要，语义上能看懂的科目就是同一行；看不懂的留空 + 在文件顶部 `# reason` 行加注释）
3. 中文同比值（如"减少 0.1 个百分点"）保持 str
4. 数字保留千分位逗号（如 "1,000" 不变）
5. 必须写出所有 N 年的所有数据行
6. 长表按 year desc, subject, metric 排序后输出
7. 完成后输出"完成"两字
```

**SubAgent 调用前**：`logger.log_start(agent_name="table_merge_aligner", task_description=f"语义对齐 {company} {group_key}", metadata={"company": company, "group_key": group_key, "years": years})`

**SubAgent 调用后**：`logger.log_end(call_id, result_summary=f"已生成 {len(years)} 年 long+wide")`

### 第四阶段：验证产物

1. 确认 `_long.csv` 和 `_wide.csv` 两个文件都存在
2. 检查 long CSV 行数 ≥ N × M（M 是最小行数）
3. 检查 wide CSV 第一列 = `subject`，其余列名都以 `_年` 结尾

如果任一不满足：**用 Edit 工具修复**（不要重跑 SubAgent，太贵）。

## 关键规则

1. **不要手工跑**：本 skill 由后端 worker 触发；CLI 直跑 = 错
2. **只处理一组**：一次调用只处理一个 group_key（后端逐组串行调）
3. **不归一单位**：service 已经覆盖，LLM 不要做单位换算
4. **不模糊匹配科目名**：宁可少合一行也不要错合
5. **长表 7 列固定**：列顺序不可改（下游 SQL/分析依赖）
6. **BOM 必加**：CSV 首字节 `EF BB BF`（Excel 友好）

## 日志

同 stage1：每次 SubAgent 调用必须 log_start + log_end，输出到 `md/{公司}/output/log/llm_log_{timestamp}.txt`。

---

## 示例

**输入**（来自后端 worker 调用）：
```
/stage2_table_merge 贵州茅台 05_五|主营业务分行业情况 2023,2024,2025 公司/.../2023/.../主营业务分行业情况.csv|公司/.../2024/.../主营业务分行业情况.csv|公司/.../2025/.../主营业务分行业情况.csv
```

**2023 CSV headers**：`项目,营业收入,营业成本,毛利率,毛利率比上年增减`
**2024 CSV headers**：`项目,营业收入,营业成本,毛利率,YoY`
**2025 CSV headers**：`项目,营业收入,营业成本,毛利率,毛利率比上年增减`

**关键点**：2024 列名 "YoY" 是漂移 → 语义对齐应识别为"毛利率比上年增减"。

**long 表 1 行示例**：
```
data,2024,05_五,茅台酒,毛利率比上年增减,减少 0.1 个百分点,千元
```

**wide 表 1 行示例**：
```
subject,营业收入_2023,营业收入_2024,营业收入_2025,毛利率_2023,...,毛利率比上年增减_2023,毛利率比上年增减_2024,毛利率比上年增减_2025
茅台酒,1000000,1100000,1200000,0.92,...,减少 0.2 个百分点,减少 0.1 个百分点,减少 0.3 个百分点
```

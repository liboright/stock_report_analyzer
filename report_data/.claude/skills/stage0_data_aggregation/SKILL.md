---
name: stage0_data_aggregation
description: |
  读取 A 股上市公司年度报告「第三节 管理层讨论与分析」全部章节文件，跨年份（默认 2023-2025）抽取关键数据点、表格和事实，生成一份聚合参考文档 `{公司}_三年综合数据.md`。

  触发场景（任意一条即触发）：
  - 用户说「聚合年报数据」「生成参考资料」「预抽取数据」「准备数据」
  - 用户输入 `/stage0_data_aggregation {公司名}`
  - 用户输入 `@stage0_data_aggregation {公司名}`
  - stage1_business_understanding 报告"参考资料缺失"（应自动调用本 skill 生成）

  不适用于：港股/美股年报、招股书、半年报、季报、研报、新闻稿。
  本 skill 只做数据抽取与聚合，**不写业务概况/行业分析**——后者由 stage1_business_understanding 完成。
---

# Stage 0: 数据聚合 (Data Aggregation)

## 目的

把 A 股上市公司 2023-2025 三年年报「第三节 管理层讨论与分析」的所有章节数据**预先抽取、跨年聚合**，生成单一参考文档，供下游 stage1_business_understanding 消费。

**核心价值**：
- **解耦**：把"读年报"和"写文档"分开。stage0 只负责数据，stage1 只负责叙事。
- **可追溯**：所有抽取的数据点都标注源文件路径 + 年份，可一键回溯到原文。
- **可复用**：参考资料可被多次消费（如 stage1 跑多版文档）。

## 输入

| 参数 | 类型 | 来源 | 描述 |
|------|------|------|------|
| `company` | string | user | 公司名（如「宁德时代」） |

## 输出

| 输出 | 路径 |
|------|------|
| 三年综合数据 | `{公司}/md/research_file/参考资料/{公司}_三年综合数据.md` |

## 数据源

- 主源：`{公司}/md/clean/{公司}{年}年年报/管理层讨论/*.md`
- 补充源：`{公司}/md/clean/{公司}{年}年年报/by_section/行业分析/*.md`
- 模板：`assets/templates/aggregate_data.md`

## 工具

### llm_logger

5 个核心 API：`log_agent_call` / `log_subagent_call` / `log_step` / `log_session_summary` / `log_start+log_end`。完整 API 与调用示例见 [references/llm_logger_api.md](references/llm_logger_api.md)。

### Data Extractor SubAgent

类型：`general-purpose`。每次读一个章节，调一次 SubAgent 抽取关键数据。

**Prompt 模板**：[references/extractor_prompt.md](references/extractor_prompt.md) 定义完整 prompt 结构。

**调用方式**：
```
Agent(subagent_type="general-purpose", prompt=extractor_prompt, task_description="...")
```

**产物契约**：SubAgent 返回 markdown 片段（不含 H1），由 main agent 收集后聚合落盘。

## 工作流程

### 第一阶段：扫描年报结构

1. 扫描 `{公司}/md/clean/{公司}{年}年年报/管理层讨论/` 下的年份文件夹
2. 确定可用年份列表（默认 2023/2024/2025，由近及远）
3. 对每个年份扫描该目录下的所有 `.md` 章节文件（按文件名前缀编号排序）
4. 同步识别 `by_section/行业分析/` 下的补充材料
5. 维护「年份 → 章节文件列表」映射

**路径规则**：cwd = `REPORT_DATA_PATH`；所有数据在 `{公司}/` 子目录下。详见 [references/path_conventions.md](references/path_conventions.md)。

### 第二阶段：迭代数据抽取（按 年份 × 章节）

```
for 年份 in [最近年份, ..., 最早年份]:
    for chapter_file in sorted(glob(f"{公司}/md/clean/{公司}{年}年年报/管理层讨论/*.md")):
        content = Read(chapter_file)
        if len(content.strip()) < 200:
            log(f"[skip-short] {chapter_file.name}")
            continue
        # 调 data_extractor SubAgent 抽取
        extracted_md = data_extractor(chapter=chapter_file, year=年份, section_name=章节名)
```

每个循环单元的步骤：

1. **Read 章节** + `log_step(step_name="Read - 章节", data="字符数: N")`
2. **长度判定**：< 200 字符 → skip-short；否则继续
3. **构造 prompt**：以 [references/extractor_prompt.md](references/extractor_prompt.md) 为骨架，注入章节内容 + 年份 + 公司名
4. **调 `general-purpose` SubAgent** + `log_agent_call(...)` 记录完整 prompt + response
5. **SubAgent 返回 markdown 片段**，main agent 暂存到内存映射 `chapter_name -> {年份: extracted_md}`

### 第三阶段：按章节名聚合 + 落盘

1. 将同一章节名（去除文件名前缀编号，如 `05_主要经营情况讨论与分析` → `主要经营情况讨论与分析`）的多份抽取结果合并
2. 按章节名排序生成动态 TOC
3. 渲染模板 `assets/templates/aggregate_data.md`：
   - 章节标题 = 序号 + 去除前缀的章节名
   - 每章节内：抽取年份列表（按远及近）
   - 每章节内：跨年水平表格（多张表格按主题分小节）
4. **落盘**：
   - 路径：`{公司}/md/research_file/参考资料/{公司}_三年综合数据.md`
   - 用 `/d/...` 前缀避免 sandbox 拦截
   - 单次 `Write` 调用直接写入最终文件（无中间临时文件）
5. `log_step` 记录落盘结果（字节数、章节数、表格数）

### 第四阶段：Session 汇总

`log_session_summary` 记录：
- 完成公司名
- 产物路径 + 字节数
- SubAgent 调用总数
- skip-short 章节数
- 数据缺失项（如有）

## 模板结构

完整模板见 [assets/templates/aggregate_data.md](assets/templates/aggregate_data.md)：

```
# {公司} 三年综合数据

> 数据来源、范围、生成时间、生成方式

## 目录
（动态生成）

---

## 一、{章节1名}
（来自年报第 1 个章节）

### 关键数据点
### 数据表格
### 重要事实

## 二、{章节2名}
...

```

**章节数**：动态（按年报实际章节数），不限 18 节。

## 关键规则

1. **由近及远处理，按远及近聚合**：
   - 处理顺序：年份从最近（2025）到最早（2023）
   - 聚合输出表格：年份从最早（2023）到最近（2025），便于对比
2. **不外推**：年报未披露 → 单元格写「未披露」，叙述中说明
3. **只用年报数据**：不调用网络搜索补充
4. **不写业务概况/行业分析**：本 skill 只做数据聚合，文档生成由 stage1 完成
5. **动态章节名**：section 名来自年报实际章节名，不固定

## 错误处理

详见 [references/error_handling.md](references/error_handling.md)。

## 示例

完整跑通示例见 [examples/run_ningdeshishi_2025.md](examples/run_ningdeshishi_2025.md)。

## 版本

- v1（2026-06-23）：初始版本，独立 skill，动态章节名，强制 stage1 依赖
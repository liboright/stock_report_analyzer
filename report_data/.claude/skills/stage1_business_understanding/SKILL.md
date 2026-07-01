---
name: stage1_business_understanding
description: |
  读取 A 股上市公司年度报告「第三节 管理层讨论与分析」章节，生成两份研究文档：
  1. {公司}_业务概况.md（9 节叙述式，含分业务/分产品/分地区收入毛利率数据表）
  2. {公司}_行业分析.md（7 节行业认知）

  触发场景（任意一条即触发）：
  - 用户说「分析公司业务」「了解行业」「业务概况」「行业分析」「公司画像」
  - 用户输入 `/stage1_business_understanding {公司名}`
  - 用户输入 `@stage1_business_understanding {公司名}`

  不适用于：港股/美股年报、招股书、半年报、季报、研报、新闻稿。
  一年数据或单一章节场景请走其他 skill，本 skill 默认处理 2023-2025 三年整合。
---

# Stage 1: 业务了解 (Business Understanding)

## 目的

阅读 A 股上市公司年报「第三节 管理层讨论与分析」主要业务和行业情况章节，结合财务数据，形成公司业务概况和行业分析两个 markdown 文档。

## 输入

| 参数 | 类型 | 来源 |
|------|------|------|
| `company` | string | 用户指定（如「宁德时代」） |

## 输出

| 输出 | 路径 |
|------|------|
| 业务概况 | `md/research_file/{公司}_业务概况.md` |
| 行业分析 | `md/research_file/{公司}_行业分析.md` |

路径规范见 [references/path_conventions.md](references/path_conventions.md)。

## 数据源

- 必备前置：`{公司}/md/clean/{公司}{年}年年报/管理层讨论/*.md`（按文件名前缀编号排序）
- 补充：`{公司}/md/clean/{公司}{年}年年报/by_section/行业分析/*.md`
- **强制依赖**：`{公司}/md/research_file/参考资料/{公司}_三年综合数据.md`（由 `stage0_data_aggregation` 生成，缺失则硬失败）
- 模板：`assets/templates/business_profile.md` + `assets/templates/industry_analysis.md`

## 强制依赖

> ⚠️ **本 skill 必须在 stage0 产物存在时才能运行**。

```python
from pathlib import Path

ref_path = f"{公司}/md/research_file/参考资料/{公司}_三年综合数据.md"
if not Path(ref_path).exists():
    raise RuntimeError(
        f"参考资料缺失：{ref_path}\n"
        f"stage1 依赖 stage0_data_aggregation 的产物。\n"
        f"请先运行：@stage0_data_aggregation {公司}\n"
        f"或调用：/stage0_data_aggregation {公司}"
    )
```

依赖文件路径见 [references/path_conventions.md](references/path_conventions.md)。

## 工具

### report_writer SubAgent

```python
Agent(subagent_type="report_writer", prompt=generated_prompt, task_description=...)
```

两种模式：完整写作（首个章节）| 增量补充（后续章节）。产物契约与落盘机制见 [references/subagent_contract.md](references/subagent_contract.md)。

### llm_logger

5 个核心 API：`log_agent_call` / `log_subagent_call` / `log_step` / `log_session_summary` / `log_start+log_end`。完整 API 与调用示例见 [references/llm_logger_api.md](references/llm_logger_api.md)。

## 工作流程

### 第一阶段：明确信息范围

**第 0 步（强制）：依赖检查**

读取 stage0 产物 + 验证年报目录：

```python
from pathlib import Path

# 1. 验证 stage0 产物存在（强制依赖）
ref_path = f"{公司}/md/research_file/参考资料/{公司}_三年综合数据.md"
if not Path(ref_path).exists():
    raise RuntimeError(
        f"参考资料缺失：{ref_path}\n"
        f"请先运行：@stage0_data_aggregation {公司}"
    )

# 2. 读取 stage0 聚合数据（作为下游核心输入）
ref_content = Read(ref_path)
log_step(step_name="Read - stage0 产物", data=f"字符数: {len(ref_content)}")
```

**后续步骤**：

1. 扫描 `{公司}/md/clean/{公司}{年}年年报/管理层讨论/` 下的年份文件夹
2. 确定可用年份列表（如 2025/2024/2023）
3. 从最近年份开始，往前逐年处理

### 第二阶段：迭代写作双文档

**A' 方案**：对 `管理层讨论/` 下的**所有 `.md` 文件逐个 Read**；`< 200` 字符的「声明/不适用/纯标题」章节跳过 `report_writer` 调用，但仍记入处理日志。

**迭代结构**：

```
for 年份 in [最近年份, ..., 最早年份]:
    for chapter_file in sorted(glob(...)):
        content = Read(chapter_file)
        if len(content.strip()) < 200:
            log(f"[skip-short] {chapter_file.name}")
            continue
        report_writer(doc=业务概况, chapter=chapter_file, year=年份)
        report_writer(doc=行业分析, chapter=chapter_file, year=年份)
```

**每个循环单元的步骤**：

1. Read 章节 + `log_step(step_name="Read - 章节", data="字符数: N")`
2. 长度判定：< 200 → skip-short；否则继续
3. `log_start` 或 `log_agent_call` 记录业务概况调用开始
4. 调 `report_writer`（业务概况）
5. `log_end` 记录业务概况完成
6. 同上 3-5 步处理行业分析

**章节处理规则**：

| 条件 | 动作 |
|---|---|
| Read 成功 + `len(content) >= 200` | 调 `report_writer` 写两份文档 |
| Read 成功 + `len(content) < 200` | 跳过，记 `[skip-short]` |
| Read 失败 | 跳过，记 warning |
| 章节含「详见第 X 页」引用 | 同步 Read `by_section/行业分析/` 对应章节 |

### 第三阶段：验证并补充财务数据

读取 `{公司名}_业务概况.md`，按检查清单核对 12 项必备数据：

- 营业收入 / 净利润 / 扣非净利润
- 总资产 / 净资产 / ROE / 毛利率
- 主要客户集中度
- 分业务收入与毛利率（v2.1）
- 分产品收入与毛利率（v2.1）
- 分地区收入与毛利率（v2.1）
- 分业务经营数据·产销量/产能（v2.1）

缺失时调 `report_writer` 回填，仍缺失则在 result 里标注「数据缺失项」。

### 第四阶段：落盘

runner 负责落盘（详见 [references/subagent_contract.md](references/subagent_contract.md) §落盘机制）。

## 模板结构

### 业务概况（9 节）

完整模板见 [assets/templates/business_profile.md](assets/templates/business_profile.md)：

1. 公司基本定位（200-400 字）
2. 业务板块拆解（4 个板块各 150-300 字 + 每板块 1 张「业务数据 + 收入毛利率」组合表）
3. 主要产品矩阵（200-400 字 + 1 张产品表）
4. 经营业绩（300-400 字 + 1 张关键表 + 3 张分类汇总表）
5. 三年财务对比（1 张表）
6. 主要客户与销售（150-250 字 + 1 张客户表）
7. 核心竞争优势（300-500 字）
8. 未来增长逻辑（300-500 字）
9. 风险提示（200-400 字）

**表格上限**：业务概况 12 张（v2.1 起，原 8 张）/ 行业分析 6 张。

### 行业分析（7 节）

完整模板见 [assets/templates/industry_analysis.md](assets/templates/industry_analysis.md)：

1. 行业概况与发展阶段
2. 市场空间与增长趋势
3. 行业政策环境（国内/海外）
4. 产业链结构分析（上/中/下游）
5. 市场竞争格局
6. 行业发展趋势
7. 行业地位演变

## 关键规则

1. **由近及远**：以 2025 年为基准，2024/2023 用于趋势对比
2. **迭代保存**：每读一个章节写一轮，立即保存中间结果
3. **数据驱动**：所有数字带同比变化（如「同比 +17.04%」）；市场份额带具体数字
4. **只用年报数据**：不使用网络搜索补充
5. **数据缺失不外推**：年报未披露 → 单元格写「未披露」，叙述中说明
6. **不输出投资建议**：本 skill 只做业务和行业理解，不输出估值/目标价/买卖建议

## 错误处理

详见 [references/error_handling.md](references/error_handling.md)。

## 示例

完整跑通示例见 [examples/run_ningdeshishi_2025.md](examples/run_ningdeshishi_2025.md)。

## 版本

- v2.1（2026-06-22）：新增 4 类经营情况数据表（分业务/分产品/分地区收入毛利率 + 分业务经营数据）；表格上限 8→12
- v2（叙述式骨架）
- v1（填空式表格骨架，已废弃）

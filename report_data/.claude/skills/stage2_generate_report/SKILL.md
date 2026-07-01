---
name: stage2_generate_report
description: |
  多轮公司分析报告生成。基于 stage0（三年综合数据）和 stage1（业务概况 + 行业分析）的产物，合成一份面向读者的 9 节公司分析报告。采用 **v1 初稿 → 主线提炼 → v2 重写 → 审查 → 条件润色循环** 的闭环流程，解决单次端到端常出现的信息重复、数据缺失、语言生硬、逻辑断裂等问题。

  触发场景（任意一条即触发）：
  - 用户说「生成公司报告」「写公司分析报告」「综合报告」「最终报告」「年报解读」
  - 用户输入 `/stage2_generate_report {公司名}`
  - 用户输入 `@stage2_generate_report {公司名}`

  不适用于：港股/美股年报、招股书、半年报、季报、研报、新闻稿。
---

# Stage 2: 综合报告生成 (Generate Report)

## 目的

把 stage0 的**数据**和 stage1 的**业务/行业叙述**合成为一份**单文档最终报告**，让不熟悉该公司的读者 5 分钟读完即可形成完整认知。

> 详细的"为什么不一次端到端生成"与多轮设计动机见 [references/workflow.md](references/workflow.md) 顶部。

## 输入

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `company` | string | ✅ | 用户指定（如「宁德时代」） |
| `max_polish_rounds` | int | ❌ | 润色循环最大轮数，默认 2（即最多再润色 2 次，总版本数 ≤ 4） |

## 输出

| 输出 | 路径 | 描述 |
|------|------|------|
| 公司分析报告（最终版） | `{公司}/md/research_file/{公司}_公司报告.md` | 唯一对外产物 |
| 叙事主线（保留） | `{公司}/md/research_file/参考资料/{公司}_报告主线.md` | 透明性 + 复盘用 |
| 中间稿 | `{公司}/md/research_file/_drafts/{公司}_v{N}.md` | 调试用，可清理 |
| 审查报告 | `{公司}/md/research_file/_drafts/{公司}_review_v{N}.json` | 调试用，可清理 |

路径规范见 [references/path_conventions.md](references/path_conventions.md)。

## 强制依赖检查

> ⚠️ **本 skill 必须在 stage0 + stage1 全部产物存在时才能运行**，否则硬失败。

```python
from pathlib import Path

required = [
    f"{公司}/md/research_file/参考资料/{公司}_三年综合数据.md",  # stage0
    f"{公司}/md/research_file/{公司}_业务概况.md",               # stage1
    f"{公司}/md/research_file/{公司}_行业分析.md",               # stage1
]
missing = [p for p in required if not Path(p).exists()]
if missing:
    raise RuntimeError(
        f"前置产物缺失（{len(missing)} 个）：\n" +
        "\n".join(f"  - {p}" for p in missing) +
        f"\n\nstage2 依赖 stage0 + stage1。请按以下顺序运行：\n"
        f"  1. @stage0_data_aggregation {公司}     # 生成三年综合数据\n"
        f"  2. @stage1_business_understanding {公司} # 生成业务概况 + 行业分析\n"
        f"  3. @stage2_generate_report {公司}        # 生成本报告\n"
    )
```

**3 个强制依赖的设计动机**：本 skill 不重新读年报、不重新聚合数据、不重新写业务/行业——所有内容都从已有产物综合而来。这保证可追溯、幂等、成本最低。

## 工具

| SubAgent | 类型 | MODE | 任务 | 详细契约 |
|----------|------|------|------|----------|
| `report_writer` | report_writer | `write` | 端到端写 v1 初稿 | [subagent_contract.md](references/subagent_contract.md) |
| `report_writer` | report_writer | `mainline` | 提炼叙事主线 | [subagent_contract.md](references/subagent_contract.md) |
| `report_writer` | report_writer | `rewrite` | 基于主线/审查重写 v2+ | [subagent_contract.md](references/subagent_contract.md) |
| `report_reviewer` | general-purpose | `review` | 审查 → pass/fail + issues | [subagent_contract.md](references/subagent_contract.md) |

> `report_reviewer` 复用 `general-purpose` 加结构化 prompt，**不新增 agent 类型**，避免 agent 体系膨胀。

## 工作流程（8 阶段）

```
[1 依赖检查] → [2 读 3 份输入] → [3 识别最近一期年报]
                                            ↓
                       [4 写 v1 初稿: report_writer write]
                                            ↓
                       [5 提炼叙事主线: report_writer mainline]
                                            ↓
                       [6 基于主线重写 v2: report_writer rewrite]
                                            ↓
                       [7 审查 v2: report_reviewer]
                                            ↓
                                       pass?
                                       /    \
                                     yes    no
                                      ↓      ↓
                            [9 落盘最终版]   [8 润色循环 (≤max_polish_rounds 轮)]
                                            ↓
                                       [9 落盘最终版]
                                            ↓
                                       [10 Session 汇总]
```

### 各阶段一句话摘要

| 阶段 | 任务 | SubAgent |
|------|------|----------|
| 1 | 检查 3 个产物存在，否则硬失败 | — |
| 2 | Read 3 份输入材料 | — |
| 3 | 从 stage1 业务概况识别最近年份，定位年报目录 | — |
| 4 | 端到端写 v1 初稿（覆盖优先） | `report_writer` write |
| 5 | 提炼叙事主线（9 节论点 + 横向主题 + 关键数据点） | `report_writer` mainline |
| 6 | 基于主线重写 v2（强调贯穿/去重/措辞） | `report_writer` rewrite |
| 7 | 审查 v2 → pass/fail + critical_issues | `report_reviewer` |
| 8 | 润色循环：每轮 rewrite + review，未通过继续 | 两者 |
| 9 | 落盘最终版 + 验证 9 节齐全 | — |
| 10 | 记录 session 汇总 | — |

**完整流程、每个阶段的 prompt 模板、循环伪代码**见 [references/workflow.md](references/workflow.md)。

## 关键规则

1. **3 个强制依赖**：stage0 + stage1 全部产物必须存在，否则硬失败
2. **多轮闭环**：(v1) → mainline → (v2) → review → 条件 polish 循环。每次润色必须基于审查报告
3. **优先最近一期**：第七节必须从最近一年年报原文里捞新事件，不能用三年叙述替代
4. **新业务单独成节**：如果有新业务，第四节必须独立写；没有则写"近期无显著新业务方向"并简述原因
5. **不输出投资建议**：不给目标价、评级、买卖建议——本 skill 只做认知输出
6. **不修改前置产物**：不修改 stage0 / stage1 / mainline 产物；如发现数据问题，回退到对应 stage 修改
7. **叙述优先**：表格点睛，单节不超过 2 张表，整篇不超过 8 张
8. **数据驱动**：所有数字必须能在 stage0 参考资料或最近一年年报原文中找到出处
9. **路径硬规则**：写文件必须用 `/d/...` 前缀避免 sandbox 拦截
10. **drafts 目录**：中间稿和审查报告落盘到 `_drafts/`，最终版落盘到 `research_file/`

## 模板结构（9 节）

完整模板见 [assets/templates/report.md](assets/templates/report.md)：

| 节 | 标题 | 字数 | 表格 |
|----|------|------|------|
| 1 | 执行摘要 | 600-800 | 1 |
| 2 | 公司画像 | 300-400 | 1 |
| 3 | 主要业务 | 800-1200 | 2 |
| 4 | 新业务 | 400-600 | 1 |
| 5 | 盈利模式 | 600-800 | 2 |
| 6 | 发展现状与趋势 | 500-700 | 1 |
| 7 | 最近一期财务深度解读 | 500-700 | 1 |
| 8 | 风险提示 | 300-400 | 0 |
| 9 | 未来展望 | 400-500 | 0 |

**总表格上限**：8 张。

## 中间产物管理

- **保留**：主线文档 + 最终版报告
- **可清理**：`_drafts/v{N}.md` 和 `_drafts/review_v{N}.json` 调试后可删除

## 错误处理

详见 [references/error_handling.md](references/error_handling.md)（含 10 类错误 + 降级策略表）。

## 与其他 skill 的关系

```
stage0_data_aggregation → stage1_business_understanding → stage2_generate_report (本 skill)
```

| skill | 输出 | 何时调用 |
|-------|------|----------|
| stage0 | 三年综合数据 | 用户需要数据分析时 |
| stage1 | 业务概况 + 行业分析 | 用户需要业务/行业理解时 |
| stage2（本） | 单一公司分析报告 | 用户需要面向读者的最终报告时 |

## 示例

完整跑通示例见 [examples/run_ningdeshishi_2025.md](examples/run_ningdeshishi_2025.md)。

## 版本

- v2（2026-06-29）：引入多轮生成-主线-审查-润色闭环。重构拆分：workflow 详情移入 references/workflow.md。
- v1（2026-06-24）：初始版本。由 stage2_table_merge 重构而来。

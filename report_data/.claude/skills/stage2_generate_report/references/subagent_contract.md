# SubAgent 契约 (SubAgent Contract)

本 skill 调用的 SubAgent 类型与契约。

## 目录

- [总体架构](#总体架构)
- [report_writer SubAgent](#report_writer-subagent)
  - [MODE = write](#mode--write-v1-初稿)
  - [MODE = mainline](#mode--mainline-叙事主线)
  - [MODE = rewrite](#mode--rewrite-基于主线审查重写)
  - [Prompt 长度](#prompt-长度)
  - [输出形态](#输出形态)
  - [落盘机制](#落盘机制)
- [report_reviewer SubAgent](#report_reviewer-subagent)
  - [输入](#输入)
  - [输出](#输出)
  - [JSON 结构](#json-结构)
  - [Pass 标准](#pass-标准)
  - [错误处理](#错误处理)
- [调用时序](#调用时序)

## 总体架构

| SubAgent | 类型 | MODE | 任务 | 调用次数 |
|----------|------|------|------|----------|
| `report_writer` | report_writer | `write` | 端到端写 v1 初稿 | 1 |
| `report_writer` | report_writer | `mainline` | 提炼叙事主线 | 1 |
| `report_writer` | report_writer | `rewrite` | 基于主线/审查重写 v2+ | 1 + N |
| `report_reviewer` | general-purpose | `review` | 审查报告 → pass/fail + issues | N |

> 注：`report_reviewer` 不新增 agent 类型，而是用 `general-purpose` SubAgent + 结构化 prompt 实现，避免 agent 体系膨胀。

---

## report_writer SubAgent

**唯一**自定义 SubAgent 类型。完整调用契约与 stage1 一致，详见 [stage1/references/subagent_contract.md](../../stage1_business_understanding/references/subagent_contract.md)。

### 本 skill 的多 mode 约定

`report_writer` 在本 skill 承担 3 种角色，通过 prompt 中的 `MODE` 字段区分：

#### MODE = `write` (v1 初稿)

- **目标**：覆盖完整优先
- **输入**：3 份原始材料 + 最近一期关键章节
- **输出**：完整 9 节 markdown 文档，9 节齐全 + 数据齐全
- **不强调**：措辞、贯穿（由后续 mode 处理）
- **落盘路径**：`{公司}/md/research_file/_drafts/{公司}_v1.md`

#### MODE = `mainline` (叙事主线)

- **目标**：从 v1 + 原始材料中提炼贯穿 9 节的"读者读完后应该带走的故事"
- **输入**：v1 + 3 份原始材料
- **输出**：主线文档 markdown（**不落盘**，由 main agent 落盘）
- **主线文档结构**：
  - 一句话定位（150 字内）
  - 核心故事（2-3 段）
  - 9 节主线论点（每节 3-5 个 bullet）
  - 横向贯穿主题（3-5 个）
  - 关键数据点（5-8 个）
  - 风格基调
- **落盘路径**：由 main agent 落盘到 `{公司}/md/research_file/参考资料/{公司}_报告主线.md`

#### MODE = `rewrite` (基于主线/审查重写)

- **目标**：以主线为骨架 + 按审查报告反馈 → 真正对外版本
- **输入**：上一版 v_n + 审查报告 + 3 份原始材料 + 最近一期关键章节 + 叙事主线
- **输出**：完整 9 节 markdown 文档（v2 / v3 / v4）
- **强调**：主线贯穿、强制去重、横向主题一致、数据一致、措辞润色
- **特殊约束**：
  - 必修审查报告的每条 critical_issue
  - 不允许偷偷改数字（与 v_n 保持数据一致）
  - 处理完的 critical_issue 在文末加注释「[本节已处理: critical_issue N]」
- **落盘路径**：`{公司}/md/research_file/_drafts/{公司}_v{N}.md`

### Prompt 长度

可达 50-100 KB（stage0 参考资料 + stage1 两份 + 最近一期 + v_n + 审查报告 + mainline），需注意 token 上限。**v_n 超过 30 KB 时考虑摘要后传入**。

### 输出形态

完整 9 节 markdown 文档，UTF-8 编码。

### 落盘机制

runner（即 main agent）负责主线文档的落盘（mode=mainline），SubAgent 直接写报告文档（mode=write/rewrite）。

---

## report_reviewer SubAgent

**类型**：`general-purpose`（不复用自定义 agent，避免 agent 体系膨胀）。

### 任务

审查某一版报告（v2 / v3 / v4），输出**结构化 JSON 审查报告**。

### 输入

| 材料 | 必填 | 说明 |
|------|------|------|
| 待审查报告 v_n | ✅ | 完整 markdown |
| 数据底稿（stage0） | ✅ | 事实基准 |
| 业务叙述（stage1） | ✅ | 事实基准 |
| 行业叙述（stage1） | ✅ | 事实基准 |
| 叙事主线 | ✅ | 设计意图基准 |
| 报告模板摘要 | ✅ | 结构基准 |

### 输出

**只输出 JSON 字符串**，不解释、不 markdown 代码块包裹、不落盘。main agent 解析后落盘到 `_drafts/{公司}_review_v{N}.json`。

### JSON 结构

```json
{
  "score": <1-10 整数>,
  "pass": <bool, pass 标准: critical_issues 为空且 score >= 7>,
  "summary": "<整体评价 1-2 句>",
  "critical_issues": [
    {
      "type": "信息重复" | "数据缺失" | "逻辑断裂" | "事实错误" | "章节缺失",
      "location": "<节名 / 段号>",
      "description": "<具体问题>",
      "suggestion": "<修复建议>"
    }
  ],
  "minor_issues": [
    {
      "type": "语言生硬" | "措辞" | "表格" | "结构",
      "location": "<节名 / 段号>",
      "description": "<具体问题>",
      "suggestion": "<修复建议>"
    }
  ],
  "section_feedback": {
    "执行摘要": "<一句话评价 + 改进方向>",
    "公司画像": "...",
    "主要业务": "...",
    "新业务": "...",
    "盈利模式": "...",
    "发展现状与趋势": "...",
    "最近一期财务深度解读": "...",
    "风险提示": "...",
    "未来展望": "..."
  }
}
```

### Pass 标准

| 条件 | 是否必须 |
|------|----------|
| `critical_issues` 为空数组 | ✅ 必要条件 |
| `score >= 7` | ✅ 必要条件 |
| `minor_issues` 为空 | ❌ 非必要（润色循环外允许残留） |

### 错误处理

详见 [error_handling.md](error_handling.md)。

---

## 调用时序

```
1. main agent: 依赖检查 → 通过
2. main agent: Read 3 个输入文件
3. main agent: 识别最近年份 + 定位最近一期年报
4. main agent: Read 最近一期关键章节（2-3 个）
5. main agent: log_start(agent_name="report_writer", mode="write")
6. main agent → report_writer (write): 构造 prompt
7. report_writer: 撰写 v1 → 调 Write 落盘到 _drafts/v1.md
8. main agent: log_end(call_id)
9. main agent: log_start(agent_name="report_writer", mode="mainline")
10. main agent → report_writer (mainline): 构造 prompt
11. report_writer: 提炼主线 → 返回 markdown 字符串
12. main agent: 用 Write 落盘到 参考资料/报告主线.md
13. main agent: log_end(call_id)
14. main agent: log_start(agent_name="report_writer", mode="rewrite")
15. main agent → report_writer (rewrite): 构造 prompt
16. report_writer: 重写 v2 → 调 Write 落盘到 _drafts/v2.md
17. main agent: log_end(call_id)
18. main agent: log_start(agent_name="report_reviewer")
19. main agent → general-purpose (review): 构造 prompt
20. general-purpose: 审查 v2 → 返回 JSON 字符串
21. main agent: 用 Write 落盘到 _drafts/review_v1.json
22. main agent: log_end(call_id)
23. main agent: 判断 review_v1.pass
    ├─ true: 跳过润色循环
    └─ false: 进入润色循环 (24)
24. main agent: 润色循环 (最多 max_polish_rounds 轮)
    每轮:
      a. log_start(report_writer, mode="rewrite", round=N)
      b. 调 report_writer → v_{N+1} → _drafts/v_{N+1}.md
      c. log_end
      d. log_start(report_reviewer, round=N+1)
      e. 调 general-purpose → review v_{N+1}
      f. main agent 落盘到 _drafts/review_v{N}.json
      g. log_end
      h. 判断 pass → break / continue
25. main agent: 落盘最终版到 research_file/{公司}_公司报告.md
26. main agent: 验证 (9 节齐全)
27. main agent: log_session_summary
```

## 错误处理

详见 [error_handling.md](error_handling.md)。

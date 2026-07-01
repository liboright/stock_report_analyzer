# llm_logger API

LLM 调用日志记录器，用于自动追踪每次 Agent / 工具调用。

## 目录

- [5 个核心 API](#5-个核心-api)
- [初始化](#初始化)
- [完整调用示例](#完整调用示例)
- [强制记录规则](#强制记录规则)
- [日志输出位置与规模](#日志输出位置与规模)

## 5 个核心 API

| API | 用途 | 调用时机 |
|-----|------|---------|
| `logger.log_agent_call(agent, task, prompt, response, metadata, parent_call_id)` | 记录完整 LLM 调用（含 prompt + response）| 每次调 Agent 后 |
| `logger.log_subagent_call(parent_call_id, agent, task, prompt, response)` | 记录子智能体调用（自动关联父调用）| 嵌套 Agent 调用后 |
| `logger.log_step(step_name, action, data)` | 记录非 LLM 步骤 | Read/Glob/Write/Edit/Bash |
| `logger.log_session_summary(summary)` | 写 session 汇总 | skill 结束时 |
| `logger.log_start` / `logger.log_end` | 简化版开始/结束配对 | 旧代码用 |

## 初始化

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(<skill_path>).parent))  # 把 skill 父目录加进去
from scripts.llm_logger import get_logger

logger = get_logger("宁德时代")  # base_dir 自动推断为 宁德时代/output/
```

## 完整调用示例

```python
# 1. 步骤：扫描数据
logger.log_step(
    step_name="Bash - 扫描年报章节",
    action="扫描 2023/2024/2025 三年管理层讨论章节",
    data={"2025": ["01_xxx.md"], "跳过": ["03_套话"]},
)

# 2. 步骤：读章节
logger.log_step(
    step_name="Read - 章节",
    action="读取 贵州茅台/md/clean/.../管理层讨论/05_主要经营.md",
    data="字符数: ~25000",
)

# 3. 主 Agent 调用（含完整 prompt + response）
parent_id = logger.log_agent_call(
    agent_name="report_writer",
    task_description="生成业务概况 + 行业分析",
    prompt="<完整 prompt>",
    response="<完整 response>",
    metadata={"公司": "宁德时代", "年份": "2023-2025", "阶段": "第二阶段"},
)

# 4. 子 Agent 调用
sub_id = logger.log_subagent_call(
    parent_call_id=parent_id,
    agent_name="report_writer",
    task_description="增量补充 2023 年数据",
    prompt="...",
    response="...",
)

# 5. 落盘
logger.log_step(
    step_name="Write - 落盘",
    action="解析 markdown 代码块并写入",
    data={"路径": "宁德时代/md/research_file/宁德时代_业务概况.md", "字节数": 12880},
)

# 6. session 汇总
logger.log_session_summary({
    "完成公司": ["宁德时代"],
    "产物": ["宁德时代_业务概况.md", "宁德时代_行业分析.md"],
    "SubAgent 调用": 1,
})
```

## 强制记录规则

不调用视为「skill 未完整运行」：

| 触发动作 | 必须调用的 API |
|---------|---------------|
| 调用 Agent | `log_agent_call` 或 `log_subagent_call` |
| 调用 Read/Glob/Write/Edit/Bash | `log_step` |
| skill 结束 | `log_session_summary` |

## 日志输出位置与规模

- 输出位置：`<base_dir>/log/llm_log_<timestamp>.txt`
- 默认 `<base_dir>` = `{公司}/output/`
- 一次 skill 运行产生 **1 个** 日志文件（共享 timestamp）
- 文件末尾自动追加 `# Session 汇总` 区块
- Prompt / Response 上限各 20 万字符（超出自动截断并标注）
- 一次 v2 流程典型日志大小：20-50 KB（约 13 条记录）

"""
LLM 调用日志记录工具
====================
用于自动记录每次 LLM (Agent) 调用的完整 prompt 和 response，包括主智能体与子智能体调用。

核心 API（按使用频率排序）：
- `log_agent_call(agent_name, task, prompt, response, ...)` —— 记录一次 Agent 调用（完整 prompt + response）
- `log_subagent_call(parent_call_id, agent_name, task, prompt, response, ...)` —— 记录子智能体调用
- `log_step(step_name, action, data=None)` —— 记录非 LLM 步骤（Read / Glob / 落盘 等）
- `log_start(...)` / `log_end(...)` —— 简化版「开始/结束」配对日志（保留向后兼容）
- `log_call(...)` —— 一体化调用（保留向后兼容）

每条记录使用 `===` 分隔，自动写入 `<base_dir>/log/llm_log_<timestamp>.txt`。
"""

import os
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any


class LLMLogger:
    """LLM 调用日志记录器"""

    # 单条 prompt / response 的字符上限（防止单文件过大）
    PROMPT_LIMIT = 200_000
    RESPONSE_LIMIT = 200_000

    def __init__(self, company: str, base_dir: str):
        """
        初始化日志记录器

        Args:
            company: 公司名称
            base_dir: 基础目录 (md/{公司名}/output/)
        """
        self.company = company
        self.base_dir = Path(base_dir)
        self.log_dir = self.base_dir / "log"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 生成 session 时间戳（一次 skill 运行共享同一 timestamp）
        self.timestamp = int(time.time() * 1000)
        self.log_file = self.log_dir / f"llm_log_{self.timestamp}.txt"
        self.current_call_id = 0
        self.call_registry: dict[str, dict] = {}  # call_id -> {agent, task, prompt_len, response_len}

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    def _next_call_id(self) -> str:
        """生成下一个 call_id（自增）"""
        self.current_call_id += 1
        return f"{self.timestamp}_{self.current_call_id}"

    def _write(self, lines: list[str]) -> None:
        """写入日志文件（追加模式）"""
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _truncate(self, text: str, limit: int) -> tuple[str, bool]:
        """超长文本截断（返回 (truncated_text, was_truncated)）"""
        if len(text) > limit:
            return (
                text[:limit] + f"\n\n[... 已截断：原文 {len(text)} 字符，超出限制 {limit} 字符 ...]",
                True,
            )
        return text, False

    def _header(self, lines: list[str], title: str, call_id: str) -> list[str]:
        """通用日志开头"""
        lines.append("=" * 70)
        lines.append(f"{title} #{call_id}")
        lines.append("=" * 70)
        lines.append(f"时间: {datetime.now().isoformat()}")
        return lines

    # ------------------------------------------------------------------
    # 主 API
    # ------------------------------------------------------------------

    def log_agent_call(
        self,
        agent_name: str,
        task_description: str,
        prompt: str,
        response: str = None,
        metadata: dict = None,
        parent_call_id: Optional[str] = None,
        truncate: bool = True,
    ) -> str:
        """
        记录一次完整的 LLM Agent 调用（**推荐使用**，完整 prompt + 完整 response）。

        Args:
            agent_name: Agent 名称（"report_writer" / "general-purpose" / "dev-coder" 等）
            task_description: 任务描述
            prompt: 完整输入提示词
            response: 完整 LLM 回复（可选）
            metadata: 附加元数据（如 {"year": 2025, "phase": "第二阶段"}）
            parent_call_id: 父调用 call_id（子智能体调用时填写，便于追溯调用链）
            truncate: 是否对超长文本截断（默认 True）

        Returns:
            call_id 用于匹配结束日志或子调用
        """
        call_id = self._next_call_id()
        prompt_text, prompt_truncated = self._truncate(prompt, self.PROMPT_LIMIT) if truncate else (prompt, False)
        response_text, response_truncated = (
            self._truncate(response, self.RESPONSE_LIMIT) if (truncate and response) else (response, False)
        )

        lines = self._header([], "LLM 调用", call_id)
        lines.append(f"公司: {self.company}")
        lines.append(f"Agent: {agent_name}")
        lines.append(f"任务: {task_description}")
        if parent_call_id:
            lines.append(f"父调用: {parent_call_id}")

        if metadata:
            lines.append(f"元数据: {json.dumps(metadata, ensure_ascii=False, indent=2)}")

        lines.append(f"Prompt 长度: {len(prompt)} 字符" + ("（已截断）" if prompt_truncated else ""))
        lines.append(f"Response 长度: {len(response) if response else 0} 字符" + ("（已截断）" if response_truncated else ""))

        lines.append("")
        lines.append("--- Prompt (完整) ---")
        lines.append(prompt_text)
        if response is not None:
            lines.append("")
            lines.append("--- Response (完整) ---")
            lines.append(response_text)
        lines.append("")
        lines.append("=" * 70)
        lines.append("")

        self._write(lines)

        self.call_registry[call_id] = {
            "agent": agent_name,
            "task": task_description,
            "parent": parent_call_id,
            "prompt_len": len(prompt),
            "response_len": len(response) if response else 0,
        }
        return call_id

    def log_subagent_call(
        self,
        parent_call_id: str,
        agent_name: str,
        task_description: str,
        prompt: str,
        response: str = None,
        metadata: dict = None,
    ) -> str:
        """
        记录子智能体调用（继承父调用上下文）。

        Args:
            parent_call_id: 父调用的 call_id
            其余参数同 log_agent_call
        """
        metadata = dict(metadata or {})
        metadata["parent_call_id"] = parent_call_id
        return self.log_agent_call(
            agent_name=agent_name,
            task_description=task_description,
            prompt=prompt,
            response=response,
            metadata=metadata,
            parent_call_id=parent_call_id,
        )

    def log_step(self, step_name: str, action: str, data: Any = None) -> str:
        """
        记录非 LLM 步骤（Read / Glob / Write / Bash 等纯工具调用，便于完整复盘工作流）。

        Args:
            step_name: 步骤名（如 "Read 章节" / "Glob 章节文件" / "落盘 业务概况"）
            action: 具体动作描述
            data: 附加数据（如文件路径、内容摘要等）

        Returns:
            step_id
        """
        step_id = self._next_call_id()
        lines = self._header([], "步骤", step_id)
        lines.append(f"步骤名: {step_name}")
        lines.append(f"动作: {action}")
        if data is not None:
            data_repr = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2, default=str)
            data_repr, _ = self._truncate(data_repr, 50_000)
            lines.append(f"数据: {data_repr}")
        lines.append("")
        lines.append("=" * 70)
        lines.append("")
        self._write(lines)
        return step_id

    # ------------------------------------------------------------------
    # 向后兼容 API
    # ------------------------------------------------------------------

    def log_call(
        self,
        agent_name: str,
        task_description: str,
        prompt: str,
        response: str = None,
        error: str = None,
        metadata: dict = None
    ):
        """向后兼容：旧版一体化 API（调用 log_agent_call）"""
        call_id = self.log_agent_call(
            agent_name=agent_name,
            task_description=task_description,
            prompt=prompt,
            response=response,
            metadata=metadata,
        )
        if error:
            self._write([f"[error] {call_id}: {error}", ""])
        return call_id

    def log_start(self, agent_name: str, task_description: str, metadata: dict = None) -> str:
        """
        简化版：只记录任务开始（适用于「先开始、后写结束」的场景）。
        **配合 log_end() 一起使用**。
        """
        call_id = self._next_call_id()
        lines = self._header([], "任务开始", call_id)
        lines.append(f"公司: {self.company}")
        lines.append(f"Agent: {agent_name}")
        lines.append(f"任务: {task_description}")
        if metadata:
            lines.append(f"元数据: {json.dumps(metadata, ensure_ascii=False, indent=2)}")
        lines.append("")
        self._write(lines)
        return call_id

    def log_end(self, call_id: str, result_summary: str = None, error: str = None):
        """简化版：记录任务结束（与 log_start 配对）"""
        lines = [""]
        lines.append("-" * 70)
        lines.append(f"任务结束 #{call_id}")
        lines.append(f"时间: {datetime.now().isoformat()}")
        if result_summary:
            summary, _ = self._truncate(result_summary, 5_000)
            lines.append(f"结果摘要: {summary}")
        if error:
            lines.append(f"错误: {error}")
        lines.append("=" * 70)
        lines.append("")
        self._write(lines)

    # ------------------------------------------------------------------
    # 会话结束 / 摘要
    # ------------------------------------------------------------------

    def log_session_summary(self, summary: dict = None) -> None:
        """
        在 session 结束时写一份汇总：调用链、总调用次数、token 估算等。

        Args:
            summary: 自定义摘要 dict（如 {"完成章节": ["01_...", "05_..."], "产物": [...]})
        """
        lines = [""]
        lines.append("#" * 70)
        lines.append(f"Session 汇总 @ {datetime.now().isoformat()}")
        lines.append("#" * 70)
        lines.append(f"公司: {self.company}")
        lines.append(f"日志文件: {self.log_file}")
        lines.append(f"总调用次数: {self.current_call_id}")
        lines.append("")
        lines.append("--- 调用清单 ---")
        for cid, info in self.call_registry.items():
            lines.append(
                f"  [{cid}] {info['agent']} | {info['task'][:50]} | "
                f"prompt={info['prompt_len']}c response={info['response_len']}c"
                + (f" | parent={info['parent']}" if info['parent'] else "")
            )
        if summary:
            lines.append("")
            lines.append("--- 自定义摘要 ---")
            lines.append(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        lines.append("#" * 70)
        lines.append("")
        self._write(lines)


def get_logger(company: str, base_dir: str = None) -> LLMLogger:
    """
    获取日志记录器实例

    Args:
        company: 公司名称
        base_dir: 基础目录，默认根据 company 自动推断

    Returns:
        LLMLogger 实例
    """
    if base_dir is None:
        # 自动推断为 md/{company}/output/
        current_dir = Path(__file__).parent
        base_dir = current_dir.parent.parent.parent / "md" / company / "output"

    return LLMLogger(company, str(base_dir))


if __name__ == "__main__":
    # 演示新 API
    import sys
    if len(sys.argv) < 2:
        print("用法: python llm_logger.py <公司名>")
        sys.exit(1)

    company = sys.argv[1]
    logger = get_logger(company)

    # 1. 步骤日志
    logger.log_step("Read", "读取年报章节 01_一、报告期内公司从事的业务情况.md", data="文件路径: 贵州茅台/md/clean/...md\n字符数: 1500")

    # 2. 子智能体调用
    parent_id = logger.log_agent_call(
        agent_name="general-purpose",
        task_description="主任务：生成业务概况 + 行业分析",
        prompt="你是报告生成助手...",
        response="已生成...",
        metadata={"phase": "第二阶段", "year": 2025},
    )

    # 3. 子智能体调用
    logger.log_subagent_call(
        parent_call_id=parent_id,
        agent_name="report_writer",
        task_description="增量补充多年财务数据",
        prompt="基于上述内容补全 2023 年数据...",
        response="已补全...",
    )

    # 4. 会话汇总
    logger.log_session_summary({"完成章节": ["01_...", "05_..."], "产物": ["业务概况.md", "行业分析.md"]})

    print(f"日志已保存到: {logger.log_file}")
"""
LLM 调用日志记录工具
用于自动记录每次 LLM (Agent) 调用的 prompt 和 response
"""

import os
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any


class LLMLogger:
    """LLM 调用日志记录器"""

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

        # 生成时间戳
        self.timestamp = int(time.time() * 1000)
        self.log_file = self.log_dir / f"llm_log_{self.timestamp}.txt"
        self.current_call_id = 0

    def log_call(
        self,
        agent_name: str,
        task_description: str,
        prompt: str,
        response: str = None,
        error: str = None,
        metadata: dict = None
    ):
        """
        记录一次 LLM 调用

        Args:
            agent_name: Agent 名称 (如 "report_writer", "dev-coder" 等)
            task_description: 任务描述
            prompt: 输入提示词
            response: LLM 回复 (可选)
            error: 错误信息 (可选)
            metadata: 附加元数据 (可选)
        """
        self.current_call_id += 1
        call_id = f"{self.timestamp}_{self.current_call_id}"

        log_entry = []
        log_entry.append("=" * 60)
        log_entry.append(f"LLM 调用日志 #{call_id}")
        log_entry.append("=" * 60)
        log_entry.append(f"时间: {datetime.now().isoformat()}")
        log_entry.append(f"公司: {self.company}")
        log_entry.append(f"Agent: {agent_name}")
        log_entry.append(f"任务: {task_description}")

        if metadata:
            log_entry.append(f"元数据: {json.dumps(metadata, ensure_ascii=False, indent=2)}")

        log_entry.append("")
        log_entry.append("--- Prompt ---")
        log_entry.append(prompt[:50000] if len(prompt) > 50000 else prompt)  # 限制长度

        if response:
            log_entry.append("")
            log_entry.append("--- Response ---")
            log_entry.append(response[:100000] if len(response) > 100000 else response)

        if error:
            log_entry.append("")
            log_entry.append("--- Error ---")
            log_entry.append(error)

        log_entry.append("")
        log_entry.append("=" * 60)
        log_entry.append("")

        # 写入文件
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_entry))

        return call_id

    def log_start(self, agent_name: str, task_description: str, metadata: dict = None) -> str:
        """
        记录任务开始（简化版）

        Returns:
            call_id 用于匹配结束日志
        """
        self.current_call_id += 1
        call_id = f"{self.timestamp}_{self.current_call_id}"

        log_entry = []
        log_entry.append("=" * 60)
        log_entry.append(f"任务开始 #{call_id}")
        log_entry.append("=" * 60)
        log_entry.append(f"时间: {datetime.now().isoformat()}")
        log_entry.append(f"公司: {self.company}")
        log_entry.append(f"Agent: {agent_name}")
        log_entry.append(f"任务: {task_description}")

        if metadata:
            log_entry.append(f"元数据: {json.dumps(metadata, ensure_ascii=False, indent=2)}")

        log_entry.append("")

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_entry))

        return call_id

    def log_end(self, call_id: str, result_summary: str = None, error: str = None):
        """
        记录任务结束
        """
        log_entry = []
        log_entry.append("")
        log_entry.append(f"任务结束 #{call_id}")
        log_entry.append(f"时间: {datetime.now().isoformat()}")

        if result_summary:
            log_entry.append(f"结果摘要: {result_summary[:2000]}")

        if error:
            log_entry.append(f"错误: {error}")

        log_entry.append("=" * 60)
        log_entry.append("")

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(log_entry))


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
        # 脚本位于 .claude/skills/{skill_name}/ 下
        # 向上三级：skills -> .claude -> 项目根目录 -> md/{company}/output
        current_dir = Path(__file__).parent
        base_dir = current_dir.parent.parent.parent / "md" / company / "output"

    return LLMLogger(company, str(base_dir))


if __name__ == "__main__":
    # 测试代码
    import sys
    if len(sys.argv) < 2:
        print("用法: python llm_logger.py <公司名>")
        sys.exit(1)

    company = sys.argv[1]
    logger = get_logger(company)

    # 测试记录
    logger.log_call(
        agent_name="test_agent",
        task_description="测试任务",
        prompt="这是一个测试 prompt",
        response="这是一个测试 response",
        metadata={"test": True}
    )

    print(f"日志已保存到: {logger.log_file}")

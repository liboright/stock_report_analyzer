"""标题层级转换器。

将 MinerU 输出的统一一级标题（`#`）转换为正确的 Markdown 层级（`##`、`###` 等）。
从 `D:/quant/report_gen/report_generator/parser/heading_level_converter.py` 复制，
仅去掉 `if __name__ == "__main__":` 测试块。
"""
from __future__ import annotations

import re
from typing import List, Tuple


class HeadingLevelConverter:
    """将 MinerU 的统一一级标题转换为正确的 Markdown 层级。"""

    # (正则模式, 目标层级)
    # 1=# (第X节), 2=## (一、), 3=### (1、), 4=#### （（1））, 5=##### (1）)
    PATTERNS: List[Tuple[str, int]] = [
        (r"^第[一二三四五六七八九十百千]+节\s+", 1),   # 第一节  -> #
        (r"^[一二三四五六七八九十百千]+、", 2),         # 一、    -> ##
        (r"^\d+、", 3),                                 # 1、     -> ###
        (r"^（\d+）", 4),                               # （1）   -> ####
        (r"^\d+）", 5),                                 # 1）     -> #####
    ]

    def __init__(self) -> None:
        self._converted_count = 0

    def convert(self, md_content: str) -> str:
        self._converted_count = 0
        lines = md_content.split("\n")
        result: List[str] = []
        for line in lines:
            # 只处理 MinerU 的统一一级标题（"# " 开头但不以 "##" 开头）
            if line.startswith("# ") and not line.startswith("##"):
                converted_line = self._convert_heading(line)
                result.append(converted_line)
                if converted_line != line:
                    self._converted_count += 1
            else:
                result.append(line)
        return "\n".join(result)

    def _convert_heading(self, line: str) -> str:
        content = line[2:]
        level = self._detect_level(content)
        return "#" * level + " " + content

    def _detect_level(self, content: str) -> int:
        for pattern, level in self.PATTERNS:
            if re.match(pattern, content):
                return level
        return 2  # 默认 ##

    @property
    def converted_count(self) -> int:
        return self._converted_count


def convert_mineru_markdown(md_content: str) -> str:
    converter = HeadingLevelConverter()
    return converter.convert(md_content)

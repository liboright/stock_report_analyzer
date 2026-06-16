"""单元格 / 文本清洗工具（纯函数，无状态）。

清洗规则（按优先级）：
1. LaTeX 残留
    - $1 5 . 0 1 \\%$           → "15.01%"
    - $200\\mathrm{Wh/kg}$     → "200Wh/kg"
    - 兜底任何 $...$            → 去 $，折叠空白
2. 方框字符 □ ■ ☐ ☒ \\u00A0     → 删除
3. 连续空白                     → 折叠为单空格
4. None                         → ""

折行合并（merge_continuation_rows）：见 plan D-8 节判定条件。
"""

from __future__ import annotations

import re
from typing import List, Optional

# 预编译正则
_LATEX_DIGIT_PERCENT = re.compile(r"\$\s*([\d\s,.\-+]+?)\s*\\?%\s*\$")
"""$1 5 . 0 1 \\%$ 类纯数字百分比。"""

_LATEX_DIGIT_UNIT = re.compile(
    r"\$\s*([\d\s,.\-+]+?)\s*\\(?:mathrm|text|mathit)\s*\{([^}]+)\}\s*\$"
)
"""$200\\mathrm{Wh/kg}$ 类带 \\mathrm{...} 单位的数值。"""

_LATEX_GENERIC = re.compile(r"\$([^$]+)\$")
"""兜底：任何 $...$ 包裹的内容。"""

_BOX_CHARS = re.compile(r"[\u25A1\u25A0\u2610\u2611\u2612\u00A0]")
"""空心方框 ☐ 实心方框 ■ 复选框 ☐☒ + 不间断空格 nbsp。"""

_WS_COLLAPSE = re.compile(r"\s+")

# CJK 字符之间不应有空白：处理 HTML <td> 内含字面换行被折叠成 "项目 名称" 的情况
# 匹配 [CJK 字符] + 任意空白 + [CJK 字符] → 两个 CJK 字符直接拼接
_CJK_BETWEEN_WS = re.compile(
    r"([\u4e00-\u9fff\u3400-\u4dbf])\s+([\u4e00-\u9fff\u3400-\u4dbf])"
)

# 判定一个 cell 是否"像 data value"（用于判断首行是否真的是 header）。
# 包含千分位逗号、百分号、小数 → 是值；否则视为文本标签。
_VALUE_LIKE = re.compile(r"[,%]|\d+\.\d+")


def _strip_latex(text: str) -> str:
    """剥离 LaTeX 残留（按优先级处理）。"""

    def _repl_digit_percent(m: re.Match[str]) -> str:
        digits = _WS_COLLAPSE.sub("", m.group(1))
        return f"{digits}%"

    def _repl_digit_unit(m: re.Match[str]) -> str:
        digits = _WS_COLLAPSE.sub("", m.group(1))
        unit = _WS_COLLAPSE.sub("", m.group(2))
        return f"{digits}{unit}"

    def _repl_generic(m: re.Match[str]) -> str:
        inner = m.group(1)
        inner = inner.replace("\\%", "%")
        inner = re.sub(r"\\[a-zA-Z]+\s*\{([^}]*)\}", r"\1", inner)
        inner = inner.replace("\\", "")
        return _WS_COLLAPSE.sub("", inner)

    text = _LATEX_DIGIT_PERCENT.sub(_repl_digit_percent, text)
    text = _LATEX_DIGIT_UNIT.sub(_repl_digit_unit, text)
    text = _LATEX_GENERIC.sub(_repl_generic, text)
    return text


def clean_cell(value: Optional[str]) -> str:
    """清洗单个单元格文本。

    None → ""；剥 LaTeX 残留；去方框字符；折叠空白；
    移除 CJK 字符之间的空白（处理 HTML 字面换行造成的 "项目 名称" → "项目名称"）；
    strip。
    """
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    text = _strip_latex(text)
    text = _BOX_CHARS.sub("", text)
    text = _WS_COLLAPSE.sub(" ", text)
    text = _CJK_BETWEEN_WS.sub(r"\1\2", text)
    return text.strip()


def row_looks_like_header(row: List[Optional[str]]) -> bool:
    """判断 row 是否"看起来像 header 行"。

    用于装配层兜底：外部 parser 漏检 header 时，只在首行像 header
    （所有非空 cell 都是文本标签，不含逗号/百分号/小数）才把它当 header；
    否则（如 "字段 | 值" 类的 KV 摘要表）保留为 data 行。
    """
    for cell in row:
        if cell is None:
            continue
        text = str(cell).strip()
        if not text:
            continue
        if _VALUE_LIKE.search(text):
            return False
    return True


def clean_text(value: Optional[str]) -> str:
    """清洗一般文本（标题 / 单位行 / 表头）。

    与 clean_cell 相同的规则，但保留为独立函数以便未来调整粒度。
    """
    return clean_cell(value)


def _is_group_header_row(cleaned_row: List[str]) -> bool:
    """是否为分组标题行（仅一个非空单元格、覆盖整行）。"""
    non_empty = [c for c in cleaned_row if c]
    return len(non_empty) == 1


def merge_continuation_rows(
    grid: List[List[Optional[str]]],
    col_count: Optional[int] = None,
) -> List[List[str]]:
    """折行合并：把"前 N-1 列空 + 最后一列有续写"的行合并到上一行最后一列。

    判定条件（同时满足）：
    1. 当前行的非空 cell 都集中在最后一列；
    2. 非空 cell 数 ≤ 2；
    3. 不是单一非空 cell 且占第一列（那是分组标题行）；
    4. 上一行存在。

    合并方式: prev[last_col] + "\\n" + cur[last_col]。
    """
    if not grid:
        return []

    if col_count is None:
        col_count = max(len(row) for row in grid) if grid else 0
    if col_count == 0:
        return [[clean_cell(c) for c in row] for row in grid]

    last_col = col_count - 1

    # 预处理：先做清洗，得到字符串网格
    cleaned: List[List[str]] = []
    for row in grid:
        new_row: List[str] = []
        for j in range(col_count):
            cell = row[j] if j < len(row) else None
            new_row.append(clean_cell(cell))
        cleaned.append(new_row)

    # 折行合并：从前往后扫，合并到上一行
    result: List[List[str]] = []
    for row in cleaned:
        non_empty_indices = [j for j, c in enumerate(row) if c]

        is_continuation = (
            len(result) > 0
            and 0 < len(non_empty_indices) <= 2
            and all(j == last_col for j in non_empty_indices)
        )
        if is_continuation:
            prev = result[-1]
            prev_last = prev[last_col]
            cur_last = row[last_col]
            merged = (prev_last + "\n" + cur_last) if prev_last else cur_last
            prev[last_col] = merged
            continue

        result.append(row)

    return result

"""年份标准化工具（纯函数，无状态）。

负责把表头列名和正文文本中的相对年份表述（本年度 / 上年度 / 本期 / 上期 /
本报告期 / 上年同期 / 前年 / 期末 / 期初 等）映射为具体的 YYYY 年。

关键边界：
    "营业收入比上年同期增减" 既含 "上年同期" 又含 "增减"。
    必须由 detect_yoy_column 在装配层优先判定为 yoy 列，
    本模块的 normalize_year_in_cell 也会在该单元格内置 YoY 关键词时直接跳过。
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from .models import YearMapping

# 显式年份："2024年" / "2024 年"
_RE_EXPLICIT_YEAR = re.compile(r"(19|20)\d{2}\s*年")
"""带"年"后缀的 4 位数字。"""

_RE_EXPLICIT_YEAR_NUMBER_ONLY = re.compile(r"(19|20)\d{2}")
"""任意 4 位数字（用于提取年份值，不强制要求"年"后缀）。"""

# 同比 / 变化类（不改写文本，仅标记 is_yoy）
# 关键边界：不放裸 "变化"（会误匹配 "是否发生重大变化" 这类是非列），
# 限定为 "变化率"（明确的百分比同比）或 "同比/增减/增幅/降幅" 等强信号。
_RE_YOY_LABEL = re.compile(
    r"同比|比上年同期|变动比例|变动率|变化率|增减|增减幅?|增幅|降幅|yoy",
    re.IGNORECASE,
)

# 当前年份（本期 / 本年度 / 本报告期 等）
# 关键边界：不放裸 "报告期" / "报告期内"，避免误匹配描述性文本
# 如 "报告期内取得和处置子公司方式" —— 这里 "报告期内" 是描述，
# 不是年份引用。带 "本" 前缀的 "本报告期" 是明确年份引用，可保留。
_RE_CURRENT_YEAR = re.compile(
    r"本报告期|本期|报告期末|本年度|本年|当期|当年度|当年"
)

# 上一年（上年度 / 上年同期 / 上期 / 期初 / 年初 等）
_RE_PREVIOUS_YEAR = re.compile(
    r"上年同期|上一年同期|上一年度|上年度|上一年|上年末|上年|上期|上一期|期初|年初|去年同期|去年"
)

# 前年 / 上上年
_RE_TWO_YEARS_AGO = re.compile(r"前年|上上年|两年前|上上年度")


def build_year_mapping(report_year: int) -> YearMapping:
    """根据报告期年份构造年份映射。"""
    return YearMapping(
        report_year=report_year,
        current_year=report_year,
        previous_year=report_year - 1,
        year_before_previous=report_year - 2,
        applied_count=0,
    )


def detect_yoy_column(header_text: str) -> bool:
    """判断表头单元格是否为"同比 / 变化 / 增减"类列。"""
    if not header_text:
        return False
    return bool(_RE_YOY_LABEL.search(header_text))


def extract_explicit_year(text: str) -> Optional[int]:
    """从文本中提取显式 4 位年份（若有"年"后缀优先）。

    返回 int 或 None。
    """
    if not text:
        return None
    m = _RE_EXPLICIT_YEAR.search(text)
    if m:
        try:
            return int(m.group(0).rstrip("年").strip())
        except ValueError:
            pass
    # 兜底：纯 4 位数字（如表头是裸 "2024"）
    m2 = _RE_EXPLICIT_YEAR_NUMBER_ONLY.search(text)
    if m2:
        try:
            return int(m2.group(0))
        except ValueError:
            return None
    return None


def normalize_year_in_cell(
    text: str, mapping: YearMapping
) -> Tuple[str, bool]:
    """把单元格 / 文本中的相对年份替换为具体 YYYY 年。

    跳过条件：
    - 文本含同比 / 变化 / 增减关键词（is_yoy 列保持原文）
    - 文本已含显式年份且没有任何相对年份关键词（避免误改）

    Returns:
        (new_text, changed) - 改写后文本 + 是否实际改动
    """
    if not text:
        return text, False

    # 同比 / 增减类：不改写
    if detect_yoy_column(text):
        return text, False

    original = text
    new = text

    # 按"短词在先、长词在后"或反之都有歧义，这里通过 finditer 排序最长匹配优先
    # 但简化策略：先 _RE_TWO_YEARS_AGO（最长且最特殊）→ _RE_PREVIOUS_YEAR → _RE_CURRENT_YEAR
    new = _RE_TWO_YEARS_AGO.sub(f"{mapping.year_before_previous}年", new)
    new = _RE_PREVIOUS_YEAR.sub(f"{mapping.previous_year}年", new)
    new = _RE_CURRENT_YEAR.sub(f"{mapping.current_year}年", new)

    return new, (new != original)


def normalize_year_in_text(
    text: str, mapping: YearMapping
) -> Tuple[str, int]:
    """对一段正文（非单元格）做年份标准化，返回 (新文本, 替换处数)。

    与 normalize_year_in_cell 不同：正文中"同比"类字眼可能与年份关键词
    出现在同一段，但不应阻止年份替换。此处不应用 yoy 跳过逻辑。
    """
    if not text:
        return text, 0
    count = 0

    def _sub(pat: re.Pattern[str], repl: str, s: str) -> str:
        nonlocal count
        new_s, n = pat.subn(repl, s)
        count += n
        return new_s

    new = _sub(_RE_TWO_YEARS_AGO, f"{mapping.year_before_previous}年", text)
    new = _sub(_RE_PREVIOUS_YEAR, f"{mapping.previous_year}年", new)
    new = _sub(_RE_CURRENT_YEAR, f"{mapping.current_year}年", new)

    return new, count

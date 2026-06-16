"""定位 Markdown 中每个 <table> 的标题、单位、报告期年份。

对外提供：
    extract_year_from_md_path(md_path)          → int
    find_table_locations(md_text)               → List[TableLocation]

内部工具：
    extract_title_for_offset(md_text, offset)   → (title, level) | (None, None)
    extract_unit_for_offset(md_text, table_start, title_end_offset) → UnitInfo | None
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .exceptions import ReportYearInferenceError
from .models import TableLocation, UnitInfo
from .text_cleaner import _BOX_CHARS, clean_text

# 表格块：完整 <table>...</table>
_TABLE_BLOCK_RE = re.compile(
    r"<table\b[^>]*>.*?</table>",
    re.IGNORECASE | re.DOTALL,
)

# Markdown 标题（行首 1-6 个 # + 空白 + 标题文本）
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

# "单位：xxx" 行
_UNIT_LINE_RE = re.compile(r"^\s*单位\s*[:：]\s*(.+?)\s*$", re.MULTILINE)

# 标题前置序号清洗：1）/（1）/一、/(1) 等
_TITLE_PREFIX_RE = re.compile(
    r"^[（(]?\s*[一二三四五六七八九十百0-9]+\s*[）)、.]?\s*"
)

# 父目录名 4 位数字（1990~2099）
_YEAR_DIR_RE = re.compile(r"^(19|20)\d{2}$")

# 文件名兜底
_YEAR_IN_NAME_RE = re.compile(r"(19|20)\d{2}")


def extract_year_from_md_path(md_path: Path) -> int:
    """从 md 文件路径推断报告期年份。

    策略：
        1. 任一父目录名是 4 位数字（1990~2099）→ 取最近的
        2. 文件名 stem 含 (19|20)\\d{2} → 取第一个出现
        3. 都拿不到 → 抛 ReportYearInferenceError

    Args:
        md_path: 必须是 Path 对象。

    Returns:
        int: 报告期年份。

    Raises:
        ReportYearInferenceError: 无法推断。
    """
    candidates: List[str] = []

    # 优先级 1：父目录
    for ancestor in md_path.parents:
        name = ancestor.name
        candidates.append(name)
        if _YEAR_DIR_RE.match(name):
            return int(name)

    # 优先级 2：文件名 stem
    stem = md_path.stem
    candidates.append(stem)
    m = _YEAR_IN_NAME_RE.search(stem)
    if m:
        return int(m.group(0))

    raise ReportYearInferenceError(md_path, candidates[:8])


def find_table_locations(md_text: str) -> List[TableLocation]:
    """扫描 md 文本，定位每个 <table> 块及其上方的标题 / 单位。

    Args:
        md_text: 完整的 markdown 文本内容。

    Returns:
        List[TableLocation]: 顺序与文本中出现顺序一致。
    """
    if not md_text:
        return []

    locations: List[TableLocation] = []
    for m in _TABLE_BLOCK_RE.finditer(md_text):
        start = m.start()
        end = m.end()
        raw_html = m.group(0)

        title, level, title_end = _extract_title_with_end(md_text, start)
        unit = extract_unit_for_offset(md_text, start, title_end)
        preceding_text = _extract_preceding_text(md_text, start, title_end)

        locations.append(
            TableLocation(
                start=start,
                end=end,
                raw_html=raw_html,
                title=title,
                title_level=level,
                unit=unit,
                preceding_text=preceding_text,
            )
        )

    return locations


def extract_title_for_offset(
    md_text: str, offset: int
) -> Tuple[Optional[str], Optional[int]]:
    """从 0..offset 区间内找最后一个 Markdown 标题。

    Returns:
        (title, level) - 标题文本（剥前置序号）+ # 级别；都为 None 表示没找到。
    """
    title, level, _end = _extract_title_with_end(md_text, offset)
    return title, level


def _extract_title_with_end(
    md_text: str, offset: int
) -> Tuple[Optional[str], Optional[int], int]:
    """同 extract_title_for_offset，但额外返回标题匹配的结束偏移（用于切片）。"""
    last_match: Optional[re.Match[str]] = None
    for hm in _HEADING_RE.finditer(md_text, 0, offset):
        last_match = hm

    if last_match is None:
        return None, None, 0

    level = len(last_match.group(1))
    title = last_match.group(2).strip()
    title = _TITLE_PREFIX_RE.sub("", title).strip()
    title = clean_text(title)
    return (title or None), level, last_match.end()


def extract_unit_for_offset(
    md_text: str, table_start: int, title_end_offset: int
) -> Optional[UnitInfo]:
    """在 "上一个标题结束 ~ <table>" 之间找所有 "单位：xxx" 行。

    Args:
        md_text: 原 md 文本。
        table_start: 表格起始偏移。
        title_end_offset: 上一个标题结束偏移（无标题时为 0）。

    Returns:
        UnitInfo | None
    """
    if title_end_offset >= table_start:
        return None

    region = md_text[title_end_offset:table_start]
    # 先清洗方框字符避免影响"单位：" 行识别
    region_clean = _BOX_CHARS.sub("", region)

    raw_lines: List[str] = []
    for um in _UNIT_LINE_RE.finditer(region_clean):
        unit_text = clean_text(um.group(1))
        if unit_text:
            raw_lines.append(unit_text)

    if not raw_lines:
        return None

    return UnitInfo(
        raw_lines=raw_lines,
        primary=raw_lines[0],
        from_column_brackets={},
    )


def _extract_preceding_text(
    md_text: str, table_start: int, title_end_offset: int
) -> str:
    """提取 "上一个标题结束 ~ <table>" 之间的非空文字（去单位行后）。"""
    if title_end_offset >= table_start:
        return ""
    region = md_text[title_end_offset:table_start]
    # 去单位行 + 去空行
    lines = [
        ln.strip()
        for ln in region.splitlines()
        if ln.strip() and not _UNIT_LINE_RE.match(ln)
    ]
    return "\n".join(lines)

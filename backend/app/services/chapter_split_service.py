"""章节切分 service：把完整 markdown 按"第X节"切到
``{公司}/md/clean/{公司}{年份}年年报/by_section/``。

与 MinerUOnlineParser.split_by_sections 行为对齐（同样的正则、跳过目录、文件命名规则），
但**不依赖** minerU 类本身——上游已把 markdown 解析出来，本服务只做切分。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from app.config import get_settings


_SECTION_RE = re.compile(r"^#{1,2}\s+(第[一二三四五六七八九十百千]+节|目录)")
_CHINESE_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_ILLEGAL = r'\/:*?"<>|'


@dataclass
class ChapterFile:
    section_num: str
    title: str
    path: Path


def _chinese_to_int(s: str) -> int:
    if s == "十":
        return 10
    if len(s) == 2 and s[0] == "十":
        return 10 + _CHINESE_NUM.get(s[1], 1)
    if len(s) == 2 and s[1] == "十":
        return _CHINESE_NUM.get(s[0], 1) * 10
    return _CHINESE_NUM.get(s, 0)


def _extract_section_num(title: str) -> str:
    m = re.match(r"第([一二三四五六七八九十]+)节", title)
    if not m:
        return ""
    n = _chinese_to_int(m.group(1))
    return f"{n:02d}" if n else ""


def _safe_filename(name: str) -> str:
    for c in _ILLEGAL:
        name = name.replace(c, "_")
    return name[:50].strip()


def split_markdown_by_sections(
    md_text: str,
    company: str,
    year: int,
    output_root: Path | None = None,
) -> List[ChapterFile]:
    """把 markdown 按"第X节"切到 ``{公司}/md/clean/{公司}{年份}年年报/by_section/`` 下。

    output_root 默认取 ``settings.REPORT_DATA_PATH``。
    """
    if output_root is None:
        output_root = get_settings().REPORT_DATA_PATH
    out_dir = output_root / company / "md" / "clean" / f"{company}{year}年年报" / "by_section"
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = md_text.split("\n")
    sections: list[dict] = []
    current_title: str | None = None
    current_buf: list[str] = []
    skip = True  # 跳过第一节/目录之前的内容

    for line in lines:
        if _SECTION_RE.match(line):
            skip = False
            if current_title and current_buf:
                sections.append({"title": current_title, "content": "\n".join(current_buf)})
            current_title = re.sub(r"^#+\s+", "", line).strip()
            current_buf = [line]
        else:
            if skip or current_title is None:
                continue
            current_buf.append(line)
    if current_title and current_buf:
        sections.append({"title": current_title, "content": "\n".join(current_buf)})

    written: List[ChapterFile] = []
    counter = 0
    for sec in sections:
        title = sec["title"].strip()
        # 只跳过纯"目录"章节（不要误伤"第一节 重要提示、目录和释义"）
        if title == "目录" or title.startswith("目录 "):
            continue
        num = _extract_section_num(title)
        if num:
            filename = f"{num}_{_safe_filename(title)}.md"
        else:
            counter += 1
            filename = f"{counter:02d}_{_safe_filename(title)}.md"
        path = out_dir / filename
        path.write_text(sec["content"], encoding="utf-8")
        written.append(ChapterFile(section_num=num or f"{counter:02d}", title=title, path=path))

    return written

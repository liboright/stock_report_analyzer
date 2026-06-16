"""MinerU 输出后处理器。

从 `D:/quant/report_gen/report_generator/parser/mineru_processor.py` 复制。
相对 import 路径保持子包内一致（`.exceptions` / `.heading_level_converter`）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from .heading_level_converter import HeadingLevelConverter


class MinerUProcessor:
    """MinerU 输出处理器 —— Markdown 后处理（标题归一 / 章节拆分 / 清理）。"""

    def __init__(self, md_content: str, output_dir: Path, pdf_name: Optional[str] = None):
        self.md_content = md_content
        self.output_dir = Path(output_dir)
        self.pdf_name = pdf_name or "output"

    def process(
        self,
        convert_headings: bool = True,
        split_sections: bool = False,
        cleanup: bool = True,
    ) -> str:
        result = self.md_content

        if convert_headings:
            converter = HeadingLevelConverter()
            result = converter.convert(result)

        # 临时保存转换后的 markdown（cleanup=True 时会被删）
        self.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_md = self.output_dir / f"{self.pdf_name}.md"
        with open(tmp_md, "w", encoding="utf-8") as f:
            f.write(result)

        if split_sections:
            self.md_content = result
            self.split_by_sections()

        if cleanup:
            self._cleanup_output_dir()
            for f in [self.output_dir / "full.md", self.output_dir / f"{self.pdf_name}.md"]:
                if f.exists():
                    f.unlink()

        return result

    def convert_headings_only(self) -> str:
        return HeadingLevelConverter().convert(self.md_content)

    def split_by_sections(self, output_dir: Optional[str] = None) -> List[str]:
        split_dir = Path(output_dir) if output_dir else self.output_dir
        split_dir.mkdir(parents=True, exist_ok=True)

        sections = self._extract_sections()
        files: List[str] = []
        section_counter = 0
        for section in sections:
            title = section["title"]
            safe = self._sanitize_filename(title)
            if title.strip() == "目录":
                continue
            num = self._extract_section_number(title)
            filename = f"{num}_{safe}.md" if num else f"{section_counter:02d}_{safe}.md"
            section_counter += 1
            content = self._update_image_references(section["content"])
            p = split_dir / filename
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            files.append(str(p))
        return files

    # ---------- 私有辅助 ----------

    def _extract_sections(self) -> List[dict]:
        lines = self.md_content.split("\n")
        pattern = re.compile(r"^#{1,2}\s+(第[一二三四五六七八九十百千]+节|目录)")
        sections: List[dict] = []
        cur_title: Optional[str] = None
        cur_content: List[str] = []
        skip = True
        for line in lines:
            if pattern.match(line):
                if cur_title and cur_content:
                    sections.append({"title": cur_title, "content": "\n".join(cur_content)})
                cur_title = re.sub(r"^#+\s+", "", line).strip()
                cur_content = [line]
                skip = False
            else:
                if skip:
                    continue
                if cur_title is None:
                    continue
                cur_content.append(line)
        if cur_title and cur_content:
            sections.append({"title": cur_title, "content": "\n".join(cur_content)})
        return sections

    def _extract_section_number(self, title: str) -> Optional[str]:
        m = re.match(r"第([一二三四五六七八九十]+)节", title)
        if not m:
            return None
        cn = m.group(1)
        cn_map = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                  "六": "6", "七": "7", "八": "8", "九": "9"}
        if cn == "十":
            return "10"
        if len(cn) == 2 and cn[0] == "十":
            return "1" + cn_map.get(cn[1], cn[1])
        if len(cn) == 2 and cn[1] == "十":
            return cn_map.get(cn[0], cn[0]) + "0"
        return cn_map.get(cn, cn)

    def _sanitize_filename(self, title: str) -> str:
        safe = re.sub(r'[\\/:*?"<>|]', "", title)
        return safe[:50] if len(safe) > 50 else safe

    def _update_image_references(self, content: str) -> str:
        pattern = r'!\[?\]\((?:MinerU\.md/)?images/([^)]+)\)'
        return re.sub(pattern, lambda m: f"![](images/{m.group(1)})", content)

    def _cleanup_output_dir(self) -> None:
        if not self.output_dir:
            return
        for name in ("content_list_v2.json", "layout.json", "mineru_result.zip"):
            p = self.output_dir / name
            if p.exists():
                p.unlink()
        for pat in ("*_content_list.json", "*_model.json", "*_origin.pdf"):
            for f in self.output_dir.glob(pat):
                f.unlink()


def process_mineru_md(
    md_path: str,
    output_dir: Optional[str] = None,
    convert_headings: bool = True,
    split_sections: bool = False,
    cleanup: bool = True,
) -> str:
    md_path = Path(md_path)
    md_content = md_path.read_text(encoding="utf-8")
    if output_dir is None:
        output_dir = md_path.parent
    return MinerUProcessor(md_content, Path(output_dir), md_path.stem).process(
        convert_headings, split_sections, cleanup
    )

"""MinerU 在线 API 解析器（高层入口）。

从 `D:/quant/report_gen/report_generator/parser/mineru_online_parser.py` 复制。
改动点：`__init__` 接受 `api_key` / `api_base` 并向下传给 `MinerUAPIClient`，
不再依赖 `mineru_api_client` 模块顶部的硬编码常量。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from .exceptions import PDFParserError
from .mineru_api_client import MinerUAPIClient
from .mineru_processor import MinerUProcessor


_DEFAULT_API_BASE = "https://mineru.net/api"


class MinerUOnlineParser:
    """基于 MinerU 在线 API 的 PDF 解析器（高层入口）。"""

    def __init__(
        self,
        pdf_path: str,
        api_key: str,
        api_base: str = _DEFAULT_API_BASE,
    ):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise PDFParserError(f"PDF 文件不存在: {pdf_path}")

        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.output_dir: Optional[Path] = None
        self.markdown_content: Optional[str] = None
        self.batch_id: Optional[str] = None
        self.file_url: Optional[str] = None

    def parse(
        self,
        output_dir: Optional[str] = None,
        convert_headings: bool = True,
        cleanup: bool = True,
    ) -> str:
        """执行解析，返回处理后的 Markdown 内容。"""
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = self.pdf_path.parent / "mineru_output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 步骤 1：API 转换（返回未归一的 markdown）
            api_client = MinerUAPIClient(
                str(self.pdf_path),
                api_key=self.api_key,
                api_base=self.api_base,
            )
            self.markdown_content = api_client.convert(str(self.output_dir))
            if api_client.output_dir:
                self.output_dir = api_client.output_dir

            # 步骤 2：标题归一 + 清理
            processor = MinerUProcessor(
                self.markdown_content,
                self.output_dir,
                self.pdf_path.stem,
            )
            self.markdown_content = processor.process(
                convert_headings=convert_headings,
                split_sections=False,
                cleanup=cleanup,
            )
        except PDFParserError:
            raise
        except Exception as e:
            raise PDFParserError(f"MinerU 在线 API 解析失败: {e}") from e

        return self.markdown_content

    def save_markdown(self, output_path: str) -> str:
        if not self.markdown_content:
            self.parse()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self.markdown_content)
        return str(output_path)

    def get_output_dir(self) -> Optional[Path]:
        return self.output_dir

    # ---------- 章节拆分（项目内暂未用，保留以便未来） ----------

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

    def split_by_sections(
        self,
        output_dir: Optional[str] = None,
        copy_images: bool = True,
        update_image_paths: bool = True,
    ) -> List[str]:
        if not self.markdown_content:
            self.parse()

        if output_dir:
            split_dir = Path(output_dir)
        else:
            split_dir = self.output_dir.parent / f"{self.pdf_path.stem}_sections"
        split_dir.mkdir(parents=True, exist_ok=True)

        if copy_images:
            source_images_dir = None
            for cand in (self.output_dir / "images", self.output_dir / "MinerU.md" / "images"):
                if cand.exists() and cand.is_dir() and any(cand.iterdir()):
                    source_images_dir = cand
                    break
            if source_images_dir:
                target = split_dir / "images"
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(source_images_dir, target)

        sections = self._extract_sections()
        files: List[str] = []
        section_counter = 0
        for section in sections:
            title = section["title"]
            safe = self._sanitize_filename(title)
            if "目录" in title:
                continue
            num = self._extract_section_number(title)
            filename = f"{num}_{safe}.md" if num else f"{section_counter:02d}_{safe}.md"
            section_counter += 1
            content = section["content"]
            if update_image_paths:
                content = self._update_image_references(content)
            p = split_dir / filename
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            files.append(str(p))
        return files

    def _extract_sections(self) -> List[Dict[str, str]]:
        lines = self.markdown_content.split("\n")
        pattern = re.compile(r"^#{1,2}\s+(第[一二三四五六七八九十百千]+节|目录)")
        sections: List[Dict[str, str]] = []
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


def parse_mineru_online(
    pdf_path: str,
    api_key: str,
    api_base: str = _DEFAULT_API_BASE,
    output_dir: Optional[str] = None,
) -> str:
    """便捷函数：直接调一次 parse。"""
    parser = MinerUOnlineParser(pdf_path, api_key=api_key, api_base=api_base)
    return parser.parse(output_dir)

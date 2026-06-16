"""年报 PDF 按章节切分：最后一节（财务报告）单独成文。

核心：基于 PyMuPDF 字体/字号 + 位置 + 居中 + 短行 + 目录参考，
多信号综合识别一级章节标题。
不依赖 PDF outline，不依赖 TOC 文本格式。

2026/06 实施：解决 MinerU API 200 页限制（年报多在 200+ 页）。
2026/06 修订：增加"视觉同行 line 合并"，适配茅台 2025 等
将"第八节 财务报告"在 PyMuPDF 中拆为同一 y 坐标的两个独立 line 的版式。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf

from app.config import get_settings
from app.db import session as db_session
from app.models.annual_report import AnnualReport

# ---- 文本模式 ----
# 章节标题文本（如"第八节 财务报告" / "第十节 财务报告"）。
# 容许"第X节"与"财务报告"之间无空白（视觉同行 line 合并后无空格）。
SECTION_RE = re.compile(r"第[一二三四五六七八九十百]+节\s*财务报告|第[一二三四五六七八九十百]+节财务报告")
# 目录页标识：单行"目 录" / "目 录" / "目录"
TOC_MARKER_RE = re.compile(r"^\s*目\s*录\s*$", re.MULTILINE)
# 目录里的章节行（带页码），如"第八节 财务报告 ........ 107"
SECTION_TOC_RE = re.compile(
    r"第[一二三四五六七八九十百]+节\s*财务报告[\s.\u3000]+(\d+)"
)

# ---- 结构特征阈值（不硬限字号，用"top N"自适应） ----
CENTER_TOLERANCE = 0.20   # 行中心到页中心的距离 / 页宽
TOP_FRACTION = 0.30       # 标题应在页面上 30% 内
TOP_N_SIZES = 3           # 字号相对 top 3（自适应，不用绝对字号）
MAX_TITLE_CHARS = 30      # 标题行字符数上限（独立成行）
MERGE_Y_TOLERANCE = 2.0   # 视觉同行 y 差 ≤ 此值才合并
MERGE_SIZE_TOLERANCE = 0.5  # 视觉同行 字号差 ≤ 此值才合并

SPLIT_SUBDIR = "split"    # 在 pdf/ 下


@dataclass
class SplitResult:
    finance_pdf_path: Path
    other_pdf_path: Path
    finance_start_page: int  # 0-based
    total_pages: int
    title_text: str


def _is_centered(line_bbox, page_width: float) -> bool:
    """判断行是否水平居中（章节标题 vs 左对齐正文）。"""
    line_center = (line_bbox[0] + line_bbox[2]) / 2
    page_center = page_width / 2
    return abs(line_center - page_center) < page_width * CENTER_TOLERANCE


def _page_top_sizes(page, top_n: int) -> list[float]:
    """返回该页所有 span 字号中最大的 top_n 个（用于自适应阈值）。"""
    sizes: list[float] = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                sizes.append(span["size"])
    sizes.sort(reverse=True)
    return sizes[:top_n]


def _collect_visual_lines(page) -> list[dict]:
    """收集一页的所有"视觉同行"：把 PyMuPDF 拆开的同基线 line 合并为虚拟行。

    返回 list[dict]，每项含：text / y0 / bbox / max_size / centered。

    合并规则：按 y0 排序后，y0 差 ≤ ``MERGE_Y_TOLERANCE``、字号差
    ≤ ``MERGE_SIZE_TOLERANCE``、居中状态一致 → 合并为一组（同一视觉行）。
    适配茅台 2025 等"第八节"和"财务报告"在 PyMuPDF 中被拆为两个
    line 对象但视觉上同一行的版式。
    """
    page_width = page.rect.width
    raw: list[dict] = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            if not text:
                continue
            bb = line.get("bbox", [0, 0, 0, 0])
            max_size = max(s["size"] for s in spans)
            raw.append({
                "text": text,
                "y0": bb[1],
                "bbox": bb,
                "max_size": max_size,
                "centered": _is_centered(bb, page_width),
            })
    raw.sort(key=lambda r: (r["y0"], r["bbox"][0]))

    merged: list[dict] = []
    for r in raw:
        if merged and (
            abs(merged[-1]["y0"] - r["y0"]) <= MERGE_Y_TOLERANCE
            and abs(merged[-1]["max_size"] - r["max_size"]) <= MERGE_SIZE_TOLERANCE
            and merged[-1]["centered"] == r["centered"]
        ):
            # 同一视觉行：拼接文本（无分隔符，依赖 SECTION_RE 容许无空格变体）
            last = merged[-1]
            last["text"] = last["text"] + r["text"]
            last["bbox"] = [
                min(last["bbox"][0], r["bbox"][0]),
                min(last["bbox"][1], r["bbox"][1]),
                max(last["bbox"][2], r["bbox"][2]),
                max(last["bbox"][3], r["bbox"][3]),
            ]
            last["max_size"] = max(last["max_size"], r["max_size"])
        else:
            merged.append(dict(r))
    return merged


def _toc_page(doc, max_scan: int = 5) -> int | None:
    """在前 max_scan 页内找包含"目 录"标识的页。"""
    for i in range(min(max_scan, len(doc))):
        if TOC_MARKER_RE.search(doc[i].get_text()):
            return i
    return None


def _toc_page_hint(doc) -> int | None:
    """参考信号：从目录页提取'第X节 财务报告'对应的页码（→ 0-based）。"""
    idx = _toc_page(doc, max_scan=5)
    if idx is None:
        return None
    m = SECTION_TOC_RE.search(doc[idx].get_text())
    return (int(m.group(1)) - 1) if m else None


def find_section_start_page(doc) -> tuple[int, str]:
    """综合多信号定位'第X节 财务报告'一级标题。

    必选 5 信号：① 文本匹配 ② 居中 ③ 字号 top N ④ 短行 ⑤ 页顶
    参考信号：目录页码（仅用于候选打分时的距离加权，不直接定位）

    Returns: (page_idx 0-based, title_text)
    Raises: ValueError 当无候选时
    """
    toc_hint = _toc_page_hint(doc)  # 仅参考，None 也 OK
    candidates: list[tuple[int, str, float]] = []

    for i, page in enumerate(doc):
        top_limit = page.rect.height * TOP_FRACTION
        top_sizes = _page_top_sizes(page, TOP_N_SIZES)
        if not top_sizes:
            continue
        size_threshold = top_sizes[-1]  # top N 中最小字号（自适应）

        for vline in _collect_visual_lines(page):
            line_text = vline["text"]
            line_bbox = vline["bbox"]
            max_size = vline["max_size"]

            # ---- 5 个必选信号 ----
            if not SECTION_RE.search(line_text):       # ① 文本
                continue
            if line_bbox[1] > top_limit:                # ② 页顶
                continue
            if max_size < size_threshold:               # ③ 字号 top N
                continue
            if len(line_text) > MAX_TITLE_CHARS:        # ④ 独立成行
                continue
            if not vline["centered"]:                   # ⑤ 居中
                continue

            # 评分：字号为主，距离目录页码为辅
            score = max_size
            if toc_hint is not None:
                score -= abs(i - toc_hint) * 0.01
            candidates.append((i, line_text, score))

    if not candidates:
        raise ValueError(
            "PDF 中未找到匹配的'第X节 财务报告'一级标题"
            f"（需同时满足：居中、字号 top {TOP_N_SIZES}、短行 ≤ {MAX_TITLE_CHARS} 字符、页顶 {TOP_FRACTION}）"
        )
    # 多候选时取 score 最高的；score 相同取 page 较大的（避免误命中目录）
    candidates.sort(key=lambda c: (-c[2], -c[0]))
    return candidates[0][0], candidates[0][1]


def _resolve_orig_pdf(rel_path: str) -> Path:
    """解析原 PDF 的实际位置。

    仅 ``REPORT_DATA_PATH / rel`` 一处（按 ``docs/artifacts.md`` 统一单棵树）。
    """
    settings = get_settings()
    new = settings.REPORT_DATA_PATH / rel_path
    if new.exists():
        return new
    raise FileNotFoundError(f"原 PDF 不存在: {rel_path} ({new})")


def split_annual_report_pdf(ar: AnnualReport, company_name: str) -> SplitResult:
    """切 AnnualReport 对应的 PDF，更新 DB 字段。同步执行。

    输入：
      - ``REPORT_DATA_PATH / ar.pdf_path``

    输出（按 ``docs/artifacts.md`` §1 规范）：
      - ``REPORT_DATA_PATH/{公司}/pdf/split/{原 PDF stem}_财务报告.pdf`` ← [start, total)
      - ``REPORT_DATA_PATH/{公司}/pdf/split/{原 PDF stem}_业务报告.pdf`` ← [0, start)

    DB 路径字段也按 ``REPORT_DATA_PATH`` 算相对路径（无 ``raw/`` 前缀）。
    """
    settings = get_settings()
    pdf_path = _resolve_orig_pdf(ar.pdf_path)

    # 输出用新 base（docs/artifacts.md §2.1）
    out_dir = settings.REPORT_DATA_PATH / company_name / "pdf" / SPLIT_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = pdf_path.stem
    finance_path = out_dir / f"{stem}_财务报告.pdf"
    other_path = out_dir / f"{stem}_业务报告.pdf"

    doc = fitz.open(str(pdf_path))
    start, title = find_section_start_page(doc)
    total = len(doc)

    # 财务报告部分
    finance_doc = fitz.open()
    finance_doc.insert_pdf(doc, from_page=start, to_page=total - 1)
    finance_doc.save(str(finance_path), garbage=4, deflate=True)
    finance_doc.close()

    # 其他章节
    other_doc = fitz.open()
    other_doc.insert_pdf(doc, from_page=0, to_page=start - 1)
    other_doc.save(str(other_path), garbage=4, deflate=True)
    other_doc.close()
    doc.close()

    # 写 DB：相对路径按新 base 算（无 raw/ 前缀）
    db = db_session.SessionLocal()
    try:
        base = settings.REPORT_DATA_PATH.resolve()
        rel_finance = str(finance_path.resolve().relative_to(base)).replace("\\", "/")
        rel_other = str(other_path.resolve().relative_to(base)).replace("\\", "/")
        ar2 = db.query(AnnualReport).filter(AnnualReport.id == ar.id).first()
        ar2.split_status = "done"
        ar2.finance_pdf_path = rel_finance
        ar2.other_pdf_path = rel_other
        db.commit()
    finally:
        db.close()

    return SplitResult(finance_path, other_path, start, total, title)

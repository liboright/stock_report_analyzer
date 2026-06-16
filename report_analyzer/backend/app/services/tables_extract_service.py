"""阶段 2.5 表格抽取 → CSV 落盘 service。

职责：
- 遍历 `md/clean/{公司}{年份}年年报/管理层讨论/*.md`（阶段 2.4 产物）
- 调 `md_table_parser.extract_tables_from_md_text` 拿 List[TableInfo]（**直接传 year** 绕过年份推断）
- 每张表 → `table/{源 md stem}/{清理后的表标题}.csv`（一表一文件）
- 不修改 `md_table_parser` 子包

路径规范严格遵守 `docs/artifacts.md §1/§3.2/§6`。
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import Settings
from app.services.md_table_parser import (
    TableInfo,
    extract_tables_from_md_text,
)

_log = logging.getLogger(__name__)

# 元数据头与正文之间的分隔标记
_HEADER_END_MARKER = "# ============== END HEADER =============="


# ============================================================
# 异常
# ============================================================


class TablesExtractError(Exception):
    """阶段 2.5 表格抽取 service 错误基类。"""


class CompanyNotFoundError(TablesExtractError):
    def __init__(self, company: str):
        super().__init__(f"公司不存在: {company}")
        self.company = company


class MdSectionNotFoundError(TablesExtractError):
    """管理层讨论目录不存在（阶段 2.4 产物缺失）。"""
    def __init__(self, path: Path):
        super().__init__(f"管理层讨论目录不存在（请先跑章节切分）: {path}")
        self.path = path


# ============================================================
# 路径工具
# ============================================================


def clean_section_dir(company: str, year: int, settings: Settings) -> Path:
    """输入目录：`{公司}/md/clean/{公司}{年份}年年报/管理层讨论/`"""
    return (
        settings.REPORT_DATA_PATH
        / company
        / "md"
        / "clean"
        / f"{company}{year}年年报"
        / "管理层讨论"
    )


def table_output_dir(company: str, year: int, settings: Settings) -> Path:
    """输出目录：`{公司}/md/clean/{公司}{年份}年年报/table/`"""
    return (
        settings.REPORT_DATA_PATH
        / company
        / "md"
        / "clean"
        / f"{company}{year}年年报"
        / "table"
    )


def table_dir_rel(company: str, year: int) -> str:
    """DB 存的相对 REPORT_DATA_PATH 的 POSIX 路径。"""
    return f"{company}/md/clean/{company}{year}年年报/table"


# ============================================================
# 标题清理
# ============================================================


_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')  # Windows 文件名非法字符（不含 \n\r\t，交给空白折叠）
_WHITESPACE = re.compile(r"\s+")
# 表标题前残留的列表编号符：(1). / (一). / 1、/ 一、 等，开头的标点/数字段
_LEADING_NUMBERING = re.compile(
    r"^[\s\.、，,。\-—_]*"
    r"(?:\([^)]{1,6}\)|（[^）]{1,6}）|\[[^\]]{1,6}\]|[0-9]{1,3})"
    r"[\s\.、，,。\-—_]*"
)


def sanitize_title(title: str, max_len: int = 60) -> str:
    """清理标题为可作文件名的 stem：去前缀编号、去非法字符、折叠空白、截断。"""
    s = title or ""
    # 反复剥离编号前缀（如 "(1).研发投入" → "研发投入"；".研发人员情况表" → "研发人员情况表"）
    while True:
        new = _LEADING_NUMBERING.sub("", s).lstrip(" .、，,。-—_")
        if new == s:
            break
        s = new
    s = _ILLEGAL_CHARS.sub("", s)
    s = _WHITESPACE.sub(" ", s).strip()
    s = s[:max_len].rstrip()
    return s or "未命名表"


def dedup_path(dir_: Path, stem: str) -> Path:
    """同目录下同名 stem 自动加 _{2} _{3} 后缀。"""
    candidate = dir_ / f"{stem}.csv"
    if not candidate.exists():
        return candidate
    for i in range(2, 1000):
        candidate = dir_ / f"{stem}_{i}.csv"
        if not candidate.exists():
            return candidate
    raise TablesExtractError(f"重名去重超过上限 999: {stem}")


# ============================================================
# CSV 写盘
# ============================================================


def _build_header_block(
    t: TableInfo,
    source_md_rel: str,
    table_seq: int,
    total_in_md: int,
) -> str:
    """构造元数据头（多行 # key, value）。"""
    unit = t.unit.primary if t.unit else ""
    ym = t.year_mapping
    ym_str = (
        f"current={ym.current_year},previous={ym.previous_year},ybp={ym.year_before_previous}"
        if ym is not None
        else ""
    )
    lines = [
        f"# source_md, {source_md_rel}",
        f"# table_seq, {table_seq}/{total_in_md}",
        f"# table_index, {t.table_index}",
        f"# report_year, {t.report_year}",
        f"# title, {t.title or ''}",
        f"# title_level, {t.title_level if t.title_level is not None else ''}",
        f"# unit, {unit}",
        f"# year_mapping, {ym_str}",
        f"# row_count, {t.row_count}",
        f"# col_count, {t.col_count}",
        f"# extracted_at, {datetime.now().isoformat(timespec='seconds')}",
        _HEADER_END_MARKER,
        "",  # 末尾空行
    ]
    return "\n".join(lines)


def _flatten_headers(headers: List[List[str]]) -> List[str]:
    """多级表头 → 单行：相邻层用 " | " 拼接。"""
    if not headers:
        return []
    cols = max(len(row) for row in headers)
    out: List[str] = []
    for c in range(cols):
        parts = []
        for row in headers:
            if c < len(row):
                v = row[c]
                if v:
                    parts.append(v)
        out.append(" | ".join(parts))
    return out


def _format_table_block(t: TableInfo) -> str:
    """把 headers + data_grid 用 csv 库序列化为字符串。"""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")

    flat_headers = _flatten_headers(t.headers)
    if flat_headers:
        # 用 None 补齐到 col_count（理论上 headers 已对齐，兜底）
        while len(flat_headers) < t.col_count:
            flat_headers.append("")
        writer.writerow(flat_headers)

    for row in t.data_grid:
        # row 已经是 List[str]，None 已转 ""
        padded = list(row) + [""] * (t.col_count - len(row)) if len(row) < t.col_count else list(row[:t.col_count])
        writer.writerow(padded)

    return buf.getvalue()


def write_table_csv(
    path: Path,
    t: TableInfo,
    source_md_rel: str,
    table_seq: int,
    total_in_md: int,
) -> None:
    """追加一张表到 CSV：元数据头 + 空行 + headers + data + 末尾空行。

    8 类固定文件名 → 多次追加。`其他` 目录每张表一个独立文件（标题不同时）。
    """
    header_block = _build_header_block(t, source_md_rel, table_seq, total_in_md)
    body = _format_table_block(t)
    new_content = header_block + body + "\n"  # 末尾再补 1 空行分隔下一块

    # BOM 处理：新建时用 utf-8-sig（Excel 友好）；追加时用 utf-8（避免重复 BOM）
    if path.exists():
        with path.open(mode="a", encoding="utf-8", newline="") as f:
            f.write(new_content)
    else:
        with path.open(mode="w", encoding="utf-8-sig", newline="") as f:
            f.write(new_content)


# ============================================================
# Result DTO
# ============================================================


@dataclass
class ExtractOutcome:
    company: str
    year: int
    total: int
    sections: Dict[str, int] = field(default_factory=dict)  # 源 md stem → 张数
    csv_paths: List[str] = field(default_factory=list)  # 相对 REPORT_DATA_PATH 的 POSIX
    duration_ms: int = 0
    status: str = "done"  # 'done' / 'empty' / 'failed'


# ============================================================
# 主入口
# ============================================================


def extract_tables_to_csv(
    settings: Settings,
    company: str,
    year: int,
) -> ExtractOutcome:
    """遍历管理层讨论/*.md → 每张表独立 CSV 落盘（按源 md 分子目录） → 返回汇总。

    行为约定：
    - 公司不在 settings 范围内检查（DB 校验由 router 层做；本函数不依赖 DB）
    - 管理层讨论目录不存在 → 抛 `MdSectionNotFoundError`（由 router 转 404）
    - 目录存在但无 md → 返回 `status='empty', total=0`（不抛）
    - 输出结构：`table/{源 md stem}/{清理后标题}.csv`；同子目录内重名加 _2 _3
    """
    started = datetime.now()
    in_dir = clean_section_dir(company, year, settings)
    out_dir = table_output_dir(company, year, settings)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_dir.exists():
        raise MdSectionNotFoundError(in_dir)

    md_files = sorted(in_dir.glob("*.md"))
    if not md_files:
        _log.info("管理层讨论目录为空: %s", in_dir)
        return ExtractOutcome(
            company=company,
            year=year,
            total=0,
            duration_ms=int((datetime.now() - started).total_seconds() * 1000),
            status="empty",
        )

    outcome = ExtractOutcome(company=company, year=year, total=0)
    base = settings.REPORT_DATA_PATH

    for md in md_files:
        try:
            md_text = md.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            _log.warning("读 md 失败: %s (%s)", md, exc)
            continue
        try:
            tables = extract_tables_from_md_text(
                md_text=md_text,
                report_year=year,
                source_path=md,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("解析失败: %s (%s)", md, exc)
            continue

        if not tables:
            continue

        total_in_md = len(tables)
        source_md_rel = str(md.relative_to(base)).replace("\\", "/")
        # 子目录名 = 源 md stem（已是合法文件名，仅过滤 Windows 非法字符兜底）
        sub_dir = out_dir / _ILLEGAL_CHARS.sub("", md.stem)
        sub_dir.mkdir(parents=True, exist_ok=True)

        for seq, t in enumerate(tables, start=1):
            stem = sanitize_title(t.title or f"table_{t.table_index}")
            csv_path = dedup_path(sub_dir, stem)
            write_table_csv(csv_path, t, source_md_rel, seq, total_in_md)
            outcome.sections[md.stem] = outcome.sections.get(md.stem, 0) + 1
            outcome.csv_paths.append(
                str(csv_path.relative_to(base)).replace("\\", "/")
            )
            outcome.total += 1

    outcome.csv_paths.sort()
    outcome.duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    _log.info(
        "表格抽取完成: company=%s year=%d total=%d sections=%s duration_ms=%d",
        company,
        year,
        outcome.total,
        outcome.sections,
        outcome.duration_ms,
    )
    return outcome

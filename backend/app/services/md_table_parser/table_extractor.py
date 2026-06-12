"""装配层：把 ParsedTable + TableLocation + YearMapping → TableInfo。

依赖外部 `D:/quant/deep-research-report/shared/tools/table_parser.py`
（已通过 app/config.py::inject_external_paths 注入 sys.path，可直接 import）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

from .exceptions import MdTableError
from .models import (
    HeaderColumn,
    TableInfo,
    TableLocation,
    UnitInfo,
    YearMapping,
)
from .text_cleaner import clean_cell, clean_text, merge_continuation_rows, row_looks_like_header
from .title_unit_locator import find_table_locations
from .year_normalizer import (
    _RE_CURRENT_YEAR,
    _RE_PREVIOUS_YEAR,
    _RE_TWO_YEARS_AGO,
    build_year_mapping,
    detect_yoy_column,
    extract_explicit_year,
    normalize_year_in_cell,
    normalize_year_in_text,
)

_log = logging.getLogger(__name__)

# 列名末尾括号单位："销售额（千元）" / "销售额(千元)" / "产能（GWh）"
_HEADER_PAREN_UNIT_RE = re.compile(
    r"[（(]\s*([%a-zA-Z\u4e00-\u9fa5/.]{1,12})\s*[)）]\s*$"
)


def _cell_to_str(cell) -> str:
    """把单元格值归一为字符串（None/非 str 都安全转换）。"""
    if cell is None:
        return ""
    return str(cell).strip()

# 项目列首关键词
_ITEM_FIRST_KEYWORDS = (
    "项目",
    "客户",
    "供应商",
    "序号",
    "产品种类",
    "时间",
    "接待",
    "公司名称",
    "合同标的",
    "行业分类",
    "主要研发项目名称",
    "对方当事人",
)


def _import_external_table_parser():
    """惰性导入外部 table_parser（避免模块导入时副作用）。

    app/config.py::inject_external_paths() 当前只把 DEEP_RESEARCH_PATH 顶层
    加入 sys.path；table_parser.py 实际位于 shared/tools/ 子目录，因此
    直接 `import table_parser` 找不到。此处按需把 shared/tools/ 路径补进
    sys.path，让该子包在任何调用上下文（FastAPI lifespan / pytest / 脚本）
    中都能自洽工作。
    """
    try:
        import table_parser  # type: ignore[import-not-found]
    except ImportError:
        import sys as _sys
        from pathlib import Path as _Path

        candidate = _Path("D:/quant/deep-research-report/shared/tools")
        if candidate.is_dir():
            sp = str(candidate)
            if sp not in _sys.path:
                _sys.path.insert(0, sp)
            try:
                import table_parser  # type: ignore[import-not-found]
                return table_parser
            except ImportError as exc:
                raise MdTableError(
                    "无法 import 外部 table_parser（已尝试注入 shared/tools 路径仍失败）。"
                    f" 原始错误: {exc}"
                ) from exc
        raise MdTableError(
            "无法 import 外部 table_parser。请确认 "
            "D:/quant/deep-research-report/shared/tools 存在，或手工把该路径加入 sys.path。"
        )
    return table_parser


def _flatten_headers(
    parsed_headers_rows: List[List[str]],
    grid_header_rows: List[List[Optional[str]]],
    col_count: int,
) -> List[str]:
    """把多级表头行合并为每列一条字符串。

    规则：
    - 优先使用 grid_header_rows（已经 rowspan/colspan 展开、对齐 col_count）
    - 按列竖向拼接每层非空文本，去重，用 " / " 连接
    - 若该列所有行都是空，回退到 parsed_headers_rows 中对应位置
    """
    if not grid_header_rows or col_count == 0:
        if parsed_headers_rows:
            return [
                " / ".join(s for s in (parsed_headers_rows[0] if parsed_headers_rows else []))
                for _ in range(col_count)
            ]
        return [""] * col_count

    flat: List[str] = []
    for col_idx in range(col_count):
        parts: List[str] = []
        seen: set[str] = set()
        for row in grid_header_rows:
            if col_idx >= len(row):
                continue
            cell = clean_cell(row[col_idx])
            if cell and cell not in seen:
                parts.append(cell)
                seen.add(cell)
        flat.append(" / ".join(parts))
    return flat


def _infer_column_role(
    header_text: str,
    col_index: int,
    flat_headers: List[str],
) -> str:
    """推断列角色：item / year / yoy / amount / ratio / other。"""
    if not header_text:
        # 空表头但是第一列，按 item 处理
        return "item" if col_index == 0 else "other"

    if detect_yoy_column(header_text):
        return "yoy"

    if any(kw in header_text for kw in _ITEM_FIRST_KEYWORDS) and col_index == 0:
        return "item"

    if extract_explicit_year(header_text) is not None:
        return "year"

    if (
        _RE_CURRENT_YEAR.search(header_text)
        or _RE_PREVIOUS_YEAR.search(header_text)
        or _RE_TWO_YEARS_AGO.search(header_text)
    ):
        return "year"

    if "占" in header_text or "比重" in header_text or "比例" in header_text or "占比" in header_text:
        return "ratio"

    if "金额" in header_text:
        return "amount"

    if col_index == 0:
        return "item"

    return "other"


def _split_headers_and_data(
    grid: List[List[Optional[str]]],
    n_header_rows: int,
) -> tuple[List[List[Optional[str]]], List[List[Optional[str]]]]:
    """把 grid 切分为表头行和数据行。"""
    if n_header_rows <= 0:
        return [], grid
    return grid[:n_header_rows], grid[n_header_rows:]


def convert_parsed_table(
    parsed,
    location: TableLocation,
    year_mapping: YearMapping,
    source_path: Path,
    table_index: int,
) -> TableInfo:
    """把 ParsedTable 转为 TableInfo。

    Args:
        parsed: 外部 table_parser.ParsedTable
        location: 已含 title / unit / preceding_text
        year_mapping: 年份映射
        source_path: 源 md 文件路径
        table_index: 在该 md 中第几个 <table>（0-based）
    """
    col_count = int(parsed.col_count or 0)

    # ============================================================
    # Header 校验：外部 table_parser._is_header_row 启发式有多个 bug：
    # 1) 漏检：首列不在 "项目/客户/供应商/序号/产品种类/时间/接待" 白名单
    #    且无 "\d{4}年" 时，header 被误判为 data → headers 为空。
    # 2) 过检：首列含上述任一关键词就视为 header，且会把该行同时塞进
    #    headers 和 data_grid（重复）。典型 case：
    #    - "前五名客户合计销售金额 | 165,061,533"：含 "客户" → header，
    #      但第二列是数字，整行不像 header。
    #    - "客户A | 对方公司 | 54,173,399"：含 "客户" → header，
    #      同时这行也出现在 data_grid（与 headers 完全重复）。
    # 策略：
    # a) dedup：剔除 parsed.headers 中与 parsed.data_grid 重复的行
    # b) 反证：剩余 parsed.headers 的第一行若不像 header → 全部降级为 data
    # c) 兜底：headers 仍为空时，看 data_grid[0] 是否像 header
    # ============================================================
    parsed_headers = [list(r) for r in (parsed.headers or [])]
    parsed_data = [list(r) for r in (parsed.data_grid or [])]

    # a) dedup
    if parsed_headers and parsed_data:
        data_key_set = {
            tuple(_cell_to_str(c) for c in r) for r in parsed_data
        }
        parsed_headers = [
            r for r in parsed_headers
            if tuple(_cell_to_str(c) for c in r) not in data_key_set
        ]

    # b) 反证：剩余 headers 的第一行若不像 header → 全部降级
    if parsed_headers and not row_looks_like_header(
        [_cell_to_str(c) for c in parsed_headers[0]]
    ):
        # 全部降级为 data（data_grid 可能没有这些行，也可能已有，去重合并）
        existing_data_keys = {
            tuple(_cell_to_str(c) for c in r) for r in parsed_data
        }
        for r in parsed_headers:
            key = tuple(_cell_to_str(c) for c in r)
            if key not in existing_data_keys:
                parsed_data.append(r)
                existing_data_keys.add(key)
        parsed_headers = []

    n_header_rows = len(parsed_headers)

    # c) 兜底：headers 仍为空时，看 data_grid[0] 是否像 header
    use_first_row_as_header = False
    if (
        n_header_rows == 0
        and parsed_data
        and len(parsed_data) > 0
        and row_looks_like_header([_cell_to_str(c) for c in parsed_data[0]])
    ):
        n_header_rows = 1
        use_first_row_as_header = True

    # Step 1: 切分表头 / 数据网格
    header_grid, data_grid_raw = _split_headers_and_data(parsed_data, n_header_rows)

    # Step 2: 折行合并（仅对数据部分）
    data_grid_merged = merge_continuation_rows(data_grid_raw, col_count=col_count)

    # Step 3: 清洗表头行
    headers_clean: List[List[str]] = []
    for row in header_grid:
        new_row: List[str] = []
        for j in range(col_count):
            cell = row[j] if j < len(row) else None
            new_row.append(clean_cell(cell))
        headers_clean.append(new_row)

    # Step 4: 展平表头 → 每列一条字符串
    # 兜底分支：use_first_row_as_header=True 时，parsed_headers 仍是空，
    # 但 header_grid 已有 1 行；_flatten_headers 会优先使用 header_grid
    # （上方"Header 校验"已把 parsed_headers 重新绑定为局部变量）
    if use_first_row_as_header and not parsed_headers:
        # 显式把"伪 header"也通过 parsed_headers 通道传下去（防御性）
        parsed_headers = header_grid
    flat_headers = _flatten_headers(
        parsed_headers,
        header_grid,
        col_count,
    )

    # Step 5: 构造 HeaderColumn[] + 改写表头中的相对年份
    header_columns: List[HeaderColumn] = []
    for i in range(col_count):
        raw = flat_headers[i] if i < len(flat_headers) else ""
        is_yoy = detect_yoy_column(raw)

        if is_yoy:
            normalized = raw
        else:
            normalized, _ = normalize_year_in_cell(raw, year_mapping)

        # 年份值：先看 normalized 中的显式年份
        year_val: Optional[int] = None
        if not is_yoy:
            year_val = extract_explicit_year(normalized)
            if year_val is None:
                if _RE_CURRENT_YEAR.search(raw):
                    year_val = year_mapping.current_year
                elif _RE_PREVIOUS_YEAR.search(raw):
                    year_val = year_mapping.previous_year
                elif _RE_TWO_YEARS_AGO.search(raw):
                    year_val = year_mapping.year_before_previous

        is_year = (year_val is not None) and not is_yoy

        role = _infer_column_role(raw, i, flat_headers)

        header_columns.append(
            HeaderColumn(
                index=i,
                raw=raw,
                normalized=normalized,
                is_year=is_year,
                is_yoy=is_yoy,
                year_value=year_val,
                column_role=role,
            )
        )

    # Step 6: 同步把 headers_clean 中各列的"相对年份"改写到位
    for row in headers_clean:
        for j in range(min(col_count, len(row))):
            col_meta = header_columns[j] if j < len(header_columns) else None
            if col_meta is None or col_meta.is_yoy:
                continue
            new_cell, _ = normalize_year_in_cell(row[j], year_mapping)
            row[j] = new_cell

    # Step 7: 列名括号单位 → UnitInfo.from_column_brackets
    bracket_units: dict[int, str] = {}
    for hc in header_columns:
        m = _HEADER_PAREN_UNIT_RE.search(hc.raw)
        if m:
            unit_text = clean_text(m.group(1))
            if unit_text:
                bracket_units[hc.index] = unit_text

    unit = location.unit
    if bracket_units:
        if unit is None:
            unit = UnitInfo(
                raw_lines=[],
                primary=None,
                from_column_brackets=bracket_units,
            )
        else:
            unit = UnitInfo(
                raw_lines=list(unit.raw_lines),
                primary=unit.primary,
                from_column_brackets={**unit.from_column_brackets, **bracket_units},
            )

    # Step 8: 数据网格清洗（已合并过续行，此处再走 clean_cell 防御）
    data_grid_clean: List[List[str]] = []
    for row in data_grid_merged:
        new_row = []
        for j in range(col_count):
            cell = row[j] if j < len(row) else ""
            new_row.append(clean_cell(cell))
        data_grid_clean.append(new_row)

    # Step 9: 把正文（标题与 <table> 之间的文字）的相对年份做一次计数（不改原文）
    if location.preceding_text:
        _, count = normalize_year_in_text(location.preceding_text, year_mapping)
        year_mapping.applied_count += count

    return TableInfo(
        source_path=source_path,
        table_index=table_index,
        report_year=year_mapping.report_year,
        title=location.title,
        title_level=location.title_level,
        unit=unit,
        headers=headers_clean,
        data_grid=data_grid_clean,
        row_count=len(data_grid_clean),
        col_count=col_count,
        header_columns=header_columns,
        year_mapping=year_mapping,
        raw_html=location.raw_html,
        raw_offset=location.start,
    )


def extract_tables_from_md_text(
    md_text: str,
    report_year: int,
    source_path: Path,
) -> List[TableInfo]:
    """从 md 文本（已读入内存）抽出所有表格并转成 TableInfo。

    Args:
        md_text: markdown 文件全文。
        report_year: 报告期年份。
        source_path: 仅用于回填 TableInfo.source_path。

    Returns:
        List[TableInfo]: 与 <table> 在文本中出现顺序一致；无表则返回 []。
    """
    if not md_text:
        _log.warning("md_text 为空: %s", source_path)
        return []

    table_parser_mod = _import_external_table_parser()
    locations = find_table_locations(md_text)

    if not locations:
        _log.warning("md 文件中没有发现 <table>: %s", source_path)
        return []

    results: List[TableInfo] = []
    year_mapping = build_year_mapping(report_year)

    for idx, loc in enumerate(locations):
        try:
            parsed = table_parser_mod.parse_html_table(loc.raw_html)
        except Exception as exc:  # noqa: BLE001 — 防御性兜底，跳过单表
            _log.error(
                "解析第 %d 个表格失败，跳过: %s (file=%s)", idx, exc, source_path
            )
            continue

        try:
            info = convert_parsed_table(
                parsed=parsed,
                location=loc,
                year_mapping=year_mapping,
                source_path=source_path,
                table_index=idx,
            )
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "装配第 %d 个表格为 TableInfo 失败，跳过: %s (file=%s)",
                idx,
                exc,
                source_path,
            )
            continue

        results.append(info)

    _log.info(
        "从 %s 解析 %d 张表格（report_year=%d）",
        source_path,
        len(results),
        report_year,
    )
    return results

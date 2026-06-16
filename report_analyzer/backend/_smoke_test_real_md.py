"""临时 smoke 脚本：用 extract_tables_from_md 解析真实年报 MD，打印所有 TableInfo 摘要。

不做任何写盘，纯肉眼回归用。跑完可删除。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 复用 tests/test_md_table_parser.py 的 sys.path 注入：把外部 table_parser 加入
_TABLE_PARSER_DIR = Path("D:/Quant/deep-research-report/shared/tools")
if _TABLE_PARSER_DIR.exists() and str(_TABLE_PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(_TABLE_PARSER_DIR))

# 让 `from app.services.md_table_parser import ...` 能工作
_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.md_table_parser import extract_tables_from_md  # noqa: E402

DEFAULT_MD = Path(
    "D:/quant/report_data/report/raw/宁德时代/md/split/"
    "宁德时代2025年年度报告/宁德时代2025年年度报告_业务报告.md"
)


def _short(s: str | None, n: int = 50) -> str:
    if s is None:
        return "-"
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "..."


def main() -> int:
    md_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MD
    print(f"输入: {md_path}")
    print(f"存在: {md_path.exists()}  大小: {md_path.stat().st_size if md_path.exists() else 'N/A'} bytes")
    print("-" * 80)

    tables = extract_tables_from_md(md_path)
    print(f"共解析到 {len(tables)} 张表\n")

    for t in tables:
        unit = t.unit.primary if t.unit else None
        unit_extra = (
            f" (列名括号单位: {list(t.unit.from_column_brackets.values())})"
            if t.unit and t.unit.from_column_brackets
            else ""
        )
        year_cols = [
            (hc.index, hc.normalized, hc.year_value)
            for hc in t.header_columns
            if hc.is_year
        ]
        yoy_cols = [
            (hc.index, hc.normalized)
            for hc in t.header_columns
            if hc.is_yoy
        ]
        print(
            f"[#{t.table_index:>2}] title(L{t.title_level})={_short(t.title, 40)}\n"
            f"      unit={unit}{unit_extra}\n"
            f"      shape={t.row_count}行 x {t.col_count}列  report_year={t.report_year}\n"
            f"      year_cols={year_cols}\n"
            f"      yoy_cols={yoy_cols}\n"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""阶段 3.x 跨年度表格合并 service 测试。

测试分层：
- 纯函数单测：`_parse_metadata_block` / `_parse_year_mapping` / `_jaccard` /
  `_is_section_header` / `_classify_table_kind` / `parse_table_csv`
- 集成测试：在 tmp_path 构造 N 年 `table/{stem}/*.csv` → 调 `scan_and_dispatch` →
  验证产物 / 分组 / 强/弱/unmergeable
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import pytest

from app.config import Settings
from app.services import table_merge_service as svc
from app.services.tables_extract_service import (
    _HEADER_END_MARKER,
    write_table_csv,
)
from app.services.md_table_parser import (
    TableInfo,
    UnitInfo,
    YearMapping,
)


# ============================================================
# 1. 纯函数：metadata / year_mapping / jaccard / section_header
# ============================================================


def test_parse_metadata_block_basic():
    lines = [
        "# source_md, 05_五、报告期内主要经营情况.md",
        "# report_year, 2025",
        "# unit, 千元",
        "not a meta line",
        "# key with, comma in value, ok?",
    ]
    meta = svc._parse_metadata_block(lines)
    assert meta["source_md"] == "05_五、报告期内主要经营情况.md"
    assert meta["report_year"] == "2025"
    assert meta["unit"] == "千元"


def test_parse_year_mapping():
    ym = svc._parse_year_mapping("current=2025,previous=2024,ybp=2023")
    assert ym == {"current": 2025, "previous": 2024, "ybp": 2023}


def test_parse_year_mapping_skips_invalid():
    ym = svc._parse_year_mapping("current=abc,previous=2024")
    assert ym == {"previous": 2024}


def test_jaccard_basic():
    assert svc._jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)
    assert svc._jaccard(set(), set()) == 1.0
    assert svc._jaccard(set(), {"a"}) == 0.0


def test_jaccard_full_overlap():
    assert svc._jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_pairwise_jaccard_single_returns_zero():
    assert svc._pairwise_jaccard([{"a"}]) == 0.0


def test_pairwise_jaccard_two():
    a = {f"col{i}" for i in range(5)}
    b = {f"col{i}" for i in range(3, 8)}  # 重叠 {col3,col4}=2 / 并集 8 = 0.25
    assert svc._pairwise_jaccard([a, b]) == pytest.approx(2 / 8)


def test_pairwise_jaccard_three():
    a = {"x", "y", "z"}
    b = {"y", "z", "w"}
    c = {"z", "w", "v"}
    # 3 pairs
    p1 = svc._jaccard(a, b)  # 2/4=0.5
    p2 = svc._jaccard(a, c)  # 1/5=0.2
    p3 = svc._jaccard(b, c)  # 2/4=0.5
    assert svc._pairwise_jaccard([a, b, c]) == pytest.approx((p1 + p2 + p3) / 3)


def test_is_section_header_true():
    assert svc._is_section_header(["按产品档次", "", "", "", ""]) is True


def test_is_section_header_false_when_subsequent_col_filled():
    assert svc._is_section_header(["项目", "金额", "", ""]) is False


def test_is_section_header_false_when_first_col_empty():
    assert svc._is_section_header(["", "金额", ""]) is False


def test_first_col_values_excludes_placeholders():
    t = _make_parsed(["a", "b", "c", "-", "—", "/"], rows=[])
    # 不直接测 _first_col_values(走 ParsedTable)；改成手搭数据
    pt = _make_parsed(
        ["科目", "2025年", "2024年"],
        rows=[
            ["营业收入", "1", "2"],
            ["-", "x", "y"],
            ["成本", "3", "4"],
        ],
    )
    vals = svc._first_col_values(pt)
    assert vals == {"营业收入", "成本"}


# ============================================================
# 2. parse_table_csv — 反解阶段 2.5 产物
# ============================================================


def _make_parsed(headers: List[str], rows: List[List[str]]) -> svc.ParsedTable:
    return svc.ParsedTable(
        csv_path=Path("dummy.csv"),
        source_md_stem="05_五",
        sanitized_title="营业收入",
        year=2025,
        unit="千元",
        year_mapping={"current": 2025, "previous": 2024, "ybp": 2023},
        headers=headers,
        rows=rows,
        col_count=len(headers),
        row_count=len(rows),
    )


def _make_table_info(title: str, data_rows: List[List[str]], unit: str = "千元") -> TableInfo:
    return TableInfo(
        source_path=Path("D:/dummy.md"),
        table_index=0,
        report_year=2025,
        title=title,
        title_level=3,
        unit=UnitInfo(raw_lines=[unit], primary=unit, from_column_brackets={}),
        headers=[["项目", "金额"]],
        data_grid=data_rows,
        row_count=len(data_rows),
        col_count=2,
        header_columns=[],
        year_mapping=YearMapping(
            report_year=2025,
            current_year=2025,
            previous_year=2024,
            year_before_previous=2023,
            applied_count=0,
        ),
        raw_html="<table>...</table>",
        raw_offset=0,
    )


def _write_table_csv(
    tmp: Path, stem: str, title: str, rows: List[List[str]], year: int
) -> Path:
    sub = tmp / stem
    sub.mkdir(parents=True, exist_ok=True)
    p = sub / f"{title}.csv"
    t = _make_table_info(title, rows)
    write_table_csv(p, t, f"{stem}.md", 1, 1)
    return p


def _setup_two_year(
    base: Path, company: str, year_a: int, year_b: int, stem: str, title: str
) -> None:
    """在某 base 下铺 2 年同名同 schema 的 CSV（同 stem 子目录）。"""
    rows_a = [
        ["营业收入", "1000", "900"],
        ["营业成本", "500", "450"],
    ]
    rows_b = [
        ["营业收入", "1100", "1000"],
        ["营业成本", "550", "500"],
    ]
    for y, rows in [(year_a, rows_a), (year_b, rows_b)]:
        per_year = base / company / "md" / "clean" / f"{company}{y}年年报" / "table" / stem
        per_year.mkdir(parents=True, exist_ok=True)
        # 直接复用 write_table_csv 写 BOM/header
        t = _make_table_info(title, rows)
        write_table_csv(per_year / f"{title}.csv", t, f"{stem}.md", 1, 1)


def test_parse_table_csv_roundtrip(tmp_path: Path):
    p = _write_table_csv(tmp_path, "05_五", "营业收入", [["a", "1"]], 2025)
    pt = svc.parse_table_csv(p, year=2025)
    assert pt.source_md_stem == "05_五"
    assert pt.sanitized_title == "营业收入"
    assert pt.year == 2025
    assert pt.unit == "千元"
    assert pt.headers == ["项目", "金额"]
    assert pt.rows == [["a", "1"]]
    assert pt.year_mapping == {"current": 2025, "previous": 2024, "ybp": 2023}


def test_parse_table_csv_raises_on_missing_end_marker(tmp_path: Path):
    p = tmp_path / "bad.csv"
    p.write_text("# title, x\n项目,金额\n1,2\n", encoding="utf-8-sig")
    with pytest.raises(svc.TableMergeError):
        svc.parse_table_csv(p, year=2025)


def test_parse_table_csv_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(svc.TableMergeError):
        svc.parse_table_csv(tmp_path / "nope.csv", year=2025)


# ============================================================
# 3. 分组 / 评估
# ============================================================


def test_group_across_years_separates_by_stem_and_title():
    t1 = _make_parsed(["项目", "2025年"], rows=[["营收", "1"]])
    t1.source_md_stem = "stemA"
    t1.sanitized_title = "营收"
    t1.year = 2025

    t2 = _make_parsed(["项目", "2024年"], rows=[["营收", "2"]])
    t2.source_md_stem = "stemA"
    t2.sanitized_title = "营收"
    t2.year = 2024

    t3 = _make_parsed(["项目", "2025年"], rows=[["x", "1"]])
    t3.source_md_stem = "stemB"
    t3.sanitized_title = "x"
    t3.year = 2025

    groups = svc.group_across_years([t1, t2, t3])
    assert len(groups) == 2
    by_key = {g.group_key: g for g in groups}
    assert len(by_key["stemA|营收"].tables) == 2
    assert len(by_key["stemB|x"].tables) == 1


def test_assess_group_strong_for_aligned_headers_and_rows():
    t1 = _make_parsed(
        ["项目", "2025年", "2024年"],
        rows=[["营收", "1000", "900"], ["成本", "500", "450"]],
    )
    t1.year = 2025
    t2 = _make_parsed(
        ["项目", "2025年", "2024年"],
        rows=[["营收", "1100", "1000"], ["成本", "550", "500"]],
    )
    t2.year = 2024
    g = svc.TableGroup(
        group_key="s|t", source_md_stem="s", sanitized_title="t", tables=[t1, t2]
    )
    v = svc.assess_group(g)
    assert v.status == "strong"
    assert v.years == [2024, 2025]


def test_assess_group_weak_when_columns_diverge():
    t1 = _make_parsed(
        ["项目", "金额", "占比", "毛利率比上年增减"],
        rows=[["营收", "1000", "60%", "减少 0.1 个百分点"]],
    )
    t1.year = 2025
    t2 = _make_parsed(
        ["项目", "营业收入", "占比"],
        rows=[["营收", "1100", "65%"]],
    )
    t2.year = 2024
    g = svc.TableGroup(
        group_key="s|t", source_md_stem="s", sanitized_title="t", tables=[t1, t2]
    )
    v = svc.assess_group(g)
    assert v.status == "weak"


def test_assess_group_unmergeable_single_year():
    t1 = _make_parsed(["项目", "2025年"], rows=[["a", "1"]])
    t1.year = 2025
    g = svc.TableGroup(
        group_key="s|t", source_md_stem="s", sanitized_title="t", tables=[t1]
    )
    v = svc.assess_group(g)
    assert v.status == "unmergeable"
    assert v.years == [2025]


# ============================================================
# 4. long / wide 记录构造
# ============================================================


def test_build_long_records_includes_year_subject_metric_value():
    t1 = _make_parsed(
        ["项目", "2025年金额"],
        rows=[["营业收入", "1000"], ["营业成本", "500"]],
    )
    t1.year = 2025
    t2 = _make_parsed(
        ["项目", "2024年金额"],
        rows=[["营业收入", "900"], ["营业成本", "450"]],
    )
    t2.year = 2024
    g = svc.TableGroup(
        group_key="s|t", source_md_stem="s", sanitized_title="t", tables=[t2, t1]
    )
    records = svc.build_long_records(g)
    # 2 years × 2 rows × 1 metric = 4
    assert len(records) == 4
    years = {r["year"] for r in records}
    assert years == {"2024", "2025"}
    # value 保留 str
    assert any(r["value"] == "1000" for r in records)
    assert all(r["unit"] == "千元" for r in records)


def test_build_long_records_preserves_section_header_row():
    t = _make_parsed(
        ["项目", "2025年金额", "2024年金额"],
        rows=[
            ["按产品档次", "", ""],  # section header
            ["茅台酒", "1000", "900"],
        ],
    )
    t.year = 2025
    g = svc.TableGroup(
        group_key="s|t", source_md_stem="s", sanitized_title="t", tables=[t]
    )
    records = svc.build_long_records(g)
    section = [r for r in records if r["_row_type"] == "section_header"]
    assert len(section) == 1
    assert section[0]["subject"] == "按产品档次"
    assert section[0]["metric"] == ""


def test_build_wide_records_subject_index_year_metric_cols():
    t1 = _make_parsed(
        ["项目", "金额"],
        rows=[["营收", "1000"], ["成本", "500"]],
    )
    t1.year = 2025
    t2 = _make_parsed(
        ["项目", "金额"],
        rows=[["营收", "900"], ["成本", "450"]],
    )
    t2.year = 2024
    g = svc.TableGroup(
        group_key="s|t", source_md_stem="s", sanitized_title="t", tables=[t1, t2]
    )
    headers, rows = svc.build_wide_records(g)
    assert headers[0] == "subject"
    assert "金额_2025" in headers
    assert "金额_2024" in headers
    by_subj = {r[0]: r for r in rows}
    assert by_subj["营收"][headers.index("金额_2025")] == "1000"
    assert by_subj["营收"][headers.index("金额_2024")] == "900"
    assert by_subj["成本"][headers.index("金额_2025")] == "500"


# ============================================================
# 5. scan_and_dispatch 集成
# ============================================================


def test_dispatch_empty_when_no_company_dir(tmp_path: Path):
    s = Settings(REPORT_DATA_PATH=tmp_path)
    r = svc.scan_and_dispatch("宁德时代", years=None, settings=s)
    assert r.status == "empty"
    assert r.total_groups == 0
    assert r.total_csvs == 0


def test_dispatch_strong_group_writes_long_and_wide(tmp_path: Path):
    _setup_two_year(tmp_path, "宁德时代", 2024, 2025, "05_五", "营业收入")
    s = Settings(REPORT_DATA_PATH=tmp_path)
    r = svc.scan_and_dispatch("宁德时代", years=None, settings=s)

    assert r.status == "done"
    assert r.total_csvs == 2
    assert r.total_groups == 1
    assert r.strong_count == 1
    assert r.weak_count == 0
    assert r.unmergeable_count == 0

    grp = r.groups[0]
    assert grp.status == "strong"
    assert grp.years == [2024, 2025]

    out_dir = tmp_path / "宁德时代" / "md" / "research_file" / "table"
    assert (out_dir / "05_五_营业收入_long.csv").exists()
    assert (out_dir / "05_五_营业收入_wide.csv").exists()

    long_text = (out_dir / "05_五_营业收入_long.csv").read_text(encoding="utf-8-sig")
    assert "# kind, long" in long_text
    assert "year,source_md_stem,subject,metric,value,unit" in long_text
    # 2 年 × 2 行 × 1 指标 = 4 数据行
    body = long_text.split(_HEADER_END_MARKER, 1)[1]
    data_rows = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
    # 表头 1 + 4 数据 = 5
    assert len(data_rows) == 5


def test_dispatch_unmergeable_for_single_year(tmp_path: Path):
    _setup_two_year(tmp_path, "宁德时代", 2025, 2099, "05_五", "营业收入")
    s = Settings(REPORT_DATA_PATH=tmp_path)
    r = svc.scan_and_dispatch("宁德时代", years=[2025], settings=s)
    # 2099 没产物 → years=[2025] → 单年 → unmergeable
    assert r.total_csvs == 1
    assert r.unmergeable_count == 1
    assert r.strong_count == 0


def test_dispatch_weak_when_columns_diverge(tmp_path: Path):
    """构造 2 年同名表但列名差异大 → 走 weak。"""
    base = tmp_path / "宁德时代" / "md" / "clean"
    stem = "05_五"
    # 2025：["项目", "金额", "占比"]
    for y, hdrs in [
        (2025, ["项目", "金额", "占比"]),
        (2024, ["科目", "营业收入", "成本占比"]),
    ]:
        per_year = base / f"宁德时代{y}年年报" / "table" / stem
        per_year.mkdir(parents=True, exist_ok=True)
        t = _make_table_info("营业收入", [["a", "1", "2"]])
        t.headers = [hdrs]
        t.col_count = len(hdrs)
        t.data_grid = [["营收", "1000", "60%"]]
        t.row_count = 1
        write_table_csv(per_year / "营业收入.csv", t, f"{stem}.md", 1, 1)
    s = Settings(REPORT_DATA_PATH=tmp_path)
    r = svc.scan_and_dispatch("宁德时代", years=None, settings=s)
    assert r.total_groups == 1
    assert r.weak_count == 1
    assert r.strong_count == 0
    assert r.skill_tasks[0].group_key == f"{stem}|营业收入"
    # 不写 long/wide
    out_dir = tmp_path / "宁德时代" / "md" / "research_file" / "table"
    assert not (out_dir / "05_五_营业收入_long.csv").exists()


def test_dispatch_force_clears_existing_outputs(tmp_path: Path):
    _setup_two_year(tmp_path, "宁德时代", 2024, 2025, "05_五", "营业收入")
    s = Settings(REPORT_DATA_PATH=tmp_path)
    # 第一次跑
    r1 = svc.scan_and_dispatch("宁德时代", years=None, settings=s, force=False)
    assert r1.strong_count == 1
    out_dir = tmp_path / "宁德时代" / "md" / "research_file" / "table"
    # 故意塞垃圾文件
    (out_dir / "stale.csv").write_text("noise", encoding="utf-8")

    # force=True 清空
    r2 = svc.scan_and_dispatch("宁德时代", years=None, settings=s, force=True)
    assert r2.strong_count == 1
    assert not (out_dir / "stale.csv").exists()


def test_dispatch_respects_years_filter(tmp_path: Path):
    _setup_two_year(tmp_path, "宁德时代", 2024, 2025, "05_五", "营业收入")
    s = Settings(REPORT_DATA_PATH=tmp_path)
    # 只取 2025：单年 → unmergeable
    r = svc.scan_and_dispatch("宁德时代", years=[2025], settings=s)
    assert r.years == [2025]
    assert r.unmergeable_count == 1

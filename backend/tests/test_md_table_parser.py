"""md_table_parser 子包测试。

设计原则：
- 全部 12 用例在内存中构造，无需真实文件
- 真实数据用例（解析宁德时代 2024/2025 年报）用 pytest.mark.skipif 防御本地缺失
- 模块级注入外部 table_parser 路径，避免依赖 conftest 的环境副作用
"""

from __future__ import annotations

import sys
from pathlib import Path

# 把外部 table_parser 所在目录加入 sys.path（inject_external_paths() 当前只注
# 入 DEEP_RESEARCH_PATH 顶层，而 table_parser.py 在 shared/tools/ 子目录下）
_TABLE_PARSER_DIR = Path("D:/Quant/deep-research-report/shared/tools")
if _TABLE_PARSER_DIR.exists() and str(_TABLE_PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(_TABLE_PARSER_DIR))

import pytest

from app.services.md_table_parser import (
    HeaderColumn,
    MdFileNotFoundError,
    MdTableError,
    ReportYearInferenceError,
    TableInfo,
    UnitInfo,
    YearMapping,
    build_year_mapping,
    clean_cell,
    clean_text,
    detect_yoy_column,
    extract_explicit_year,
    extract_tables_from_md,
    extract_year_from_md_path,
    find_table_locations,
    merge_continuation_rows,
    normalize_year_in_cell,
    normalize_year_in_text,
)


# ============================================================
# 1. extract_year_from_md_path — 父目录是 4 位数字
# ============================================================
def test_year_inference_from_parent_dir(tmp_path: Path) -> None:
    f = tmp_path / "2024" / "report.md"
    f.parent.mkdir(parents=True)
    f.write_text("dummy", encoding="utf-8")
    assert extract_year_from_md_path(f) == 2024


# ============================================================
# 2. extract_year_from_md_path — 父目录无年份 → 兜底文件名 stem
# ============================================================
def test_year_inference_from_filename(tmp_path: Path) -> None:
    f = tmp_path / "report_2025.md"
    f.write_text("dummy", encoding="utf-8")
    assert extract_year_from_md_path(f) == 2025


# ============================================================
# 3. extract_year_from_md_path — 完全无年份 → 抛 ReportYearInferenceError
# ============================================================
def test_year_inference_raises(tmp_path: Path) -> None:
    f = tmp_path / "no_year.md"
    f.write_text("dummy", encoding="utf-8")
    with pytest.raises(ReportYearInferenceError) as excinfo:
        extract_year_from_md_path(f)
    assert excinfo.value.path == f
    # candidates 最多保留 8 段；父目录不命中年份 + stem 也不命中 → 必抛
    assert isinstance(excinfo.value.candidates, list)
    assert len(excinfo.value.candidates) > 0


# ============================================================
# 4. 真实 2024 宁德时代年报 → ≥ 5 张表，标题对应最近 Markdown 标题
# ============================================================
REAL_2024 = Path(
    "D:/quant/report_data/宁德时代/md/clean/宁德时代2024年年报/管理层讨论/04_四、主营业务分析.md"
)


@pytest.mark.skipif(not REAL_2024.exists(), reason="真实 2024 数据缺失")
def test_real_2024_extracts_multiple_tables() -> None:
    tables = extract_tables_from_md(REAL_2024)
    assert len(tables) >= 5, f"期望 ≥5 张表，实际 {len(tables)}"
    for t in tables:
        assert isinstance(t, TableInfo)
        assert t.title is not None
        assert t.col_count > 0
        assert t.row_count >= 0
        # 标题应是非空字符串
        assert t.title.strip() != ""


# ============================================================
# 5. 单位行 "单位：千元" → UnitInfo.primary
# ============================================================
def test_unit_line_becomes_primary(tmp_path: Path) -> None:
    md = (
        "## 测试表标题\n\n"
        "单位：千元\n\n"
        "<table><tr><td>项目</td><td>2024年</td></tr>"
        "<tr><td>营业收入</td><td>100,000</td></tr></table>\n"
    )
    f = tmp_path / "2024" / "t.md"
    f.parent.mkdir(parents=True)
    f.write_text(md, encoding="utf-8")

    tables = extract_tables_from_md(f)
    assert len(tables) == 1
    t = tables[0]
    assert t.unit is not None
    assert t.unit.primary == "千元"
    assert "千元" in t.unit.raw_lines


# ============================================================
# 6. 表头本报告期 / 上年同期 → 2024年 / 2023年；同比列保持原文且 is_yoy=True
# ============================================================
def test_header_year_normalization(tmp_path: Path) -> None:
    md = (
        "## 测试\n\n"
        "<table><tr><td>项目</td><td>本报告期</td><td>上年同期</td><td>同比增减</td></tr>"
        "<tr><td>营业收入</td><td>100</td><td>80</td><td>25.00%</td></tr></table>\n"
    )
    f = tmp_path / "2024" / "t.md"
    f.parent.mkdir(parents=True)
    f.write_text(md, encoding="utf-8")

    tables = extract_tables_from_md(f)
    assert len(tables) == 1
    t = tables[0]

    year_cols = [hc for hc in t.header_columns if hc.is_year]
    yoy_cols = [hc for hc in t.header_columns if hc.is_yoy]

    # 2024 → current=2024, previous=2023
    assert len(year_cols) == 2
    assert {hc.year_value for hc in year_cols} == {2024, 2023}
    assert {hc.normalized for hc in year_cols} == {"2024年", "2023年"}

    # 同比列：保留原文
    assert len(yoy_cols) == 1
    assert yoy_cols[0].raw == "同比增减"
    assert yoy_cols[0].normalized == "同比增减"
    assert yoy_cols[0].is_year is False


# ============================================================
# 7. LaTeX 清洗：$1 5 . 0 1 \%$ → 15.01%；$200\mathrm{Wh/kg}$ → 200Wh/kg
# ============================================================
def test_latex_digit_percent() -> None:
    assert clean_cell("$1 5 . 0 1 \\%$") == "15.01%"


def test_latex_digit_with_unit() -> None:
    assert clean_cell("$200\\mathrm{Wh/kg}$") == "200Wh/kg"


def test_latex_generic_dollar() -> None:
    # 兜底：纯 $...$
    assert clean_cell("$abc$") == "abc"


def test_box_chars_and_whitespace() -> None:
    assert clean_cell("  □ ■ ☐\u00A0  hello  ") == "hello"


def test_clean_text_basic() -> None:
    assert clean_text("  100 %  ") == "100 %"


# CJK 字符之间的空白应被移除（处理 HTML 字面换行造成的"项目 名称"断词）
def test_cjk_between_whitespace_removed() -> None:
    assert clean_cell("项目 名称") == "项目名称"
    assert clean_cell("主要研发项 目名称") == "主要研发项目名称"
    # 半角空格、全角空格、换行都被识别
    assert clean_cell("骁遥双核　电池") == "骁遥双核电池"
    assert clean_cell("电池\n材料") == "电池材料"
    # CJK + 拉丁字符之间保留空格（如 "项目 A"）
    assert clean_cell("项目 A") == "项目 A"
    # 纯拉丁字符之间保留空格
    assert clean_cell("Hello World") == "Hello World"
    # 数字 + CJK 之间保留空格
    assert clean_cell("100 元") == "100 元"


# ============================================================
# 8. merge_continuation_rows — 前 N-1 个空 + 最后一列续行 → 合并到上一行
# ============================================================
def test_merge_continuation_rows_basic() -> None:
    """前 N-1 列为空、仅最后一列非空 → 续行，合并到上一行最后一列。"""
    grid = [
        ["项目", "数据"],
        ["营业收入", "100"],
        ["", "（含税）"],         # 续行
        ["净利润", "20"],         # 新的数据行
        ["", "扣非 15"],          # 续行
    ]
    merged = merge_continuation_rows(grid, col_count=2)
    # 2 个真实数据行 + 续行合并后 = 3 行
    assert len(merged) == 3
    assert merged[0] == ["项目", "数据"]
    # 营业收入行的最后一列合并了 "（含税）"
    assert merged[1][0] == "营业收入"
    assert merged[1][1] == "100\n（含税）"
    # 净利润行的最后一列合并了 "扣非 15"
    assert merged[2][0] == "净利润"
    assert merged[2][1] == "20\n扣非 15"


def test_merge_continuation_no_continuation() -> None:
    grid = [
        ["项目", "数据"],
        ["营业收入", "100"],
        ["净利润", "20"],
    ]
    merged = merge_continuation_rows(grid, col_count=2)
    assert len(merged) == 3
    assert merged[1] == ["营业收入", "100"]
    assert merged[2] == ["净利润", "20"]


# ============================================================
# 9. 不存在路径 → 抛 MdFileNotFoundError
# ============================================================
def test_nonexistent_path_raises(tmp_path: Path) -> None:
    f = tmp_path / "not_exists.md"
    with pytest.raises(MdFileNotFoundError):
        extract_tables_from_md(f)


def test_nonexistent_path_inherits_filenotfounderror() -> None:
    """异常应同时是 FileNotFoundError（兼容标准 except）"""
    f = Path("D:/nonexistent/xyz.md")
    with pytest.raises(FileNotFoundError):
        extract_tables_from_md(f)


# ============================================================
# 10. 无 <table> 的 md → 返回 [] 且含 warning 日志
# ============================================================
def test_no_table_returns_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    md = "## 标题\n\n这是一段没有表格的正文。\n"
    f = tmp_path / "2024" / "t.md"
    f.parent.mkdir(parents=True)
    f.write_text(md, encoding="utf-8")

    with caplog.at_level("WARNING"):
        tables = extract_tables_from_md(f)
    assert tables == []
    # 至少有一条 warning 说明无表
    assert any("table" in rec.message.lower() or "表" in rec.message for rec in caplog.records)


# ============================================================
# 11. 列名 "销售额（千元）" → UnitInfo.from_column_brackets
# ============================================================
def test_bracket_unit_in_column_header(tmp_path: Path) -> None:
    md = (
        "## 测试\n\n"
        "<table><tr><td>序号</td><td>客户名称</td><td>销售额（千元）</td><td>占年度销售总额比例</td></tr>"
        "<tr><td>1</td><td>客户A</td><td>54,173,399</td><td>14.96%</td></tr></table>\n"
    )
    f = tmp_path / "2024" / "t.md"
    f.parent.mkdir(parents=True)
    f.write_text(md, encoding="utf-8")

    tables = extract_tables_from_md(f)
    assert len(tables) == 1
    t = tables[0]
    assert t.unit is not None
    assert t.unit.from_column_brackets.get(2) == "千元"


# ============================================================
# 12. 同比列文本识别：含 "上年同期" + "增减" / "变动比例" / "毛利率比上年同期增减"
# ============================================================
@pytest.mark.parametrize(
    "header_text",
    [
        "营业收入比上年同期增减",
        "毛利率比上年同期增减",
        "营业成本比上年同期增减",
        "变动比例",
    ],
)
def test_yoy_column_detection(header_text: str) -> None:
    assert detect_yoy_column(header_text) is True
    # YoY 列：含 "上年同期" 也不应被改写
    mapping = build_year_mapping(2024)
    out, changed = normalize_year_in_cell(header_text, mapping)
    assert changed is False
    assert out == header_text


# "重大变化" 类是非描述，不应被误判为 yoy 列
@pytest.mark.parametrize(
    "header_text",
    [
        "影响重大合同履行的各项条件是否发生重大变化",
        "是否存在合同无法履行的重大风险",
        "相关情况是否发生重大变化",
    ],
)
def test_yoy_column_false_positive(header_text: str) -> None:
    """裸 '变化'（不带 '率'）不应触发 yoy 标记。"""
    assert detect_yoy_column(header_text) is False


# "本报告期..." / "本期..." 这类表头应被识别为 year 列
def test_header_benbaobaopi_benqi(tmp_path: Path) -> None:
    md = (
        "## 测试\n\n"
        "<table><tr><td>合同标的</td><td>本报告期履行金额</td>"
        "<td>本期确认的销售收入金额</td></tr>"
        "<tr><td>锂离子动力电池供应</td><td>54,173,399</td><td>54,173,399</td></tr></table>\n"
    )
    f = tmp_path / "2024" / "t.md"
    f.parent.mkdir(parents=True)
    f.write_text(md, encoding="utf-8")

    tables = extract_tables_from_md(f)
    assert len(tables) == 1
    t = tables[0]
    year_cols = [hc for hc in t.header_columns if hc.is_year]
    assert len(year_cols) == 2
    assert {hc.year_value for hc in year_cols} == {2024}
    # 改写后：'本报告期履行金额' → '2024年履行金额'，
    # '本期确认的销售收入金额' → '2024年确认的销售收入金额'
    normalized_set = {hc.normalized for hc in year_cols}
    assert "2024年履行金额" in normalized_set
    assert "2024年确认的销售收入金额" in normalized_set


# 兜底：外部 parser 漏检 header 时，把 data_grid[0] 当 header
def test_first_row_as_header_fallback(tmp_path: Path) -> None:
    """首列是 '合同标的'（不在外部 parser 的关键词白名单内）→ headers 为空。
    我们的兜底应把第一行识别为 header。"""
    md = (
        "## 重大销售合同\n\n"
        "单位：千元\n\n"
        "<table><tr><td>合同标的</td><td>对方当事人</td>"
        "<td>本报告期履行金额</td></tr>"
        "<tr><td>客户A</td><td>对方公司</td><td>54,173,399</td></tr></table>\n"
    )
    f = tmp_path / "2024" / "t.md"
    f.parent.mkdir(parents=True)
    f.write_text(md, encoding="utf-8")

    tables = extract_tables_from_md(f)
    assert len(tables) == 1
    t = tables[0]
    # col 2 "本报告期履行金额" 应被识别为 year 列
    year_cols = [hc for hc in t.header_columns if hc.is_year]
    assert len(year_cols) == 1
    assert year_cols[0].normalized == "2024年履行金额"


# "字段 | 值" 类的 2 列 KV 摘要表不应被误判为有 header
def test_kv_summary_table_no_header(tmp_path: Path) -> None:
    """行 0 第二列是数字 "165,061,533"（含逗号）→ 触发 _VALUE_LIKE，
    不应被当作 header；所有行都应留在 data_grid 里。"""
    md = (
        "## 客户合计\n\n"
        "<table><tr><td>前五名客户合计销售金额（千元）</td>"
        "<td>165,061,533</td></tr>"
        "<tr><td>前五名客户合计销售金额占年度销售总额比例</td>"
        "<td>38.96%</td></tr></table>\n"
    )
    f = tmp_path / "2024" / "t.md"
    f.parent.mkdir(parents=True)
    f.write_text(md, encoding="utf-8")

    tables = extract_tables_from_md(f)
    assert len(tables) == 1
    t = tables[0]
    # 所有 2 行都应是 data（不丢行）
    assert t.row_count == 2
    assert t.col_count == 2
    assert t.headers == []  # 没有 header 行
    assert t.data_grid[0] == ["前五名客户合计销售金额（千元）", "165,061,533"]
    assert t.data_grid[1] == ["前五名客户合计销售金额占年度销售总额比例", "38.96%"]


# 真实数据：2025 年报表 7（KV 摘要）应被识别为 3 行 data
# 备注：阶段 2.4 section3 拆分会把"四、主营业务分析"内的未编号 H2 子节
# （换电、零碳、全域增量等）作为独立 H2 切出；带编号的"前五名客户"、"主要研发项目"
# 这类表实际落在 "07_迈入\"全域增量\"时代.md"（最后一个 H2 子节）里。
# 报告期由抽表 service 显式传入 report_year，无需依赖父目录名推断。
REAL_2025 = Path(
    "D:/quant/report_data/宁德时代/md/clean/宁德时代2025年年报/管理层讨论/07_迈入“全域增量”时代.md"
)


@pytest.mark.skipif(not REAL_2025.exists(), reason="真实 2025 数据缺失")
def test_real_2025_kv_table_parsed_as_data() -> None:
    tables = extract_tables_from_md(REAL_2025)
    # 2025 表 7 是 "前五名客户合计销售金额..." KV 摘要
    t = tables[7]
    assert t.row_count == 3
    assert t.headers == []
    assert "前五名客户合计销售金额(千元)" in t.data_grid[0][0]
    assert t.data_grid[0][1] == "165,061,533"


# 真实数据：2025 年报表 12 表头 CJK 词内空白应被移除
@pytest.mark.skipif(not REAL_2025.exists(), reason="真实 2025 数据缺失")
def test_real_2025_cjk_header_no_whitespace() -> None:
    tables = extract_tables_from_md(REAL_2025)
    t = tables[12]  # 主要研发项目
    # 原始 HTML: "主要研发项\n目名称" → 清洗后应为 "主要研发项目名称"
    assert t.headers[0][0] == "主要研发项目名称"
    # 数据行也应清洗
    assert "骁遥双核电池" in t.data_grid[0][0]


# ============================================================
# 补充：build_year_mapping、extract_explicit_year 单元
# ============================================================
def test_build_year_mapping() -> None:
    m = build_year_mapping(2024)
    assert isinstance(m, YearMapping)
    assert m.report_year == 2024
    assert m.current_year == 2024
    assert m.previous_year == 2023
    assert m.year_before_previous == 2022


def test_extract_explicit_year() -> None:
    assert extract_explicit_year("2024年营业收入") == 2024
    assert extract_explicit_year("2023 年净利润") == 2023
    # 裸 4 位数字兜底
    assert extract_explicit_year("2024") == 2024
    assert extract_explicit_year("本年度") is None


def test_normalize_year_in_text_paragraph() -> None:
    """正文（非单元格）的相对年份应被改写。"""
    mapping = build_year_mapping(2024)
    text = "本报告期公司实现净利润 100 亿元，上年同期为 80 亿元，同比增长 25%。"
    new, count = normalize_year_in_text(text, mapping)
    assert "2024年" in new
    assert "2023年" in new
    # count: 至少 2 次（本报告期 + 上年同期）
    assert count >= 2


# ============================================================
# 补充：异常类继承
# ============================================================
def test_exception_inheritance() -> None:
    assert issubclass(MdFileNotFoundError, MdTableError)
    assert issubclass(MdFileNotFoundError, FileNotFoundError)
    assert issubclass(ReportYearInferenceError, ValueError)


# ============================================================
# 补充：find_table_locations 基础
# ============================================================
def test_find_table_locations_basic() -> None:
    md = "## 标题\n\n单位：千元\n\n<table><tr><td>项目</td></tr></table>\n"
    locs = find_table_locations(md)
    assert len(locs) == 1
    assert locs[0].title == "标题"
    assert locs[0].title_level == 2
    assert locs[0].unit is not None
    assert locs[0].unit.primary == "千元"
    assert "<table" in locs[0].raw_html

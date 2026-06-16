"""阶段 2.5 表格抽取 → CSV 落盘 service 测试。

新结构（取消 8 大类）：每张表 → `table/{源 md stem}/{清理后标题}.csv`。

测试分层：
- 纯函数单测：`sanitize_title` / `dedup_path` / 写盘格式
- 集成测试：在 tmp_path 构造 `md/clean/.../管理层讨论/*.md` → 调 service → 验证产物
- 路由测试：用 `client` fixture 验证 404 / 200 行为
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from app.services import tables_extract_service as svc
from app.services.md_table_parser import TableInfo, UnitInfo, YearMapping


# ============================================================
# 1. sanitize_title — 去前缀编号 + 清非法字符
# ============================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("(1). 主营业务分行业、分产品、分地区、分销售模式情况", "主营业务分行业、分产品、分地区、分销售模式情况"),
        (". 成本分析表", "成本分析表"),
        (".研发投入情况表", "研发投入情况表"),
        ("1、 利润表及现金流量表", "利润表及现金流量表"),
        ("以公允价值计量的金融资产", "以公允价值计量的金融资产"),
        ("资产及负债状况", "资产及负债状况"),
        ("现金流", "现金流"),
    ],
)
def test_sanitize_strips_numbering_prefix(raw, expected):
    assert svc.sanitize_title(raw) == expected


def test_sanitize_strips_illegal_chars():
    # \n 不在 _ILLEGAL_CHARS 中（会走空白折叠为单空格），其他非法字符全删
    assert svc.sanitize_title("a/b\\c:d*e?f\"g<h>i|j") == "abcdefghij"


def test_sanitize_collapses_whitespace():
    assert svc.sanitize_title("项目\n\t名   称") == "项目 名 称"


def test_sanitize_truncates_to_60():
    s = "项" * 100
    out = svc.sanitize_title(s)
    assert len(out) == 60


def test_sanitize_empty_returns_placeholder():
    assert svc.sanitize_title("") == "未命名表"
    assert svc.sanitize_title("///") == "未命名表"


# ============================================================
# 2. dedup_path — 同名自增去重
# ============================================================


def test_dedup_path_returns_original_when_absent(tmp_path: Path):
    p = svc.dedup_path(tmp_path, "营业收入")
    assert p == tmp_path / "营业收入.csv"


def test_dedup_path_appends_underscore_n(tmp_path: Path):
    (tmp_path / "营业收入.csv").write_text("x", encoding="utf-8")
    p = svc.dedup_path(tmp_path, "营业收入")
    assert p == tmp_path / "营业收入_2.csv"


def test_dedup_path_skips_existing_n(tmp_path: Path):
    (tmp_path / "营业收入.csv").write_text("x", encoding="utf-8")
    (tmp_path / "营业收入_2.csv").write_text("x", encoding="utf-8")
    (tmp_path / "营业收入_3.csv").write_text("x", encoding="utf-8")
    p = svc.dedup_path(tmp_path, "营业收入")
    assert p == tmp_path / "营业收入_4.csv"


# ============================================================
# 3. write_table_csv — 元数据头格式 + 多块追加（保留：API 未变）
# ============================================================


def _make_table_info(title: str, data_rows: List[List[str]], unit: str = "千元") -> TableInfo:
    headers = [["项目", "金额"]]
    data_grid = data_rows
    return TableInfo(
        source_path=Path("D:/dummy.md"),
        table_index=0,
        report_year=2025,
        title=title,
        title_level=3,
        unit=UnitInfo(raw_lines=[unit], primary=unit, from_column_brackets={}),
        headers=headers,
        data_grid=data_grid,
        row_count=len(data_grid),
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


def test_write_csv_creates_with_bom(tmp_path: Path):
    t = _make_table_info("测试", [["营业收入", "100"]])
    p = tmp_path / "测试.csv"
    svc.write_table_csv(p, t, "source.md", 1, 1)
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = raw.decode("utf-8-sig")
    assert "# source_md, source.md" in text
    assert "# title, 测试" in text
    assert "# report_year, 2025" in text
    assert "# unit, 千元" in text
    assert "# ============== END HEADER ==============" in text
    assert "项目,金额" in text
    assert '"营业收入","100"' in text or "营业收入,100" in text


def test_write_csv_appends_no_extra_bom(tmp_path: Path):
    """虽然新结构是一表一文件，但 write_table_csv 仍是 append 安全（API 兼容）。"""
    t1 = _make_table_info("表1", [["A", "1"]])
    t2 = _make_table_info("表2", [["B", "2"]])
    p = tmp_path / "out.csv"
    svc.write_table_csv(p, t1, "s.md", 1, 2)
    svc.write_table_csv(p, t2, "s.md", 2, 2)
    raw = p.read_bytes()
    assert raw.count(b"\xef\xbb\xbf") == 1
    text = raw.decode("utf-8-sig")
    assert text.count("# title, ") == 2
    assert "表1" in text and "表2" in text


def test_write_csv_escapes_commas_and_quotes(tmp_path: Path):
    t = _make_table_info("含,逗号和\"引号", [["x,1", '"y"']])
    p = tmp_path / "out.csv"
    svc.write_table_csv(p, t, "s.md", 1, 1)
    text = p.read_text(encoding="utf-8-sig")
    assert '"x,1"' in text
    assert '"""y"""' in text or '""y""' in text


# ============================================================
# 4. extract_tables_to_csv — 集成测试（新结构：按源 md 分子目录）
# ============================================================


def _write_md_with_table(path: Path, title: str) -> None:
    md = f"""## 第三节 管理层讨论

### 主营业务分析

#### {title}

单位：千元

<table>
<tr><th>项目</th><th>金额</th></tr>
<tr><td>营业收入</td><td>1,000</td></tr>
</table>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")


def _setup_company_files(
    company: str, year: int, base: Path, md_specs: List[tuple[str, str]]
) -> Path:
    """md_specs: [(stem, table_title), ...]。"""
    sec_dir = base / company / "md" / "clean" / f"{company}{year}年年报" / "管理层讨论"
    for stem, title in md_specs:
        _write_md_with_table(sec_dir / f"{stem}.md", title)
    return sec_dir


def test_extract_writes_one_csv_per_table_grouped_by_md(tmp_path: Path):
    """每张表 → 一个独立 csv，按源 md stem 分子目录。"""
    _setup_company_files(
        "宁德时代", 2025, tmp_path,
        [
            ("05_五、主要经营情况", "成本分析表"),
            ("05_五、主要经营情况", "资产及负债状况"),  # 同 md 第二张表会被合并到同一 md，但这里两个 md 同 stem 会覆盖
        ],
    )
    # 用单 md + 多表更准确，再补一个含两表的 md
    sec_dir = tmp_path / "宁德时代" / "md" / "clean" / "宁德时代2025年年报" / "管理层讨论"
    (sec_dir / "06_六、其他.md").write_text(
        """## 六

#### 现金流

单位：元

<table><tr><th>项目</th><th>金额</th></tr><tr><td>经营</td><td>1</td></tr></table>

#### 研发投入情况表

<table><tr><th>项目</th><th>金额</th></tr><tr><td>投入</td><td>2</td></tr></table>
""",
        encoding="utf-8",
    )

    from app.config import Settings
    s = Settings(REPORT_DATA_PATH=tmp_path)
    outcome = svc.extract_tables_to_csv(s, "宁德时代", 2025)

    assert outcome.status == "done"
    # 05 md 被覆盖只剩 1 张表；06 md 有 2 张表 → total 3
    assert outcome.total == 3
    assert outcome.sections["05_五、主要经营情况"] == 1
    assert outcome.sections["06_六、其他"] == 2

    out = tmp_path / "宁德时代" / "md" / "clean" / "宁德时代2025年年报" / "table"
    # 子目录按源 md stem
    assert (out / "05_五、主要经营情况" / "资产及负债状况.csv").exists()
    assert (out / "06_六、其他" / "现金流.csv").exists()
    assert (out / "06_六、其他" / "研发投入情况表.csv").exists()


def test_extract_dedup_same_title_in_same_md(tmp_path: Path):
    """同 md 中两张同标题表 → 同子目录下 _2 去重。"""
    sec_dir = tmp_path / "宁德时代" / "md" / "clean" / "宁德时代2025年年报" / "管理层讨论"
    sec_dir.mkdir(parents=True)
    (sec_dir / "01_一.md").write_text(
        """## 一

#### 产能状况

<table><tr><th>项目</th><th>金额</th></tr><tr><td>x</td><td>1</td></tr></table>

#### 产能状况

<table><tr><th>项目</th><th>金额</th></tr><tr><td>y</td><td>2</td></tr></table>
""",
        encoding="utf-8",
    )

    from app.config import Settings
    s = Settings(REPORT_DATA_PATH=tmp_path)
    outcome = svc.extract_tables_to_csv(s, "宁德时代", 2025)
    assert outcome.total == 2

    sub = tmp_path / "宁德时代" / "md" / "clean" / "宁德时代2025年年报" / "table" / "01_一"
    files = sorted(p.name for p in sub.glob("*.csv"))
    assert files == ["产能状况.csv", "产能状况_2.csv"]


def test_extract_skips_md_without_tables(tmp_path: Path):
    """无 <table> 的 md 不会创建空子目录。"""
    sec_dir = tmp_path / "宁德时代" / "md" / "clean" / "宁德时代2025年年报" / "管理层讨论"
    sec_dir.mkdir(parents=True)
    (sec_dir / "01_一.md").write_text("## 一\n\n没有表格只有文字。\n", encoding="utf-8")

    from app.config import Settings
    s = Settings(REPORT_DATA_PATH=tmp_path)
    outcome = svc.extract_tables_to_csv(s, "宁德时代", 2025)
    assert outcome.status == "done"
    assert outcome.total == 0
    assert outcome.sections == {}

    out = tmp_path / "宁德时代" / "md" / "clean" / "宁德时代2025年年报" / "table"
    assert out.is_dir()
    assert not (out / "01_一").exists()


def test_extract_raises_when_section_dir_missing(tmp_path: Path):
    from app.config import Settings
    s = Settings(REPORT_DATA_PATH=tmp_path)
    with pytest.raises(svc.MdSectionNotFoundError):
        svc.extract_tables_to_csv(s, "宁德时代", 2025)


def test_extract_empty_when_no_md(tmp_path: Path):
    sec_dir = tmp_path / "宁德时代" / "md" / "clean" / "宁德时代2025年年报" / "管理层讨论"
    sec_dir.mkdir(parents=True)
    from app.config import Settings
    s = Settings(REPORT_DATA_PATH=tmp_path)
    outcome = svc.extract_tables_to_csv(s, "宁德时代", 2025)
    assert outcome.status == "empty"
    assert outcome.total == 0
    assert outcome.sections == {}
    assert (
        tmp_path / "宁德时代" / "md" / "clean" / "宁德时代2025年年报" / "table"
    ).is_dir()


# ============================================================
# 5. 路由测试
# ============================================================


def _create_company_and_report(db, name: str, year: int) -> None:
    from app.models import Company, AnnualReport
    c = Company(name=name)
    db.add(c)
    db.commit()
    db.refresh(c)
    r = AnnualReport(
        company_id=c.id,
        year=year,
        pdf_path=f"{name}/pdf/original/{name}{year}年年度报告.pdf",
    )
    db.add(r)
    db.commit()


def test_route_404_when_company_missing(client):
    r = client.post("/companies/不存在的公司/tables/extract?year=2025")
    assert r.status_code == 404
    assert "公司不存在" in r.json()["detail"]


def test_route_404_when_report_missing(client):
    from app.db import session as db_session
    db = db_session.SessionLocal()
    try:
        _create_company_and_report(db, "宁德时代", 2025)
    finally:
        db.close()

    r = client.post("/companies/宁德时代/tables/extract?year=2099")
    assert r.status_code == 404
    assert "年报未上传" in r.json()["detail"]


def test_route_404_when_section_dir_missing(client, tmp_env):
    from app.db import session as db_session
    db = db_session.SessionLocal()
    try:
        _create_company_and_report(db, "宁德时代", 2025)
    finally:
        db.close()

    r = client.post("/companies/宁德时代/tables/extract?year=2025")
    assert r.status_code == 404
    assert "管理层讨论目录不存在" in r.json()["detail"]


def test_route_200_empty_when_no_md(client, tmp_env):
    from app.db import session as db_session
    db = db_session.SessionLocal()
    try:
        _create_company_and_report(db, "宁德时代", 2025)
    finally:
        db.close()

    sec_dir = (
        tmp_env / "report_data" / "宁德时代" / "md" / "clean"
        / "宁德时代2025年年报" / "管理层讨论"
    )
    sec_dir.mkdir(parents=True)

    r = client.post("/companies/宁德时代/tables/extract?year=2025")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["extract_tables_status"] == "empty"
    assert body["sections"] == []
    assert body["csv_paths"] == []

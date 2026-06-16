"""PDF 切分 service / API 测试。

策略：
- API 层测 404 边界
- Service 层用 PyMuPDF 造一个 mock PDF（含"第八节 财务报告"一级标题 + 目录 + 引用），
  验证多信号定位 + 切分 + DB 字段写入

PyMuPDF 注：CJK 字体用内置 `china-s`（Heiti） / `china-ts`（Fangsong），
无需外部字体文件，跨平台一致。
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest


# PyMuPDF 内置 CJK 字体名
FONT_HEITI = "china-s"    # 黑体（粗）
FONT_FANGSONG = "china-ts"  # 仿宋（细）


def _insert_cjk(page, point, text, fontsize, *, font=FONT_FANGSONG):
    """往 page 插入中文文本。PyMuPDF 1.24 + china-s 内置 CJK 字体。"""
    page.insert_text(point, text, fontsize=fontsize, fontname=font, color=(0, 0, 0))


def _make_mock_annual_report_pdf(
    path: Path,
    *,
    other_pages: int = 30,
    finance_pages: int = 80,
) -> Path:
    """造一个 mock 年报 PDF：
    - 1 页"目 录"页（含"第八节 财务报告 .... p_other+1"）
    - other_pages 页"其他章节"（含若干正文 + "详见第八节 财务报告"等引用）
    - 1 页"第八节 财务报告"标题页（居中、大字号、独立成行）
    - finance_pages 页财务报告内容

    Returns: path
    """
    doc = fitz.open()
    page_w, page_h = 595, 842

    # ---- 目录页（page 0）----
    toc_page = doc.new_page(width=page_w, height=page_h)
    _insert_cjk(toc_page, (200, 100), "目 录", fontsize=16, font=FONT_HEITI)
    _insert_cjk(
        toc_page,
        (50, 150),
        f"第八节 财务报告 {'.' * 60} {other_pages + 1}",
        fontsize=10,
    )
    _insert_cjk(
        toc_page,
        (50, 180),
        f"第一节 重要提示 {'.' * 60} 1",
        fontsize=10,
    )

    # ---- "其他章节"页（page 1 ~ other_pages）----
    for i in range(other_pages):
        p = doc.new_page(width=page_w, height=page_h)
        _insert_cjk(p, (50, 50), f"一、报告期内公司业务情况（page {i+1}）", fontsize=10)
        _insert_cjk(
            p,
            (50, 200),
            "本报告期内公司主营业务持续发展。详见本报告"
            f'第八节 财务报告之"财务报表附注"第 {i+1} 项。',
            fontsize=10,
        )
        for j in range(5):
            _insert_cjk(
                p,
                (50, 300 + j * 30),
                f"第{j+1}行普通正文：报告期公司实现营业收入若干元，同比增长若干。",
                fontsize=10,
            )

    # ---- 财务报告标题页（page other_pages+1，即 0-based other_pages）----
    title_page = doc.new_page(width=page_w, height=page_h)
    # 一级标题：居中（page_w/2 = 297.5）、大字号 16、独立成行
    _insert_cjk(
        title_page,
        (150, 100),  # y0 = 100 < page_h * 0.30 = 252.6
        "第八节 财务报告",
        fontsize=16,
        font=FONT_HEITI,
    )

    # ---- 财务报告内容页 ----
    for i in range(finance_pages):
        p = doc.new_page(width=page_w, height=page_h)
        _insert_cjk(p, (50, 100), f"财务报告内容 {i+1}", fontsize=10)
        _insert_cjk(p, (50, 200), f"合并资产负债表项目（page {i+1}）", fontsize=10)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return path


# ================== API 层测试 ==================


def test_split_unknown_company_404(client) -> None:
    r = client.post("/companies/不存在/split-pdf?year=2023")
    assert r.status_code == 404


def test_split_unuploaded_year_404(client) -> None:
    r = client.post("/companies", json={"name": "宁德时代"})
    assert r.status_code == 201
    r = client.post("/companies/宁德时代/split-pdf?year=2023")
    assert r.status_code == 404
    assert "未上传" in r.json()["detail"]


# ================== Service 层测试（用 mock PDF）====================


def test_split_service_locates_and_splits(client, tmp_env) -> None:
    """造一个 mock PDF，验证：
    1. 定位到 one-based page=other_pages+1（0-based = other_pages+1）
    2. 切分后财务报告 PDF 页数 = finance_pages + 1（含标题页）
    3. 切分后非财务 PDF 页数 = other_pages + 1（含目录页）
    4. DB 字段已写
    """
    from app.config import get_settings
    from app.db import session as db_session
    from app.models import AnnualReport, Company
    from app.services import pdf_split_service

    # 1) 建公司 + 写 mock PDF 到 REPORT_DATA_PATH/{公司}/pdf/original/
    other_pages = 30
    finance_pages = 80
    settings = get_settings()
    pdf_path = _make_mock_annual_report_pdf(
        settings.REPORT_DATA_PATH / "宁德时代" / "pdf" / "original" / "宁德时代2023年年度报告.pdf",
        other_pages=other_pages,
        finance_pages=finance_pages,
    )
    assert pdf_path.exists()

    db = db_session.SessionLocal()
    try:
        company = Company(name="宁德时代")
        db.add(company)
        db.commit()
        db.refresh(company)

        rel_pdf = str(pdf_path.resolve().relative_to(settings.REPORT_DATA_PATH.resolve()))
        ar = AnnualReport(
            company_id=company.id,
            year=2023,
            pdf_path=rel_pdf,
            source="test",
        )
        db.add(ar)
        db.commit()
        db.refresh(ar)
        ar_id = ar.id
    finally:
        db.close()

    # 2) 调 service
    db = db_session.SessionLocal()
    try:
        ar = db.query(AnnualReport).filter(AnnualReport.id == ar_id).first()
        result = pdf_split_service.split_annual_report_pdf(ar, "宁德时代")
    finally:
        db.close()

    # 3) 验证结果
    # PDF 布局：[0]目录 [1..other_pages]内容 [other_pages+1]标题 [..]财务内容
    assert result.finance_start_page == other_pages + 1  # 0-based
    assert result.total_pages == 1 + other_pages + 1 + finance_pages
    assert "第八节" in result.title_text
    assert "财务报告" in result.title_text

    # 4) 验证落盘（按 artifacts.md §1 阶段 2.1：新位置 = REPORT_DATA_PATH/{公司}/pdf/split/）
    assert result.finance_pdf_path.exists()
    assert result.other_pdf_path.exists()
    # 落盘位置应在 REPORT_DATA_PATH 下，不在 RAW_BASE_PATH/report/raw/ 下
    assert "report" not in result.finance_pdf_path.parts or (
        "report" in result.finance_pdf_path.parts
        and "raw" not in result.finance_pdf_path.parts
    ), f"finance_pdf_path 不应在 report/raw/ 下: {result.finance_pdf_path}"
    # 落盘路径的父目录应是 REPORT_DATA_PATH/{公司}/pdf/split
    assert result.finance_pdf_path.parent.name == "split"
    assert result.finance_pdf_path.parent.parent.name == "pdf"
    assert result.finance_pdf_path.parent.parent.parent.name == "宁德时代"
    fin_doc = fitz.open(str(result.finance_pdf_path))
    assert len(fin_doc) == finance_pages + 1
    fin_doc.close()
    oth_doc = fitz.open(str(result.other_pdf_path))
    assert len(oth_doc) == other_pages + 1
    oth_doc.close()

    # 5) 验证 DB 字段（相对路径按 REPORT_DATA_PATH 算，不带 raw/ 前缀）
    db = db_session.SessionLocal()
    try:
        ar2 = db.query(AnnualReport).filter(AnnualReport.id == ar_id).first()
        assert ar2.split_status == "done"
        assert ar2.finance_pdf_path is not None
        assert ar2.other_pdf_path is not None
        # 新规范：路径不带 "raw/" 前缀（无 report/raw/ 残留）
        assert not ar2.finance_pdf_path.startswith("raw/")
        assert not ar2.other_pdf_path.startswith("raw/")
        assert "report/raw" not in ar2.finance_pdf_path
        assert "report/raw" not in ar2.other_pdf_path
        # 子结构应符合 artifacts.md：宁德时代/pdf/split/..._财务报告.pdf
        assert ar2.finance_pdf_path.startswith("宁德时代/pdf/split/")
        assert ar2.other_pdf_path.startswith("宁德时代/pdf/split/")
        assert ar2.finance_pdf_path.endswith("_财务报告.pdf")
        assert ar2.other_pdf_path.endswith("_业务报告.pdf")
    finally:
        db.close()


def test_find_section_start_page_with_real_layout(tmp_env) -> None:
    """直接测 find_section_start_page 定位函数。

    验证：5 必选信号能精确定位到 one-based page=other_pages+1
    """
    from app.services.pdf_split_service import find_section_start_page

    other_pages = 25
    finance_pages = 60
    pdf_path = _make_mock_annual_report_pdf(
        tmp_env / "real_layout.pdf",
        other_pages=other_pages,
        finance_pages=finance_pages,
    )

    doc = fitz.open(str(pdf_path))
    try:
        start, title = find_section_start_page(doc)
    finally:
        doc.close()

    # PDF 布局：[0]目录 [1..other_pages]内容 [other_pages+1]标题 [..]财务内容
    # start = other_pages + 1（0-based）
    assert start == other_pages + 1
    assert "第八节" in title
    assert "财务报告" in title


def test_find_section_start_page_raises_when_no_match(tmp_env) -> None:
    """PDF 中无"第X节 财务报告"时，应抛 ValueError。"""
    from app.services.pdf_split_service import find_section_start_page

    # 造一个纯空白 PDF
    pdf_path = tmp_env / "empty.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(str(pdf_path))
    doc.close()

    doc = fitz.open(str(pdf_path))
    try:
        with pytest.raises(ValueError, match="未找到匹配"):
            find_section_start_page(doc)
    finally:
        doc.close()

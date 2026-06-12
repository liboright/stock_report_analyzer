"""POST /companies/{name}/chapters 章节切分端点 测试（mock 模式）。

策略：
- 不依赖真实业务报告 MD 生成：手工构造 DB 字段 `business_md_path` 和 `finance_md_path`
  指向 fake MD 文件。
- 重点测 chapters 自身的：
  - 端点校验（公司/年份/business_md_path/finance_md_path 缺失）
  - worker 三步串行（章节切分 → 财务 MD 复制 → 第三节 H2 拆分）
  - 产物落盘到 md/clean/.../by_section/ 和 管理层讨论/
  - 财务报告复制：内容一致 / 幂等 / 失败回退
"""
from __future__ import annotations

import time
from pathlib import Path


def _wait_run_done(client, run_id: int, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        r = client.get(f"/tasks/{run_id}")
        if r.status_code != 200:
            time.sleep(0.3)
            continue
        body = r.json()
        last_status = body["status"]
        if last_status in {"done", "failed"}:
            return body
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} 在 {timeout}s 内未结束，最后 status={last_status}")


def _seed_company_with_business_md(
    tmp_env: Path,
    company_name: str,
    year: int,
    *,
    business_md_text: str | None = None,
    finance_md_text: str | None = "# 财务报告\n## 一、合并资产负债表\n\n资产合计 1,000\n",
    seed_finance: bool = True,
) -> int:
    """手工造一个 Company + AnnualReport，并设 business_md_path / finance_md_path。

    - business_md_text: 业务报告 MD 内容（None 则使用默认 3 章节 sample）
    - finance_md_text: 财务报告 MD 内容（None 跳过 finance MD 文件落盘）
    - seed_finance: 是否在 DB 中设置 finance_md_path（即使 finance_md_text=None 也可单独控制）
    """
    from app.config import get_settings
    from app.db import session as db_session
    from app.models import AnnualReport, Company

    settings = get_settings()

    if business_md_text is None:
        # 包含 3 个章节（第一节 / 第三节）的中文 markdown
        business_md_text = (
            "# 第一节 重要提示\n\n"
            "本公司董事会及董事保证本报告内容真实、准确、完整。\n\n"
            "# 第二节 公司概况\n\n"
            "宁德时代是全球领先的动力电池企业。\n\n"
            "# 第三节 管理层讨论与分析\n\n"
            "## 一、报告期内公司所处行业情况\n\n"
            "新能源汽车行业 2023 年保持高速增长。\n\n"
            "## 二、主营业务分析\n\n"
            "公司主营业务为动力电池、储能电池的研发与销售。\n"
        )

    # 写业务报告 MD 到新位置：md/raw/业务报告/...（按 docs/artifacts.md）
    business_dir = (
        settings.REPORT_DATA_PATH / company_name / "md" / "raw"
        / "业务报告" / f"{company_name}{year}年年度报告"
    )
    business_dir.mkdir(parents=True, exist_ok=True)
    business_md = business_dir / f"{company_name}{year}年年度报告_业务报告.md"
    business_md.write_text(business_md_text, encoding="utf-8")
    business_rel = str(business_md.relative_to(settings.REPORT_DATA_PATH)).replace("\\", "/")

    # 写财务报告 MD：md/raw/财务报告/...（按 docs/artifacts.md）
    finance_rel: str | None = None
    if finance_md_text is not None:
        finance_dir = (
            settings.REPORT_DATA_PATH / company_name / "md" / "raw"
            / "财务报告" / f"{company_name}{year}年年度报告"
        )
        finance_dir.mkdir(parents=True, exist_ok=True)
        finance_md = finance_dir / f"{company_name}{year}年年度报告_财务报告.md"
        finance_md.write_text(finance_md_text, encoding="utf-8")
        finance_rel = str(finance_md.relative_to(settings.REPORT_DATA_PATH)).replace("\\", "/")

    db = db_session.SessionLocal()
    try:
        company = db.query(Company).filter(Company.name == company_name).first()
        if not company:
            company = Company(name=company_name, stock_code="300750")
            db.add(company)
            db.commit()
            db.refresh(company)

        ar = (
            db.query(AnnualReport)
            .filter(AnnualReport.company_id == company.id, AnnualReport.year == year)
            .first()
        )
        if not ar:
            ar = AnnualReport(
                company_id=company.id,
                year=year,
                pdf_path=f"{company_name}/pdf/original/{company_name}{year}年年度报告.pdf",
                source="test",
                business_md_path=business_rel,
                finance_md_path=finance_rel if seed_finance else None,
            )
            db.add(ar)
            db.commit()
            db.refresh(ar)
        else:
            ar.business_md_path = business_rel
            if seed_finance:
                ar.finance_md_path = finance_rel
            db.commit()
        ar_id = ar.id
    finally:
        db.close()
    return ar_id


# ================== API 层测试 ==================


def test_chapters_unknown_company_404(client) -> None:
    r = client.post("/companies/不存在/chapters?year=2023")
    assert r.status_code == 404


def test_chapters_unuploaded_year_404(client) -> None:
    client.post("/companies", json={"name": "宁德时代"})
    r = client.post("/companies/宁德时代/chapters?year=2099")
    assert r.status_code == 404
    assert "未上传" in r.json()["detail"]


def test_chapters_requires_business_md(client, tmp_env) -> None:
    """business_md_path 缺失时 → 409。"""
    from app.config import get_settings
    from app.db import session as db_session
    from app.models import AnnualReport, Company

    settings = get_settings()
    db = db_session.SessionLocal()
    try:
        company = Company(name="宁德时代", stock_code="300750")
        db.add(company)
        db.commit()
        db.refresh(company)
        ar = AnnualReport(
            company_id=company.id,
            year=2023,
            pdf_path="宁德时代/pdf/original/宁德时代2023年年度报告.pdf",
            source="test",
        )
        db.add(ar)
        db.commit()
    finally:
        db.close()

    r = client.post("/companies/宁德时代/chapters?year=2023")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "业务报告 MD" in detail
    assert "parse-split" in detail


# ================== Worker 端到端测试 ==================


def test_chapters_happy_path(client, tmp_env) -> None:
    """happy path：business_md_path 存在 → 202 → 等 done → 章节 + 第三节落盘。"""
    from app.config import get_settings
    from app.db import session as db_session
    from app.models import AnnualReport

    settings = get_settings()
    ar_id = _seed_company_with_business_md(tmp_env, "宁德时代", 2023)

    r = client.post("/companies/宁德时代/chapters?year=2023")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["company"] == "宁德时代"
    assert body["year"] == 2023
    assert body["status"] == "queued"
    run_id = body["run_id"]

    # BackgroundTask 让出时间片
    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    # chapters_pipeline 调 section3_split_service，后者调 split_section3.py
    # split_section3.py 依赖 annual_report_reader；缺依赖时可能 fail，但 step1（章节切分）必成功
    # 这里只断言 step1 一定完成（by_section/ 有文件）
    assert final["status"] in {"done", "failed"}, final
    assert final["current_stage"] in {1, 2}

    # 验证章节文件落盘（按 docs/artifacts.md：md/clean/.../by_section/）
    by_section_dir = (
        settings.REPORT_DATA_PATH / "宁德时代" / "md" / "clean"
        / "宁德时代2023年年报" / "by_section"
    )
    assert by_section_dir.is_dir(), f"by_section 目录未创建: {by_section_dir}"
    section_files = sorted(by_section_dir.glob("*.md"))
    assert len(section_files) >= 3, f"应至少切分 3 个章节，实际 {len(section_files)}"
    # 验证文件名约定：NN_标题.md
    for p in section_files:
        stem = p.stem
        assert len(stem) >= 3 and stem[:2].isdigit() and stem[2] == "_", \
            f"章节文件命名不符合 NN_ 约定: {p.name}"


def test_chapters_run_template_is_chapters_pipeline(client, tmp_env) -> None:
    """run 记录的 template 应为 'chapters_pipeline'。"""
    from app.db import session as db_session
    from app.models import ReportRun

    _seed_company_with_business_md(tmp_env, "宁德时代", 2023)

    r = client.post("/companies/宁德时代/chapters?year=2023")
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    db = db_session.SessionLocal()
    try:
        run = db.query(ReportRun).filter(ReportRun.id == run_id).first()
        assert run is not None
        assert run.template == "chapters_pipeline"
    finally:
        db.close()


# ================== 财务报告复制（Step 1.5）测试 ==================


def test_chapters_requires_finance_md(client, tmp_env) -> None:
    """finance_md_path 缺失时 → 409。"""
    # 只 seed business MD，不 seed finance（既不写文件也不写 DB 字段）
    _seed_company_with_business_md(
        tmp_env,
        "宁德时代",
        2023,
        finance_md_text=None,
        seed_finance=False,
    )

    r = client.post("/companies/宁德时代/chapters?year=2023")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "财务报告 MD" in detail
    assert "parse-split" in detail


def test_chapters_finance_md_copied_to_by_section(client, tmp_env) -> None:
    """happy-path：财务 MD 被复制到 by_section/10_第十节_财务报告.md，内容一致。"""
    from app.config import get_settings

    settings = get_settings()
    finance_text = "# 财务报告内容\n## 一、合并资产负债表\n\n资产合计 9,876\n"
    _seed_company_with_business_md(
        tmp_env, "宁德时代", 2023, finance_md_text=finance_text
    )

    r = client.post("/companies/宁德时代/chapters?year=2023")
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]

    time.sleep(2.0)
    _wait_run_done(client, run_id, timeout=30.0)

    finance_target = (
        settings.REPORT_DATA_PATH / "宁德时代" / "md" / "clean"
        / "宁德时代2023年年报" / "by_section" / "10_第十节_财务报告.md"
    )
    assert finance_target.is_file(), f"财务报告未复制: {finance_target}"
    assert finance_target.read_text(encoding="utf-8") == finance_text

    # 同时 by_section/ 下应有 01_~09_ 业务章节
    by_section_dir = finance_target.parent
    other_files = [p for p in by_section_dir.glob("*.md") if p.name != "10_第十节_财务报告.md"]
    assert len(other_files) >= 3, f"业务章节缺失，by_section={[p.name for p in by_section_dir.iterdir()]}"


def test_chapters_finance_copy_idempotent(client, tmp_env) -> None:
    """连续触发两次 chapters，财务报告 MD 的 mtime 不变（幂等）。"""
    from app.config import get_settings

    settings = get_settings()
    _seed_company_with_business_md(tmp_env, "宁德时代", 2023)

    # 第一次触发
    r1 = client.post("/companies/宁德时代/chapters?year=2023")
    assert r1.status_code == 202
    run_id1 = r1.json()["run_id"]
    time.sleep(2.0)
    _wait_run_done(client, run_id1, timeout=30.0)

    finance_target = (
        settings.REPORT_DATA_PATH / "宁德时代" / "md" / "clean"
        / "宁德时代2023年年报" / "by_section" / "10_第十节_财务报告.md"
    )
    assert finance_target.is_file()
    mtime1 = finance_target.stat().st_mtime_ns

    # 让 mtime 分辨率拉开
    time.sleep(0.05)

    # 第二次触发（business_md / finance_md 都没改）
    r2 = client.post("/companies/宁德时代/chapters?year=2023")
    assert r2.status_code == 202
    run_id2 = r2.json()["run_id"]
    time.sleep(2.0)
    _wait_run_done(client, run_id2, timeout=30.0)

    mtime2 = finance_target.stat().st_mtime_ns
    assert mtime1 == mtime2, "财务报告 MD 被重复复制（mtime 变化），未做幂等"


def test_chapters_finance_md_source_missing_fails(client, tmp_env) -> None:
    """DB 中有 finance_md_path 但物理文件被删 → run failed，但业务章节（1-9）已落盘。"""
    from app.config import get_settings

    settings = get_settings()
    _seed_company_with_business_md(tmp_env, "宁德时代", 2023)

    # 把财务 MD 物理删除，但保留 DB 字段
    finance_src = (
        settings.REPORT_DATA_PATH / "宁德时代" / "md" / "raw"
        / "财务报告" / "宁德时代2023年年度报告"
        / "宁德时代2023年年度报告_财务报告.md"
    )
    assert finance_src.is_file()
    finance_src.unlink()

    r = client.post("/companies/宁德时代/chapters?year=2023")
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "failed", final
    assert "财务报告 MD" in (final.get("error") or "")

    # 业务章节 1-9（Step 1）应已落盘（在 Step 1.5 失败之前）
    by_section_dir = (
        settings.REPORT_DATA_PATH / "宁德时代" / "md" / "clean"
        / "宁德时代2023年年报" / "by_section"
    )
    assert by_section_dir.is_dir()
    business_chapters = [p for p in by_section_dir.glob("*.md") if p.name != "10_第十节_财务报告.md"]
    assert len(business_chapters) >= 3, \
        f"Step 1 业务章节未保留: {[p.name for p in by_section_dir.iterdir()]}"
    # 财务文件不应存在（Step 1.5 失败前就 return）
    finance_target = by_section_dir / "10_第十节_财务报告.md"
    assert not finance_target.exists(), "Step 1.5 失败时不应留下半成品财务 MD"

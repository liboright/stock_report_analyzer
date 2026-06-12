"""「切分+解析」组合流水线测试（mock 模式，不调真实 MinerU）。

策略：
- 不依赖 pdf_split_service 端到端：手工构造 DB 字段 `split_status='done'` +
  `finance_pdf_path` / `other_pdf_path` 指向 fake PDF 文件。
- 这样聚焦测 parse_split 自身的：
  - 端点校验（split 状态、404 边界）
  - worker 两步串行 + 进度事件
  - partial 状态（业务成功 / 财务失败）
  - 断点续跑
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest


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


def _make_fake_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 fake report for parse-split test")
    return path


def _seed_company_with_split(
    tmp_env: Path,
    company_name: str,
    year: int,
    *,
    split_status: str = "done",
    business_md: str | None = None,
    finance_md: str | None = None,
    parse_split_status: str | None = None,
) -> int:
    """手工造一个 Company + AnnualReport，并设 split 状态、fake PDF、optional MD。

    Returns: AnnualReport.id
    """
    from app.config import get_settings
    from app.db import session as db_session
    from app.models import AnnualReport, Company

    settings = get_settings()

    # fake 原 PDF（split 校验需要文件存在；mock 模式不读内容）
    orig_pdf = _make_fake_pdf(
        settings.REPORT_DATA_PATH / company_name / "pdf" / "original" / f"{company_name}{year}年年度报告.pdf"
    )
    # fake split 后的两份 PDF
    business_pdf_rel = f"{company_name}/pdf/split/{company_name}{year}年年度报告_业务报告.pdf"
    finance_pdf_rel = f"{company_name}/pdf/split/{company_name}{year}年年度报告_财务报告.pdf"
    _make_fake_pdf(settings.REPORT_DATA_PATH / business_pdf_rel)
    _make_fake_pdf(settings.REPORT_DATA_PATH / finance_pdf_rel)

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
                pdf_path=str(orig_pdf.resolve().relative_to(settings.REPORT_DATA_PATH.resolve())).replace("\\", "/"),
                source="test",
            )
            db.add(ar)
            db.commit()
            db.refresh(ar)

        # 强制设置 split 字段（覆盖已有）
        ar.split_status = split_status
        ar.other_pdf_path = business_pdf_rel
        ar.finance_pdf_path = finance_pdf_rel
        ar.business_md_path = business_md
        ar.finance_md_path = finance_md
        ar.parse_split_status = parse_split_status
        db.commit()
        ar_id = ar.id
    finally:
        db.close()
    return ar_id


def _md_path_for(company_name: str, year: int, kind: str) -> Path:
    """返回 parse_split_pipeline 约定的 mock MD 落盘路径。"""
    from app.config import get_settings

    settings = get_settings()
    folder = "业务报告" if kind == "business" else "财务报告"
    suffix = "业务报告" if kind == "business" else "财务报告"
    return (
        settings.REPORT_DATA_PATH
        / company_name
        / "md"
        / "raw"
        / folder
        / f"{company_name}{year}年年度报告"
        / f"{company_name}{year}年年度报告_{suffix}.md"
    )


# ================== API 层测试 ==================


def test_parse_split_unknown_company_404(client) -> None:
    r = client.post("/companies/不存在/parse-split?year=2023")
    assert r.status_code == 404


def test_parse_split_unuploaded_year_404(client) -> None:
    r = client.post("/companies", json={"name": "宁德时代"})
    assert r.status_code == 201
    r = client.post("/companies/宁德时代/parse-split?year=2099")
    assert r.status_code == 404
    assert "未上传" in r.json()["detail"]


def test_parse_split_requires_split_first(client, tmp_env) -> None:
    """split_status != 'done' 时 → 409。"""
    _seed_company_with_split(tmp_env, "宁德时代", 2023, split_status="pending")

    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "split-pdf" in detail
    assert "pending" in detail


# ================== Worker 端到端测试 ==================


def test_parse_split_happy_path_mock(client, tmp_env) -> None:
    """happy path：split_status=done + use_mock=True → 202 → 等 done → 两份 MD 落盘 + 业务 MD 标注完成。"""
    from app.config import get_settings
    from app.db import session as db_session
    from app.models import AnnualReport

    settings = get_settings()
    ar_id = _seed_company_with_split(tmp_env, "宁德时代", 2023)

    # 触发
    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["company"] == "宁德时代"
    assert body["year"] == 2023
    assert body["use_mock"] is True
    assert body["status"] == "queued"
    assert body["business_pdf"].endswith("业务报告.pdf")
    assert body["finance_pdf"].endswith("财务报告.pdf")
    # 响应模型加了 annotation_status 字段（透传 DB 当前值；空为 None）
    assert "annotation_status" in body
    run_id = body["run_id"]

    # BackgroundTask 让出时间片
    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "done", final
    assert final["current_stage"] == 2
    assert final["error"] is None
    assert final["final_path"] is not None
    assert "业务报告" in final["final_path"]

    # 验证 DB
    db = db_session.SessionLocal()
    try:
        ar = db.query(AnnualReport).filter(AnnualReport.id == ar_id).first()
        assert ar.parse_split_status == "done"
        assert ar.business_md_path is not None
        assert ar.finance_md_path is not None
        # 新位置：md/raw/业务报告/... / md/raw/财务报告/...（按 artifacts.md §1）
        assert "raw" in ar.business_md_path
        assert "raw" in ar.finance_md_path
        assert "业务报告" in ar.business_md_path
        assert "财务报告" in ar.finance_md_path
        # 业务 MD 标注状态（mock 模板已用正确 # 数，count 可能为 0，但状态必须写）
        assert ar.annotation_status == "annotated"
        assert ar.annotated_at is not None
    finally:
        db.close()

    # 验证文件（按 REPORT_DATA_PATH 拼）
    business_md = settings.REPORT_DATA_PATH / ar.business_md_path  # type: ignore[operator]
    finance_md = settings.REPORT_DATA_PATH / ar.finance_md_path  # type: ignore[operator]
    assert business_md.exists()
    assert finance_md.exists()
    # mock 模板内容（pdf_parse_service._mock_md_content）有"第十节 财务报告"
    assert "第十节" in business_md.read_text(encoding="utf-8")
    assert "第十节" in finance_md.read_text(encoding="utf-8")

    # parse_pdf_to_md 在 use_mock 模式下不写 full.md（仅真实 MinerU 才写 full.md）


def test_parse_split_business_fails(client, tmp_env, monkeypatch) -> None:
    """业务报告 MD 失败 → run failed，DB 两个 md_path 都 NULL。"""
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service

    ar_id = _seed_company_with_split(tmp_env, "宁德时代", 2023)

    def fake_batch(items, *, use_mock=False):
        raise RuntimeError("业务 MinerU 失败（测试 mock）")

    monkeypatch.setattr(pdf_parse_service, "parse_pdfs_to_md_batch", fake_batch)

    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "failed", final
    assert "失败" in final["error"]
    assert final["current_stage"] == 1

    # 验证 DB：两个 md_path 都还是 NULL，parse_split_status 保持 None
    db = db_session.SessionLocal()
    try:
        ar = db.query(AnnualReport).filter(AnnualReport.id == ar_id).first()
        assert ar.business_md_path is None
        assert ar.finance_md_path is None
        assert ar.parse_split_status is None  # 业务失败前没写 business_done
    finally:
        db.close()


def test_parse_split_business_done_finance_fails(client, tmp_env, monkeypatch) -> None:
    """业务成功 + 财务失败 → run failed，但 partial 状态写入 DB（业务 MD 已落盘）。"""
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service

    ar_id = _seed_company_with_split(tmp_env, "宁德时代", 2023)

    real_batch = pdf_parse_service.parse_pdfs_to_md_batch

    def fake_batch(items, *, use_mock=False):
        """第一个 item（业务）成功；第二个 item（财务）抛错。

        业务 MD 由 fake_batch 自己写盘（模拟部分成功），财务未写。
        """
        results = {}
        for i, (pdf, md, did) in enumerate(items):
            if i == 0:
                # 业务成功：写盘 + 返回
                content = real_batch([(pdf, md, did)], use_mock=use_mock)[did]
                results[did] = content
            else:
                # 财务失败
                raise RuntimeError("财务 MinerU 配额耗尽（测试 mock）")
        return results

    monkeypatch.setattr(pdf_parse_service, "parse_pdfs_to_md_batch", fake_batch)

    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "failed", final
    assert "财务" in final["error"]
    assert "已落盘 1/2" in final["error"]
    assert final["current_stage"] == 1

    # 验证 DB：业务 MD 路径有值，财务没有，parse_split_status='business_done'
    db = db_session.SessionLocal()
    try:
        ar = db.query(AnnualReport).filter(AnnualReport.id == ar_id).first()
        assert ar.business_md_path is not None
        assert ar.finance_md_path is None
        assert ar.parse_split_status == "business_done"
    finally:
        db.close()


def test_parse_split_batch_collects_all_incomplete_years(client, tmp_env, monkeypatch) -> None:
    """触发单年 parse-split，默认 include_other_years=True → 同公司多年未完成文件一次入 batch。"""
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service

    for year in (2023, 2024, 2025):
        _seed_company_with_split(tmp_env, "宁德时代", year)

    captured: dict = {"calls": 0, "items": []}
    real_batch = pdf_parse_service.parse_pdfs_to_md_batch

    def counting_batch(items, *, use_mock=False):
        captured["calls"] += 1
        captured["items"] = list(items)
        return real_batch(items, use_mock=use_mock)

    monkeypatch.setattr(pdf_parse_service, "parse_pdfs_to_md_batch", counting_batch)

    r = client.post("/companies/宁德时代/parse-split?year=2025&use_mock=true")
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "done", final

    assert captured["calls"] == 1
    data_ids = [did for _pdf, _md, did in captured["items"]]
    assert data_ids == [
        "2025_business", "2025_finance",
        "2024_business", "2024_finance",
        "2023_business", "2023_finance",
    ]

    db = db_session.SessionLocal()
    try:
        reports = db.query(AnnualReport).order_by(AnnualReport.year).all()
        assert [r.year for r in reports] == [2023, 2024, 2025]
        assert all(r.parse_split_status == "done" for r in reports)
        assert all(r.business_md_path for r in reports)
        assert all(r.finance_md_path for r in reports)
    finally:
        db.close()


def test_parse_split_include_other_years_false_only_batches_trigger_year(client, tmp_env, monkeypatch) -> None:
    """include_other_years=False → 只解析触发年份，其他年份保持未完成。"""
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service

    for year in (2023, 2024, 2025):
        _seed_company_with_split(tmp_env, "宁德时代", year)

    captured: dict = {"items": []}
    real_batch = pdf_parse_service.parse_pdfs_to_md_batch

    def counting_batch(items, *, use_mock=False):
        captured["items"] = list(items)
        return real_batch(items, use_mock=use_mock)

    monkeypatch.setattr(pdf_parse_service, "parse_pdfs_to_md_batch", counting_batch)

    r = client.post("/companies/宁德时代/parse-split?year=2024&use_mock=true&include_other_years=false")
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "done", final

    data_ids = [did for _pdf, _md, did in captured["items"]]
    assert data_ids == ["2024_business", "2024_finance"]

    db = db_session.SessionLocal()
    try:
        by_year = {r.year: r for r in db.query(AnnualReport).all()}
        assert by_year[2024].parse_split_status == "done"
        assert by_year[2023].parse_split_status is None
        assert by_year[2025].parse_split_status is None
    finally:
        db.close()


def test_parse_split_skips_files_with_existing_md_on_disk(client, tmp_env, monkeypatch) -> None:
    """DB 未写 md_path 但磁盘已有 MD → 重提时按 output_md_path.exists() 跳过，不重复入 batch。"""
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service

    _seed_company_with_split(tmp_env, "宁德时代", 2023)
    existing_business_md = _md_path_for("宁德时代", 2023, "business")
    existing_business_md.parent.mkdir(parents=True, exist_ok=True)
    existing_business_md.write_text("# 第一节 已存在的业务 MD\n", encoding="utf-8")

    captured: dict = {"items": []}
    real_batch = pdf_parse_service.parse_pdfs_to_md_batch

    def counting_batch(items, *, use_mock=False):
        captured["items"] = list(items)
        return real_batch(items, use_mock=use_mock)

    monkeypatch.setattr(pdf_parse_service, "parse_pdfs_to_md_batch", counting_batch)

    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "done", final

    data_ids = [did for _pdf, _md, did in captured["items"]]
    assert data_ids == ["2023_finance"]
    assert existing_business_md.read_text(encoding="utf-8") == "# 第一节 已存在的业务 MD\n"

    db = db_session.SessionLocal()
    try:
        ar = db.query(AnnualReport).filter(AnnualReport.year == 2023).first()
        assert ar.parse_split_status == "done"
        assert ar.business_md_path is None  # 只靠磁盘存在跳过，不回填 DB
        assert ar.finance_md_path is not None
    finally:
        db.close()


def test_parse_split_resume_after_business_done(client, tmp_env, monkeypatch) -> None:
    """断点续跑：业务 MD 已落盘（不论 DB 状态）→ 跳过业务，只跑财务。"""
    from app.config import get_settings
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service

    settings = get_settings()
    data_base = settings.REPORT_DATA_PATH
    # 预置业务 MD 文件已存在（新位置：md/raw/业务报告/...）
    business_dir = data_base / "宁德时代" / "md" / "raw" / "业务报告" / "宁德时代2023年年度报告"
    business_dir.mkdir(parents=True, exist_ok=True)
    business_md = business_dir / "宁德时代2023年年度报告_业务报告.md"
    business_md.write_text("# 第一节 重要提示（已有内容，续跑应跳过）\n", encoding="utf-8")
    ar_id = _seed_company_with_split(
        tmp_env, "宁德时代", 2023,
        business_md=str(business_md.relative_to(data_base)).replace("\\", "/"),
        parse_split_status="business_done",
    )

    # 监听 parse_pdfs_to_md_batch 调用次数 + 接收的 items 列表
    captured: dict = {"calls": 0, "items": []}
    real_batch = pdf_parse_service.parse_pdfs_to_md_batch

    def counting_batch(items, *, use_mock=False):
        captured["calls"] += 1
        captured["items"] = list(items)
        return real_batch(items, use_mock=use_mock)

    monkeypatch.setattr(pdf_parse_service, "parse_pdfs_to_md_batch", counting_batch)

    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "done", final

    # 只调了 1 次 batch，items 列表只含财务
    assert captured["calls"] == 1
    assert len(captured["items"]) == 1
    assert captured["items"][0][2] == "2023_finance"  # data_id

    # 业务 MD 路径**不变**（续跑不会重新写）
    db = db_session.SessionLocal()
    try:
        ar = db.query(AnnualReport).filter(AnnualReport.id == ar_id).first()
        # business_md_path 保持预置值（续跑不覆盖）
        assert ar.parse_split_status == "done"
        assert ar.finance_md_path is not None
        # 业务 MD 文件内容**保持**预置的"第一节 重要提示（已有内容，续跑应跳过）"
        # （worker 不会重新写）
        actual = business_md.read_text(encoding="utf-8")
        assert "续跑应跳过" in actual
    finally:
        db.close()


# ================== 阶段 2.2.5 业务 MD 标注 sub-stage 测试 ==================


def test_annotation_rewrites_wrong_hash_counts(client, tmp_env, monkeypatch) -> None:
    """业务 MD 标注：mock 模板的 # 数原本是错的（全部用 #），标注后应改写为正确层级。"""
    from app.config import get_settings
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service

    ar_id = _seed_company_with_split(tmp_env, "宁德时代", 2023)

    # 覆盖 _mock_md_content 返回"全部 #"的版本，模拟 MinerU 真实输出
    def fake_mock(pdf_path):
        return (
            "# 第一节 重要提示\n"
            "# 第二节 公司简介\n"
            "# 第三节 管理层讨论与分析\n"
            "# 一、报告期内公司所处行业情况\n"
            "# 二、主要业务\n"
            "# 三、报告期内公司从事的业务情况\n"
            "# （一）主营业务分析\n"
            "# 1、销售模式\n"
            "# （1）境内销售\n"
            "# 第四节 公司治理\n"
        )

    monkeypatch.setattr(pdf_parse_service, "_mock_md_content", fake_mock)

    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "done", final

    # 验证 DB
    db = db_session.SessionLocal()
    try:
        ar = db.query(AnnualReport).filter(AnnualReport.id == ar_id).first()
        assert ar.annotation_status == "annotated"
        assert ar.annotated_at is not None
    finally:
        db.close()

    # 验证业务 MD 文件已被原地改写为正确 # 数
    settings = get_settings()
    business_md = settings.REPORT_DATA_PATH / ar.business_md_path  # type: ignore[operator]
    text = business_md.read_text(encoding="utf-8")
    # L1 第X节 → 1 个 #
    assert "# 第一节 重要提示" in text
    # L2 一、 → 2 个 #
    assert "## 一、报告期内公司所处行业情况" in text
    assert "## 三、报告期内公司从事的业务情况" in text
    # L3 （一） → 3 个 #
    assert "### （一）主营业务分析" in text
    # L4 1、 → 4 个 #
    assert "#### 1、销售模式" in text
    # L5 （1） → 5 个 #
    assert "##### （1）境内销售" in text


def test_annotation_does_not_touch_finance_md(client, tmp_env, monkeypatch) -> None:
    """标注算法**只**改写业务 MD；财务 MD 保持 MinerU 原始状态。"""
    from app.config import get_settings
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service

    ar_id = _seed_company_with_split(tmp_env, "宁德时代", 2023)

    # 财务 mock 返回纯 # 行（会被识别为标题）—— 但 worker 不应对财务跑标注
    def fake_mock(pdf_path):
        return "# 一、这是财务的标题\n"

    monkeypatch.setattr(pdf_parse_service, "_mock_md_content", fake_mock)

    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    run_id = r.json()["run_id"]
    time.sleep(2.0)
    _wait_run_done(client, run_id, timeout=30.0)

    db = db_session.SessionLocal()
    try:
        ar = db.query(AnnualReport).filter(AnnualReport.id == ar_id).first()
    finally:
        db.close()

    settings = get_settings()
    finance_md = settings.REPORT_DATA_PATH / ar.finance_md_path  # type: ignore[operator]
    # 财务 MD 保持 MinerU 原始 1 个 #（未被改写为 2 个 #）
    text = finance_md.read_text(encoding="utf-8")
    assert "# 一、这是财务的标题" in text
    assert "## 一、这是财务的标题" not in text


def test_resume_after_annotation_done_skips_annotate(client, tmp_env, monkeypatch) -> None:
    """断点续跑：annotation_status='annotated' → 跳过 Stage 1.5，但仍跑 Stage 2（如果 finance_md 缺失）。"""
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service

    # 预置 business_done + annotation_status=annotated，财务 MD 不存在
    ar_id = _seed_company_with_split(
        tmp_env, "宁德时代", 2023,
        parse_split_status="business_done",
    )
    business_md = _md_path_for("宁德时代", 2023, "business")
    business_md.parent.mkdir(parents=True, exist_ok=True)
    business_md.write_text("# 第一节 重要提示\n# 一、x\n", encoding="utf-8")
    # 手动标 annotation_status='annotated'
    db = db_session.SessionLocal()
    try:
        ar = db.query(AnnualReport).filter(AnnualReport.id == ar_id).first()
        ar.annotation_status = "annotated"
        db.commit()
    finally:
        db.close()

    # parse_pdfs_to_md_batch 只应被调 1 次（财务）
    captured: dict = {"calls": 0, "items": []}
    real_batch = pdf_parse_service.parse_pdfs_to_md_batch

    def counting_batch(items, *, use_mock=False):
        captured["calls"] += 1
        captured["items"] = list(items)
        return real_batch(items, use_mock=use_mock)

    monkeypatch.setattr(pdf_parse_service, "parse_pdfs_to_md_batch", counting_batch)

    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    run_id = r.json()["run_id"]
    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "done", final
    # 只调了 1 次 batch，items 列表只含财务
    assert captured["calls"] == 1
    assert len(captured["items"]) == 1
    assert captured["items"][0][2] == "2023_finance"  # data_id
    # 业务 MD 文件未变（续跑不重写）
    assert "第一节 重要提示" in business_md.read_text(encoding="utf-8")


def test_annotation_failure_keeps_parsed_state(client, tmp_env, monkeypatch) -> None:
    """标注 sub-stage 抛错 → run failed，已解析的业务/财务状态保留。"""
    from app.db import session as db_session
    from app.models import AnnualReport
    from app.services import pdf_parse_service
    from app.services import heading_annotate_service

    _seed_company_with_split(tmp_env, "宁德时代", 2023)

    def fake_annotate(self, md_path):
        raise RuntimeError("标注服务爆炸（测试 mock）")

    monkeypatch.setattr(
        heading_annotate_service.ContextAwareHeadingAnnotator,
        "annotate_business_md",
        fake_annotate,
    )

    r = client.post("/companies/宁德时代/parse-split?year=2023&use_mock=true")
    run_id = r.json()["run_id"]
    time.sleep(2.0)
    final = _wait_run_done(client, run_id, timeout=30.0)
    assert final["status"] == "failed", final
    assert "标注" in final["error"]
    assert final["current_stage"] == 1

    # DB 状态：业务/财务 MD 已落盘，parse_split_status 已推进到 done，annotation_status 仍为 NULL
    db = db_session.SessionLocal()
    try:
        ars = db.query(AnnualReport).all()
        assert len(ars) >= 1
        target = next((a for a in ars if a.year == 2023), ars[0])
        assert target.business_md_path is not None
        assert target.finance_md_path is not None
        assert target.parse_split_status == "done"
        # annotation_status 仍为 NULL（标注失败前没写）
        assert target.annotation_status is None
    finally:
        db.close()

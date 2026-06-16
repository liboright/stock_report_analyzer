"""阶段 3.x 跨年度表格合并 worker 测试。

策略：
- 直接调 ``run_table_merge_pipeline``（不走 HTTP，避免 BackgroundTask 异步时序问题）
- 在 tmp_env 下铺 2 年 CSV fixtures → 创建 Company + ReportRun → 调 worker
- 验证：
  - 强路径：long+wide CSV 落盘、sidecar JSON 写入、ReportRun.status='done'
  - 弱路径 + skill 成功：mock claude_skill_runner → long/wide 落盘 + pending_skill=False
  - 弱路径 + skill 失败：mock raise ClaudeSkillError → pending_skill=False + skill_failures 有值
  - 终态：final_path 指向 sidecar，current_stage=4
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from app.config import get_settings
from app.db import session as db_session
from app.models import Company, ReportRun
from app.services.claude_skill_runner import ClaudeSkillError, SkillRunResult
from app.services.md_table_parser import (
    TableInfo,
    UnitInfo,
    YearMapping,
)
from app.services.tables_extract_service import write_table_csv
from app.workers import table_merge_pipeline as pipeline


# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def fresh_db(tmp_env: Path):
    """基于 tmp_env 但重建 engine + create_all（避免复用其他测试残留的 engine）。"""
    s = get_settings()
    s.ensure_runtime_dirs()
    db_session.engine.dispose()
    db_session.engine = db_session.create_engine(
        s.db_url, future=True, connect_args={"check_same_thread": False}
    )
    db_session.SessionLocal = db_session.sessionmaker(
        bind=db_session.engine, autoflush=False, autocommit=False, future=True
    )
    db_session.init_db()
    yield tmp_env
    db_session.engine.dispose()


# ============================================================
# helpers
# ============================================================


def _make_table_info(
    title: str, data_rows: List[List[str]], headers: List[str], unit: str = "千元"
) -> TableInfo:
    return TableInfo(
        source_path=Path("D:/dummy.md"),
        table_index=0,
        report_year=2025,
        title=title,
        title_level=3,
        unit=UnitInfo(raw_lines=[unit], primary=unit, from_column_brackets={}),
        headers=[headers],
        data_grid=data_rows,
        row_count=len(data_rows),
        col_count=len(headers),
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


def _seed_csv(
    base: Path,
    company: str,
    year: int,
    stem: str,
    title: str,
    headers: List[str],
    rows: List[List[str]],
) -> Path:
    per_year = base / company / "md" / "clean" / f"{company}{year}年年报" / "table" / stem
    per_year.mkdir(parents=True, exist_ok=True)
    p = per_year / f"{title}.csv"
    t = _make_table_info(title, rows, headers)
    write_table_csv(p, t, f"{stem}.md", 1, 1)
    return p


def _create_company(db, name: str) -> int:
    c = db.query(Company).filter(Company.name == name).first()
    if c:
        return c.id
    c = Company(name=name)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c.id


def _create_run(db, company_id: int) -> int:
    r = ReportRun(
        company_id=company_id, year=None, template="table_merge", status="queued"
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r.id


def _make_skill_outputs(base: Path, company: str, group_key: str):
    """模拟 stage2 skill 落盘：写出 long + wide。"""
    from app.services.claude_skill_runner import _expected_table_merge_outputs

    long_p, wide_p = _expected_table_merge_outputs(
        company=company, group_key=group_key, settings=get_settings()
    )
    long_p.parent.mkdir(parents=True, exist_ok=True)
    long_p.write_text(
        "# source_md_stem, 05_五\n_row_type,year,source_md_stem,subject,metric,value,unit\n"
        "data,2024,05_五,营收,金额,1000,千元\n",
        encoding="utf-8-sig",
    )
    wide_p.write_text(
        "# source_md_stem, 05_五\nsubject,金额_2024,金额_2025\n营收,1000,1100\n",
        encoding="utf-8-sig",
    )
    return long_p, wide_p


# ============================================================
# 1. 强路径（不走 skill）
# ============================================================


def test_worker_strong_path_writes_long_wide_and_sidecar(fresh_db: Path):
    base = fresh_db / "report_data"
    company = "宁德时代"
    for y in (2024, 2025):
        _seed_csv(
            base, company, y, "05_五", "营业收入",
            headers=["项目", "金额"],
            rows=[
                ["营收", "1000" if y == 2024 else "1100"],
                ["成本", "500" if y == 2024 else "550"],
            ],
        )

    db = db_session.SessionLocal()
    try:
        cid = _create_company(db, company)
        run_id = _create_run(db, cid)
    finally:
        db.close()

    pipeline.run_table_merge_pipeline(
        run_id=run_id, company_id=cid, years=[2024, 2025], force=False
    )

    sidecar = base / company / "md" / "research_file" / "table" / f".merge_run_{run_id}.json"
    assert sidecar.exists()
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc["company"] == company
    assert sc["strong_count"] == 1
    assert sc["weak_count"] == 0
    assert sc["total_groups"] == 1
    assert sc["groups"][0]["status"] == "strong"
    assert sc["groups"][0]["long_csv"].endswith("_long.csv")
    assert sc["groups"][0]["wide_csv"].endswith("_wide.csv")

    assert (base / sc["groups"][0]["long_csv"]).exists()
    assert (base / sc["groups"][0]["wide_csv"]).exists()

    db = db_session.SessionLocal()
    try:
        run = db.get(ReportRun, run_id)
        assert run is not None
        assert run.status == "done"
        assert run.current_stage == 4
        assert run.final_path is not None
        assert run.final_path.endswith(f".merge_run_{run_id}.json")
        assert run.finished_at is not None
    finally:
        db.close()


# ============================================================
# 2. 弱路径 + skill 成功（mock claude_skill_runner）
# ============================================================


def test_worker_weak_skill_success_writes_long_wide(
    fresh_db: Path, monkeypatch
):
    base = fresh_db / "report_data"
    company = "宁德时代"
    for y, hdrs, rows in [
        (2024, ["项目", "金额", "占比"], [["营收", "1000", "60%"]]),
        (2025, ["科目", "营业收入", "成本占比"], [["营收", "1100", "65%"]]),
    ]:
        _seed_csv(base, company, y, "05_五", "营业收入", headers=hdrs, rows=rows)

    # mock skill runner：先落 long+wide 再返回 SkillRunResult
    def fake_skill(skill, company, group_key, years, csv_paths, **kwargs):
        long_p, wide_p = _make_skill_outputs(base, company, group_key)
        return SkillRunResult(
            skill=skill, company=company, year=None, output_path=long_p,
            returncode=0, stdout="ok", stderr="", elapsed_seconds=0.5,
            output_paths=[long_p, wide_p],
        )

    monkeypatch.setattr(
        pipeline.claude_skill_runner, "run_skill_for_table_merge", fake_skill
    )

    db = db_session.SessionLocal()
    try:
        cid = _create_company(db, company)
        run_id = _create_run(db, cid)
    finally:
        db.close()

    pipeline.run_table_merge_pipeline(
        run_id=run_id, company_id=cid, years=[2024, 2025]
    )

    sidecar = base / company / "md" / "research_file" / "table" / f".merge_run_{run_id}.json"
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc["strong_count"] == 0
    assert sc["weak_count"] == 1
    assert sc["skill_failures"] == []

    # skill 落盘：long+wide 都在
    weak_grp = [g for g in sc["groups"] if g["status"] == "weak"][0]
    assert weak_grp["pending_skill"] is False
    assert weak_grp["long_csv"] is not None
    assert weak_grp["wide_csv"] is not None
    assert (base / weak_grp["long_csv"]).exists()
    assert (base / weak_grp["wide_csv"]).exists()
    assert "skill 对齐完成" in weak_grp["reason"]

    # 终态 done，无 error
    db = db_session.SessionLocal()
    try:
        run = db.get(ReportRun, run_id)
        assert run.status == "done"
        assert run.error is None
    finally:
        db.close()


# ============================================================
# 3. 弱路径 + skill 失败（mock raise）
# ============================================================


def test_worker_weak_skill_failure_marks_skill_failures(
    fresh_db: Path, monkeypatch
):
    base = fresh_db / "report_data"
    company = "宁德时代"
    for y, hdrs, rows in [
        (2024, ["项目", "金额", "占比"], [["营收", "1000", "60%"]]),
        (2025, ["科目", "营业收入", "成本占比"], [["营收", "1100", "65%"]]),
    ]:
        _seed_csv(base, company, y, "05_五", "营业收入", headers=hdrs, rows=rows)

    def fake_skill_fail(skill, company, group_key, years, csv_paths, **kwargs):
        raise ClaudeSkillError("claude CLI 退出码 1")

    monkeypatch.setattr(
        pipeline.claude_skill_runner, "run_skill_for_table_merge", fake_skill_fail
    )

    db = db_session.SessionLocal()
    try:
        cid = _create_company(db, company)
        run_id = _create_run(db, cid)
    finally:
        db.close()

    pipeline.run_table_merge_pipeline(
        run_id=run_id, company_id=cid, years=[2024, 2025]
    )

    sidecar = base / company / "md" / "research_file" / "table" / f".merge_run_{run_id}.json"
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc["strong_count"] == 0
    assert sc["weak_count"] == 1
    assert len(sc["skill_failures"]) == 1
    assert "05_五|营业收入" in sc["skill_failures"][0]
    assert "退出码 1" in sc["skill_failures"][0]

    # 弱组：skill 失败 → pending_skill 仍 True（标记为"待用户重试"）
    weak_grp = [g for g in sc["groups"] if g["status"] == "weak"][0]
    assert weak_grp["pending_skill"] is True
    assert weak_grp["long_csv"] is None
    assert weak_grp["wide_csv"] is None
    assert "skill failed" in weak_grp["reason"] or "skill" in weak_grp["reason"]

    # 终态 done，但 error 字段记录了 skill 失败摘要
    db = db_session.SessionLocal()
    try:
        run = db.get(ReportRun, run_id)
        assert run.status == "done"
        assert run.error and "退出码 1" in run.error
    finally:
        db.close()


# ============================================================
# 4. empty：公司没 CSV
# ============================================================


def test_worker_empty_when_no_csvs(fresh_db: Path):
    db = db_session.SessionLocal()
    try:
        cid = _create_company(db, "空公司")
        run_id = _create_run(db, cid)
    finally:
        db.close()

    pipeline.run_table_merge_pipeline(run_id=run_id, company_id=cid, years=None)

    db = db_session.SessionLocal()
    try:
        run = db.get(ReportRun, run_id)
        assert run.status == "done"
        assert run.current_stage == 4
        assert run.error and "没有任何" in run.error
    finally:
        db.close()


# ============================================================
# 5. 公司不存在 → failed
# ============================================================


def test_worker_company_not_found_marks_failed(fresh_db: Path):
    db = db_session.SessionLocal()
    try:
        run = ReportRun(company_id=99999, year=None, template="table_merge", status="queued")
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
    finally:
        db.close()

    pipeline.run_table_merge_pipeline(run_id=run_id, company_id=99999, years=[2025])

    db = db_session.SessionLocal()
    try:
        run = db.get(ReportRun, run_id)
        assert run.status == "failed"
        assert "公司不存在" in (run.error or "")
    finally:
        db.close()


# ============================================================
# 6. force 清空旧产物
# ============================================================


def test_worker_force_clears_existing_outputs(fresh_db: Path):
    base = fresh_db / "report_data"
    company = "宁德时代"
    for y in (2024, 2025):
        _seed_csv(
            base, company, y, "05_五", "营业收入",
            headers=["项目", "金额"],
            rows=[["营收", "1000"], ["成本", "500"]],
        )

    db = db_session.SessionLocal()
    try:
        cid = _create_company(db, company)
    finally:
        db.close()

    db = db_session.SessionLocal()
    try:
        run_id_1 = _create_run(db, cid)
    finally:
        db.close()
    pipeline.run_table_merge_pipeline(run_id=run_id_1, company_id=cid, years=[2024, 2025])

    out = base / company / "md" / "research_file" / "table"
    stale = out / "stale.csv"
    stale.write_text("noise", encoding="utf-8")
    assert stale.exists()

    db = db_session.SessionLocal()
    try:
        run_id_2 = _create_run(db, cid)
    finally:
        db.close()
    pipeline.run_table_merge_pipeline(
        run_id=run_id_2, company_id=cid, years=[2024, 2025], force=True
    )

    assert not stale.exists()
    assert (out / f".merge_run_{run_id_2}.json").exists()
    assert not (out / f".merge_run_{run_id_1}.json").exists()

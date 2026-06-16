"""阶段 3.x 跨年度表格合并 router 测试。

策略：
- 用 ``client`` fixture（TestClient）走 HTTP；BackgroundTask 同步跑（TestClient 等所有 bg 完成）
- 铺 fixtures：先 ``/tables/extract`` 同款目录 → POST ``/tables/merge`` → 验证 202 + 终态
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List

import pytest


# ============================================================
# helpers
# ============================================================


def _wait_run_done(client, run_id: int, timeout: float = 30.0) -> dict:
    """轮询 GET /tasks/{run_id} 等终态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/tasks/{run_id}")
        if r.status_code == 200:
            body = r.json()
            if body["status"] in {"done", "failed"}:
                return body
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} 在 {timeout}s 内未结束")


def _make_table_info(title: str, data_rows: List[List[str]], headers: List[str], unit: str = "千元"):
    from app.services.md_table_parser import TableInfo, UnitInfo, YearMapping

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


def _seed_csv(base: Path, company: str, year: int, stem: str, title: str,
              headers: List[str], rows: List[List[str]]) -> Path:
    from app.services.tables_extract_service import write_table_csv

    per_year = base / company / "md" / "clean" / f"{company}{year}年年报" / "table" / stem
    per_year.mkdir(parents=True, exist_ok=True)
    p = per_year / f"{title}.csv"
    t = _make_table_info(title, rows, headers)
    write_table_csv(p, t, f"{stem}.md", 1, 1)
    return p


def _create_company(db, name: str) -> int:
    from app.models import Company

    c = db.query(Company).filter(Company.name == name).first()
    if c:
        return c.id
    c = Company(name=name)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c.id


# ============================================================
# 1. 端点 404：公司不存在
# ============================================================


def test_route_404_when_company_missing(client):
    r = client.post(
        "/companies/不存在公司/tables/merge",
        json={"years": [2024, 2025], "scope": "all", "force": False},
    )
    assert r.status_code == 404
    assert "公司不存在" in r.json()["detail"]


# ============================================================
# 2. 端点 202 + 强路径跑通
# ============================================================


def test_route_strong_path_returns_202_and_writes_outputs(client, tmp_env, monkeypatch):
    base = tmp_env / "report_data"
    company = "宁德时代"
    # 2 年同名同 schema → strong
    for y in (2024, 2025):
        _seed_csv(
            base, company, y, "05_五", "营业收入",
            headers=["项目", "金额"],
            rows=[["营收", "1000"], ["成本", "500"]],
        )

    from app.db import session as db_session

    db = db_session.SessionLocal()
    try:
        _create_company(db, company)
    finally:
        db.close()

    r = client.post(
        f"/companies/{company}/tables/merge",
        json={"years": [2024, 2025], "scope": "all", "force": False},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["company"] == company
    assert body["status"] == "queued"
    assert body["run_id"] > 0
    run_id = body["run_id"]

    # 等 BackgroundTask 跑完
    final = _wait_run_done(client, run_id)
    assert final["status"] == "done"
    assert final["current_stage"] == 4
    assert final["final_path"] is not None
    assert final["final_path"].endswith(f".merge_run_{run_id}.json")

    # sidecar 内容
    sidecar = base / company / "md" / "research_file" / "table" / f".merge_run_{run_id}.json"
    assert sidecar.exists()
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc["strong_count"] == 1
    assert sc["weak_count"] == 0
    assert sc["groups"][0]["status"] == "strong"
    assert (base / sc["groups"][0]["long_csv"]).exists()
    assert (base / sc["groups"][0]["wide_csv"]).exists()


# ============================================================
# 3. 端点 202 + 弱路径（mock skill 成功）
# ============================================================


def test_route_weak_skill_success(client, tmp_env, monkeypatch):
    base = tmp_env / "report_data"
    company = "宁德时代"
    for y, hdrs, rows in [
        (2024, ["项目", "金额", "占比"], [["营收", "1000", "60%"]]),
        (2025, ["科目", "营业收入", "成本占比"], [["营收", "1100", "65%"]]),
    ]:
        _seed_csv(base, company, y, "05_五", "营业收入", headers=hdrs, rows=rows)

    from app.db import session as db_session
    from app.workers import table_merge_pipeline as pipeline
    from app.services.claude_skill_runner import (
        ClaudeSkillError,
        SkillRunResult,
        _expected_table_merge_outputs,
    )
    from app.config import get_settings

    def fake_skill(skill, company, group_key, years, csv_paths, **kwargs):
        s = get_settings()
        long_p, wide_p = _expected_table_merge_outputs(
            company=company, group_key=group_key, settings=s
        )
        long_p.parent.mkdir(parents=True, exist_ok=True)
        long_p.write_text("# meta\nrow\n", encoding="utf-8-sig")
        wide_p.write_text("# meta\nsubject\n", encoding="utf-8-sig")
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
        _create_company(db, company)
    finally:
        db.close()

    r = client.post(
        f"/companies/{company}/tables/merge",
        json={"years": [2024, 2025], "scope": "all", "force": False},
    )
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    final = _wait_run_done(client, run_id)
    assert final["status"] == "done"

    sidecar = base / company / "md" / "research_file" / "table" / f".merge_run_{run_id}.json"
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc["strong_count"] == 0
    assert sc["weak_count"] == 1
    assert sc["skill_failures"] == []
    weak_grp = [g for g in sc["groups"] if g["status"] == "weak"][0]
    assert weak_grp["pending_skill"] is False
    assert weak_grp["long_csv"] is not None
    assert (base / weak_grp["long_csv"]).exists()


# ============================================================
# 4. 端点 202 + 弱路径（mock skill 失败）
# ============================================================


def test_route_weak_skill_failure_marks_skill_failures(client, tmp_env, monkeypatch):
    base = tmp_env / "report_data"
    company = "宁德时代"
    for y, hdrs, rows in [
        (2024, ["项目", "金额", "占比"], [["营收", "1000", "60%"]]),
        (2025, ["科目", "营业收入", "成本占比"], [["营收", "1100", "65%"]]),
    ]:
        _seed_csv(base, company, y, "05_五", "营业收入", headers=hdrs, rows=rows)

    from app.db import session as db_session
    from app.workers import table_merge_pipeline as pipeline
    from app.services.claude_skill_runner import ClaudeSkillError

    def fake_skill_fail(skill, company, group_key, years, csv_paths, **kwargs):
        raise ClaudeSkillError("claude CLI 退出码 1")

    monkeypatch.setattr(
        pipeline.claude_skill_runner, "run_skill_for_table_merge", fake_skill_fail
    )

    db = db_session.SessionLocal()
    try:
        _create_company(db, company)
    finally:
        db.close()

    r = client.post(
        f"/companies/{company}/tables/merge",
        json={"years": [2024, 2025], "scope": "all", "force": False},
    )
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    final = _wait_run_done(client, run_id)
    assert final["status"] == "done"  # 弱组失败不影响整 run

    sidecar = base / company / "md" / "research_file" / "table" / f".merge_run_{run_id}.json"
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc["weak_count"] == 1
    assert len(sc["skill_failures"]) == 1
    assert "退出码 1" in sc["skill_failures"][0]


# ============================================================
# 5. 端点 202 + force=True
# ============================================================


def test_route_force_clears_previous_outputs(client, tmp_env):
    base = tmp_env / "report_data"
    company = "宁德时代"
    for y in (2024, 2025):
        _seed_csv(
            base, company, y, "05_五", "营业收入",
            headers=["项目", "金额"],
            rows=[["营收", "1000"]],
        )

    from app.db import session as db_session

    db = db_session.SessionLocal()
    try:
        _create_company(db, company)
    finally:
        db.close()

    # 第一次跑
    r1 = client.post(
        f"/companies/{company}/tables/merge",
        json={"years": [2024, 2025], "force": False},
    )
    run_id_1 = r1.json()["run_id"]
    _wait_run_done(client, run_id_1)

    # 故意塞垃圾
    out = base / company / "md" / "research_file" / "table"
    stale = out / "stale.csv"
    stale.write_text("noise", encoding="utf-8")
    assert stale.exists()

    # 第二次 force=True
    r2 = client.post(
        f"/companies/{company}/tables/merge",
        json={"years": [2024, 2025], "force": True},
    )
    run_id_2 = r2.json()["run_id"]
    _wait_run_done(client, run_id_2)
    assert not stale.exists()
    assert (out / f".merge_run_{run_id_2}.json").exists()


# ============================================================
# 6. 端点 202 + 公司没 CSV → 200ms 内 done + empty
# ============================================================


def test_route_empty_company_dones_fast(client, tmp_env):
    from app.db import session as db_session

    db = db_session.SessionLocal()
    try:
        _create_company(db, "空公司")
    finally:
        db.close()

    r = client.post(
        "/companies/空公司/tables/merge",
        json={"years": None, "scope": "all", "force": False},
    )
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    final = _wait_run_done(client, run_id)
    assert final["status"] == "done"
    assert "没有任何" in (final["error"] or "")

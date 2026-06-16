"""公司 CRUD + 上传 + 去重 测试。"""
from __future__ import annotations


def test_create_company_with_auto_stock_code(client) -> None:
    r = client.post("/companies", json={"name": "宁德时代"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "宁德时代"
    assert body["stock_code"] == "300750"  # 从 mapping.json 自动来


def test_create_company_idempotent(client) -> None:
    r1 = client.post("/companies", json={"name": "宁德时代"})
    r2 = client.post("/companies", json={"name": "宁德时代"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


def test_create_company_unknown_code(client) -> None:
    r = client.post("/companies", json={"name": "未知公司XYZ"})
    assert r.status_code == 201
    assert r.json()["stock_code"] is None


def test_list_companies(client) -> None:
    client.post("/companies", json={"name": "宁德时代"})
    client.post("/companies", json={"name": "比亚迪"})
    r = client.get("/companies")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert "宁德时代" in names
    assert "比亚迪" in names


def test_get_company_detail(client) -> None:
    client.post("/companies", json={"name": "宁德时代"})
    r = client.get("/companies/宁德时代")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "宁德时代"
    assert "annual_reports" in body
    assert "report_runs" in body


def test_get_company_404(client) -> None:
    r = client.get("/companies/不存在的公司")
    assert r.status_code == 404


def test_upload_pdf_success(client, tmp_env) -> None:
    client.post("/companies", json={"name": "宁德时代"})
    pdf = tmp_env / "test_report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content 1")
    with pdf.open("rb") as f:
        r = client.post(
            "/companies/宁德时代/upload",
            files={"file": ("宁德时代2023年年度报告.pdf", f, "application/pdf")},
            data={"year": 2023},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["deduplicated"] is False
    assert body["report"]["year"] == 2023
    assert body["report"]["source"] == "manual_upload"
    assert body["report"]["parse_status"] == "pending"
    # 物理文件存在（pdf_path 是相对 REPORT_DATA_PATH 的路径，按 docs/artifacts.md 规范）
    saved = tmp_env / "report_data" / body["report"]["pdf_path"]
    assert saved.exists()


def test_upload_pdf_dedup_by_sha256(client, tmp_env) -> None:
    client.post("/companies", json={"name": "宁德时代"})
    pdf = tmp_env / "dup.pdf"
    pdf.write_bytes(b"%PDF-1.4 same content")
    # 第一次
    with pdf.open("rb") as f:
        r1 = client.post(
            "/companies/宁德时代/upload",
            files={"file": ("v1.pdf", f, "application/pdf")},
            data={"year": 2023},
        )
    assert r1.status_code == 201
    assert r1.json()["deduplicated"] is False
    # 第二次，文件相同
    with pdf.open("rb") as f:
        r2 = client.post(
            "/companies/宁德时代/upload",
            files={"file": ("v2.pdf", f, "application/pdf")},
            data={"year": 2023},
        )
    assert r2.status_code == 201
    assert r2.json()["deduplicated"] is True
    # ID 一致
    assert r1.json()["report"]["id"] == r2.json()["report"]["id"]


def test_upload_pdf_company_not_found(client, tmp_env) -> None:
    pdf = tmp_env / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    with pdf.open("rb") as f:
        r = client.post(
            "/companies/不存在/upload",
            files={"file": ("x.pdf", f, "application/pdf")},
            data={"year": 2023},
        )
    assert r.status_code == 404


def test_upload_pdf_rejects_non_pdf(client) -> None:
    client.post("/companies", json={"name": "宁德时代"})
    fake = b"not a pdf at all"
    r = client.post(
        "/companies/宁德时代/upload",
        files={"file": ("evil.exe", fake, "application/octet-stream")},
        data={"year": 2023},
    )
    assert r.status_code == 400


def test_list_annual_reports_by_company(client, tmp_env) -> None:
    client.post("/companies", json={"name": "宁德时代"})
    for y in [2023, 2024, 2025]:
        p = tmp_env / f"r{y}.pdf"
        p.write_bytes(f"%PDF-1.4 y{y}".encode())
        with p.open("rb") as f:
            client.post(
                "/companies/宁德时代/upload",
                files={"file": (f"r{y}.pdf", f, "application/pdf")},
                data={"year": y},
            )
    r = client.get("/companies/宁德时代/reports")
    assert r.status_code == 200
    years = [x["year"] for x in r.json()]
    assert years == [2025, 2024, 2023]  # desc

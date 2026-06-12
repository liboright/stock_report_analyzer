"""GET /companies/{name}/reports/{year}/files 解析产物文件树 测试。"""
from __future__ import annotations

from pathlib import Path


def test_list_files_404_unknown_company(client) -> None:
    r = client.get("/companies/不存在/reports/2023/files")
    assert r.status_code == 404


def test_list_files_empty_when_unparsed(client, tmp_env) -> None:
    client.post("/companies", json={"name": "宁德时代"})
    r = client.get("/companies/宁德时代/reports/2023/files")
    assert r.status_code == 200
    body = r.json()
    assert body == {"chapters": [], "section3": [], "research": []}


def test_list_files_populated(client, tmp_env) -> None:
    client.post("/companies", json={"name": "宁德时代"})
    from app.config import get_settings

    # 统一为 REPORT_DATA_PATH（按 docs/artifacts.md 规范）
    base = Path(get_settings().REPORT_DATA_PATH)

    # 章节：两个文件（按新结构 md/clean/.../by_section/）
    chap_dir = base / "宁德时代" / "md" / "clean" / "宁德时代2023年年报" / "by_section"
    chap_dir.mkdir(parents=True, exist_ok=True)
    (chap_dir / "01_第一节.md").write_text("# 第一节", encoding="utf-8")
    (chap_dir / "02_第二节.md").write_text("# 第二节", encoding="utf-8")

    # 第三节 H2 拆分（按新结构 md/clean/.../管理层讨论/，无 year 子目录）
    sec3_dir = base / "宁德时代" / "md" / "clean" / "宁德时代2023年年报" / "管理层讨论"
    sec3_dir.mkdir(parents=True, exist_ok=True)
    (sec3_dir / "1_概述.md").write_text("# 概述", encoding="utf-8")

    # 业务概况（按新结构 md/research_file/）
    research_dir = base / "宁德时代" / "md" / "research_file"
    research_dir.mkdir(parents=True, exist_ok=True)
    (research_dir / "宁德时代_业务概况.md").write_text("# 业务概况", encoding="utf-8")

    r = client.get("/companies/宁德时代/reports/2023/files")
    assert r.status_code == 200
    body = r.json()
    assert len(body["chapters"]) == 2
    assert body["chapters"][0] == {
        "section_num": "01",
        "title": "第一节",
        "path": "宁德时代/md/clean/宁德时代2023年年报/by_section/01_第一节.md",
        "subsections": [],
    }
    assert body["chapters"][1]["section_num"] == "02"
    assert body["chapters"][1]["title"] == "第二节"

    assert len(body["section3"]) == 1
    assert body["section3"][0] == {
        "title": "1_概述",
        "path": "宁德时代/md/clean/宁德时代2023年年报/管理层讨论/1_概述.md",
    }

    assert len(body["research"]) == 1
    assert body["research"][0]["title"] == "宁德时代_业务概况"


def test_static_mount_serves_file(client, tmp_env) -> None:
    from app.config import get_settings

    # 静态文件统一以 REPORT_DATA_PATH 为 base（/static/md/ 和 /static/raw/ 都映射到同根）
    base = Path(get_settings().REPORT_DATA_PATH)
    target = base / "smoke_test.md"
    target.write_text("hello md", encoding="utf-8")

    r = client.get("/static/md/smoke_test.md")
    assert r.status_code == 200
    assert r.text == "hello md"

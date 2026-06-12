"""测试公共 fixture：内存 SQLite + 隔离 .env + TestClient。

策略：
- 用 monkeypatch 改环境变量，使 Settings 指向 tmp_path
- 清空 lru_cache，让 get_settings() 重新读
- 重新建 engine + create_all
- 提供 client 工厂
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """改 .env 关键路径到 tmp_path，DB 用内存 SQLite 兼容。"""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    # 统一单棵树：只设 REPORT_DATA_PATH，旧的 RAW_BASE_PATH/REPORT_BASE_PATH 已删除
    monkeypatch.setenv("REPORT_DATA_PATH", str(tmp_path / "report_data"))
    # SCRIPT_PATH / DEEP_RESEARCH_PATH 保持真实路径：
    # split_section3.py 和 annual_report_reader 在那儿，必须能 import
    # （MinerU 解析器已内嵌到 app.services.mineru_parser，不再依赖外部）
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MAPPING_PATH", str(tmp_path / "mapping.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("MINERU_API_KEY", "")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")

    # 清 lru_cache
    from app.config import get_settings
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_env: Path) -> Iterator[TestClient]:
    from app.config import get_settings
    from app.db import session as db_session
    from app.main import app

    s = get_settings()
    s.ensure_runtime_dirs()
    db_session.engine.dispose()
    db_session.engine = db_session.create_engine(s.db_url, future=True, connect_args={"check_same_thread": False})
    db_session.SessionLocal = db_session.sessionmaker(bind=db_session.engine, autoflush=False, autocommit=False, future=True)
    db_session.init_db()

    # 写入 mapping.json
    (tmp_env / "mapping.json").write_text(
        '{"宁德时代":"300750","_comment":"test"}', encoding="utf-8"
    )

    with TestClient(app) as c:
        yield c

    db_session.engine.dispose()


def make_fake_pdf(path: Path, content: bytes = b"%PDF-1.4 fake") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path

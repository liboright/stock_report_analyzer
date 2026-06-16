"""SQLAlchemy 2.x 风格的 engine/session + 启动建表。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM model 的公共基类。"""


_settings = get_settings()
# check_same_thread=False 允许多线程（BackgroundTasks 同进程内调度）
engine = create_engine(
    _settings.db_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """启动时建表（幂等）+ 轻量迁移。"""
    # 导入 model 让 metadata 知道表
    from app.models import Company, AnnualReport, ReportRun, TaskEvent  # noqa: F401

    _settings.ensure_runtime_dirs()
    Base.metadata.create_all(bind=engine)
    _migrate(engine)


def _migrate(engine) -> None:
    """轻量迁移：给已存在的 annual_report 加新列（幂等）。

    新建表（测试/tmp_env）会被 create_all 一次建齐，不走 ALTER。
    已部署的 state.db 缺少新列时，逐一检测并 ALTER。
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "annual_report" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("annual_report")}
    new_cols = [
        ("parse_split_status", "VARCHAR(16)"),
        ("business_md_path", "VARCHAR(512)"),
        ("finance_md_path", "VARCHAR(512)"),
        ("extract_tables_status", "VARCHAR(16)"),
        ("tables_extracted_at", "DATETIME"),
        ("tables_dir_path", "VARCHAR(512)"),
    ]
    with engine.begin() as conn:
        for col, typedef in new_cols:
            if col not in existing:
                conn.execute(text(f"ALTER TABLE annual_report ADD COLUMN {col} {typedef}"))


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务级 session scope（workers 用）。"""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI Depends 注入器。"""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()

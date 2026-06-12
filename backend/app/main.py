"""FastAPI 入口。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import get_settings
from app.db.session import init_db
from app.routers import companies as companies_router
from app.routers import reports as reports_router
from app.routers import settings as settings_router
from app.routers import tasks as tasks_router
from app.workers import progress_bus


def _setup_logging() -> None:
    s = get_settings()
    s.ensure_runtime_dirs()
    log_file = s.LOG_DIR / "app.log"
    logging.basicConfig(
        level=getattr(logging, s.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    s = get_settings()
    s.ensure_runtime_dirs()
    s.inject_external_paths()
    init_db()
    # 把主事件循环告诉 progress_bus，跨线程 publish 时能调度回主循环
    progress_bus.set_loop(asyncio.get_running_loop())
    logging.getLogger(__name__).info("应用启动完成")
    yield
    logging.getLogger(__name__).info("应用关闭")


def _safe_resolve(base: Path, rel: str) -> Path:
    """把 rel 解析到 base 下的真实路径，防止越界。"""
    p = (base / rel).resolve()
    base_resolved = base.resolve()
    if base_resolved not in p.parents and p != base_resolved:
        raise HTTPException(status_code=404, detail="path out of base")
    return p


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Report Database Backend",
        description="A 股年报「搜索+解析+深度报告」Web 系统后端",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(companies_router.router)
    app.include_router(reports_router.router)
    app.include_router(tasks_router.router)
    app.include_router(settings_router.router)

    # 静态文件路由：每次请求读 settings（lifespan + 测试 fixture 都可生效）。
    # 两条路由都映射到同一个根 REPORT_DATA_PATH；前端用 /api/static/<kind>/<rel-path> 访问。
    @app.get("/static/md/{rel_path:path}")
    def serve_md(rel_path: str):
        s_now = get_settings()
        try:
            p = _safe_resolve(s_now.REPORT_DATA_PATH, rel_path)
        except HTTPException:
            raise
        if not p.is_file():
            raise HTTPException(status_code=404, detail=f"not found: {rel_path}")
        return FileResponse(str(p), media_type="text/markdown; charset=utf-8")

    @app.get("/static/raw/{rel_path:path}")
    def serve_raw(rel_path: str):
        s_now = get_settings()
        try:
            p = _safe_resolve(s_now.REPORT_DATA_PATH, rel_path)
        except HTTPException:
            raise
        if not p.is_file():
            raise HTTPException(status_code=404, detail=f"not found: {rel_path}")
        return FileResponse(str(p))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": app.version}

    return app


app = create_app()
